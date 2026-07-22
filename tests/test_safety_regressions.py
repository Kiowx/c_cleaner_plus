import inspect
import hashlib
import io
import json
import os
import tempfile
import threading
import types
import unittest
from unittest import mock

import main


class _FakeResponse(io.BytesIO):
    def __init__(self, payload, headers=None):
        super().__init__(payload)
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


class _FakeRegistryKey:
    def __init__(self, kind):
        self.kind = kind

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class SafetyRegressionTests(unittest.TestCase):
    def test_rule_pack_filename_rejects_path_escape(self):
        invalid_names = [
            r"..\main.py",
            "../main.py",
            r"C:\tmp\payload.json",
            "/tmp/payload.json",
            "payload.txt",
            "..",
        ]
        for name in invalid_names:
            with self.subTest(name=name):
                self.assertEqual(main.normalize_rule_pack_filename(name), "")
                self.assertIsNone(main._normalize_rule_store_item({
                    "title": "unsafe",
                    "filename": name,
                }))

    def test_rule_pack_download_is_bounded_validated_and_contained(self):
        payload = b'{"rules": []}'
        with tempfile.TemporaryDirectory() as temp_dir:
            response = _FakeResponse(payload, {"Content-Length": str(len(payload))})
            with mock.patch.object(main.urllib.request, "urlopen", return_value=response):
                path = main.download_rule_pack("safe_rules.json", base_dir=temp_dir)

            self.assertEqual(os.path.dirname(path), os.path.abspath(temp_dir))
            self.assertEqual(os.path.basename(path), "safe_rules.json")
            with open(path, "rb") as stream:
                self.assertEqual(stream.read(), payload)

    def test_rule_pack_invalid_json_is_not_persisted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            response = _FakeResponse(b"not-json")
            with mock.patch.object(main.urllib.request, "urlopen", return_value=response):
                with self.assertRaises(ValueError):
                    main.download_rule_pack("invalid.json", base_dir=temp_dir)
            self.assertFalse(os.path.exists(os.path.join(temp_dir, "invalid.json")))

    def test_rule_pack_size_limit_is_enforced_before_write(self):
        oversized = b" " * (main.RULE_PACK_MAX_BYTES + 1)
        with tempfile.TemporaryDirectory() as temp_dir:
            response = _FakeResponse(oversized)
            with mock.patch.object(main.urllib.request, "urlopen", return_value=response):
                with self.assertRaises(ValueError):
                    main.download_rule_pack("oversized.json", base_dir=temp_dir)
            self.assertFalse(os.path.exists(os.path.join(temp_dir, "oversized.json")))

    def test_sensitive_descendants_are_blocked_but_vetted_cache_children_are_allowed(self):
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        local_app_data = os.environ.get("LOCALAPPDATA", r"C:\Users\Test\AppData\Local")

        self.assertTrue(main.is_protected_system_path(os.path.join(system_root, "WinSxS", "payload.dll")))
        self.assertTrue(main.is_protected_system_path(os.path.join(program_files, "Example", "app.exe")))
        self.assertTrue(main.is_protected_system_path(os.path.join(system_root, "Temp")))
        self.assertFalse(main.is_protected_system_path(os.path.join(system_root, "Temp", "cleanup.tmp")))
        self.assertFalse(main.is_protected_system_path(os.path.join(local_app_data, "Temp", "cleanup.tmp")))

    def test_scheduled_registry_check_never_deletes(self):
        root_key = _FakeRegistryKey("root")
        app_key = _FakeRegistryKey("app")

        def open_key(parent, subkey):
            if isinstance(parent, _FakeRegistryKey):
                return app_key
            return root_key

        def query_info_key(key):
            return (1, 0, 0) if key.kind == "root" else (0, 0, 0)

        def enum_key(key, index):
            self.assertEqual(index, 0)
            return "MissingApp"

        def query_value_ex(key, name):
            self.assertEqual(name, "InstallLocation")
            return (r"Z:\definitely-missing-c-cleaner-plus", main.winreg.REG_SZ)

        logs = []
        with (
            mock.patch.object(main.winreg, "OpenKey", side_effect=open_key),
            mock.patch.object(main.winreg, "QueryInfoKey", side_effect=query_info_key),
            mock.patch.object(main.winreg, "EnumKey", side_effect=enum_key),
            mock.patch.object(main.winreg, "QueryValueEx", side_effect=query_value_ex),
            mock.patch.object(main, "force_delete_registry") as delete_mock,
        ):
            main._run_scheduled_registry_cleanup(logs.append)

        delete_mock.assert_not_called()
        self.assertTrue(any("仅报告" in line for line in logs))
        self.assertTrue(any("删除 0 项" in line for line in logs))

    def test_leftover_cleanup_no_longer_starts_a_second_worker(self):
        source = inspect.getsource(main.UninstallPage._trigger_leftover_scan)
        self.assertNotIn("threading.Thread", source)
        self.assertNotIn("stop.clear", source)

        standard_source = inspect.getsource(main.UninstallPage._std_uninstall_w)
        self.assertIn("emit_done=False", standard_source)

    def test_background_scan_methods_use_snapshotted_arguments(self):
        clean_source = inspect.getsource(main.CleanPage._cln_w)
        big_source = inspect.getsource(main.BigFilePage._scan_w)

        for forbidden in ("self.chk_perm", "self.chk_rst"):
            self.assertNotIn(forbidden, clean_source)
        for forbidden in ("self.sp_mb", "self.sp_mx", "self.drive_sel", "self.chk_skip_special"):
            self.assertNotIn(forbidden, big_source)

    def test_link_plan_allows_existing_directory_target_for_resume(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = os.path.join(temp_dir, "uv-cache")
            destination = os.path.join(temp_dir, "migrated")
            target = os.path.join(destination, "uv-cache")
            journal = os.path.join(temp_dir, "migration-journal.json")
            os.makedirs(source)
            os.makedirs(target)
            with open(os.path.join(source, "remaining.bin"), "wb") as stream:
                stream.write(b"remaining")

            with mock.patch.object(main, "_toolbox_migration_journal_path", return_value=journal):
                self.assertTrue(main.set_migration_record(source, target, "junction", "moving"))
                ok, _message, plan = main.analyze_space_saving_plan(source, destination, "junction")

            self.assertTrue(ok)
            self.assertEqual(plan["target_path"], target)
            self.assertTrue(any("断点续迁" in warning for warning in plan["warnings"]))

    def test_resumed_directory_uses_dynamic_space_checks_instead_of_restarting(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = os.path.join(temp_dir, "uv-cache")
            destination = os.path.join(temp_dir, "migrated")
            target = os.path.join(destination, "uv-cache")
            journal = os.path.join(temp_dir, "migration-journal.json")
            os.makedirs(source)
            os.makedirs(target)
            with open(os.path.join(source, "large-package.bin"), "wb") as stream:
                stream.write(b"remaining-data")
            no_space = types.SimpleNamespace(free=0)

            with (
                mock.patch.object(main, "_toolbox_migration_journal_path", return_value=journal),
                mock.patch.object(main, "_paths_share_volume", return_value=False),
                mock.patch.object(main.shutil, "disk_usage", return_value=no_space),
            ):
                self.assertTrue(
                    main.set_migration_record(source, target, "junction", "moving")
                )
                ok, message, plan = main.analyze_space_saving_plan(
                    source,
                    destination,
                    "junction",
                )
                self.assertTrue(ok, message)
                self.assertTrue(plan["resume_in_progress"])
                self.assertTrue(any("保留进度" in item for item in plan["warnings"]))

                with mock.patch.object(
                    main,
                    "_move_directory_incremental",
                    return_value=(False, "resume-sentinel"),
                ):
                    ok, message, _target = main.create_space_saving_link(
                        source,
                        destination,
                        "junction",
                        analysis_plan=plan,
                    )

            self.assertFalse(ok)
            self.assertEqual(message, "resume-sentinel")
            self.assertTrue(os.path.exists(source))

    def test_unmarked_existing_target_is_not_treated_as_migration(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = os.path.join(temp_dir, "cache")
            destination = os.path.join(temp_dir, "migrated")
            target = os.path.join(destination, "cache")
            journal = os.path.join(temp_dir, "migration-journal.json")
            os.makedirs(target)
            with open(os.path.join(target, "unrelated.bin"), "wb") as stream:
                stream.write(b"unrelated")

            with (
                mock.patch.object(main, "_toolbox_migration_journal_path", return_value=journal),
                mock.patch.object(main, "_symlink_mode_available", return_value=(True, "ok")),
                mock.patch.object(main.os, "symlink") as symlink_mock,
            ):
                ok, message, _target = main.create_space_saving_link(source, destination, "symlink")

            self.assertFalse(ok)
            self.assertIn("没有匹配的迁移断点记录", message)
            symlink_mock.assert_not_called()

    def test_marked_completed_migration_can_recreate_link(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = os.path.join(temp_dir, "cache")
            destination = os.path.join(temp_dir, "migrated")
            target = os.path.join(destination, "cache")
            journal = os.path.join(temp_dir, "migration-journal.json")
            os.makedirs(target)

            with mock.patch.object(main, "_toolbox_migration_journal_path", return_value=journal):
                self.assertTrue(main.set_migration_record(source, target, "symlink", "moved"))
                with (
                    mock.patch.object(main, "_symlink_mode_available", return_value=(True, "ok")),
                    mock.patch.object(main.os, "symlink") as symlink_mock,
                    mock.patch.object(main, "append_link_history", return_value=True),
                ):
                    ok, message, result_target = main.create_space_saving_link(source, destination, "symlink")

                self.assertFalse(main.get_migration_record(source, target, "symlink"))

            self.assertTrue(ok, message)
            self.assertEqual(result_target, target)
            symlink_mock.assert_called_once_with(target, source, target_is_directory=True)

    def test_analysis_result_avoids_second_directory_size_scan(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = os.path.join(temp_dir, "cache")
            destination = os.path.join(temp_dir, "migrated")
            target = os.path.join(destination, "cache")
            journal = os.path.join(temp_dir, "migration-journal.json")
            os.makedirs(source)
            os.makedirs(destination)
            original_dir_size = main.dir_size_detailed

            def finish_move(src, target_path, **_kwargs):
                os.makedirs(target_path, exist_ok=True)
                os.rmdir(src)
                return True, "ok"

            completed = types.SimpleNamespace(returncode=0, stdout="", stderr="")
            with (
                mock.patch.object(main, "_toolbox_migration_journal_path", return_value=journal),
                mock.patch.object(main, "dir_size_detailed", wraps=original_dir_size) as size_mock,
            ):
                ok, message, plan = main.analyze_space_saving_plan(source, destination, "junction")
                self.assertTrue(ok, message)
                with (
                    mock.patch.object(main, "_move_directory_incremental", side_effect=finish_move),
                    mock.patch.object(main.subprocess, "run", return_value=completed),
                    mock.patch.object(main, "append_link_history", return_value=True),
                ):
                    ok, message, result_target = main.create_space_saving_link(
                        source,
                        destination,
                        "junction",
                        analysis_plan=plan,
                    )

            self.assertTrue(ok, message)
            self.assertEqual(result_target, target)
            self.assertEqual(size_mock.call_count, 1)

    def test_incremental_directory_move_can_pause_and_resume(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = os.path.join(temp_dir, "cache")
            target = os.path.join(temp_dir, "target", "cache")
            os.makedirs(os.path.join(source, "nested"))
            for index in range(3):
                with open(os.path.join(source, "nested", f"item{index}.bin"), "wb") as stream:
                    stream.write(f"payload-{index}".encode("utf-8"))

            stop = threading.Event()

            def stop_after_first_progress(_value, _status):
                stop.set()

            ok, message = main._move_directory_incremental(
                source,
                target,
                stop_event=stop,
                progress_fn=stop_after_first_progress,
            )

            self.assertFalse(ok)
            self.assertIn("下次可继续", message)
            self.assertTrue(os.path.isdir(source))
            self.assertTrue(os.path.isdir(target))

            stop.clear()
            ok, message = main._move_directory_incremental(source, target, stop_event=stop)

            self.assertTrue(ok, message)
            self.assertFalse(os.path.exists(source))
            for index in range(3):
                self.assertTrue(os.path.exists(os.path.join(target, "nested", f"item{index}.bin")))

    def test_incremental_directory_move_stops_on_target_conflict(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = os.path.join(temp_dir, "cache")
            target = os.path.join(temp_dir, "target", "cache")
            os.makedirs(source)
            os.makedirs(target)
            source_file = os.path.join(source, "item.bin")
            target_file = os.path.join(target, "item.bin")
            with open(source_file, "wb") as stream:
                stream.write(b"source")
            with open(target_file, "wb") as stream:
                stream.write(b"target")

            ok, message = main._move_directory_incremental(source, target)

            self.assertFalse(ok)
            self.assertIn("同名内容", message)
            with open(source_file, "rb") as stream:
                self.assertEqual(stream.read(), b"source")
            with open(target_file, "rb") as stream:
                self.assertEqual(stream.read(), b"target")

    def test_duplicate_candidate_is_revalidated_before_delete(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            reference = os.path.join(temp_dir, "reference.bin")
            candidate = os.path.join(temp_dir, "candidate.bin")
            payload = b"same-content"
            for path in (reference, candidate):
                with open(path, "wb") as stream:
                    stream.write(payload)
            expectation = {
                "reference": reference,
                "size": len(payload),
                "digest": hashlib.sha256(payload).hexdigest(),
            }

            ok, message = main.validate_duplicate_deletion_candidate(candidate, expectation)
            self.assertTrue(ok, message)

            with open(candidate, "wb") as stream:
                stream.write(b"diff-content")
            ok, message = main.validate_duplicate_deletion_candidate(candidate, expectation)
            self.assertFalse(ok)
            self.assertIn("不再相同", message)

    def test_duplicate_delete_worker_skips_failed_revalidation(self):
        class _Emitter:
            def __init__(self):
                self.values = []

            def emit(self, *values):
                self.values.append(values)

        path = r"D:\changed-duplicate.bin"
        fake_page = types.SimpleNamespace(
            stop=threading.Event(),
            sig=types.SimpleNamespace(
                more_log=_Emitter(),
                more_prog=_Emitter(),
                more_done=_Emitter(),
            ),
        )
        expectations = {main._normalize_safety_path(path): {"reference": r"D:\reference.bin"}}

        with (
            mock.patch.object(
                main,
                "validate_duplicate_deletion_candidate",
                return_value=(False, "changed"),
            ) as validate_mock,
            mock.patch.object(main, "delete_path") as delete_mock,
        ):
            main.MoreCleanPage._del_files_w(
                fake_page,
                [path],
                True,
                duplicate_expectations=expectations,
            )

        validate_mock.assert_called_once()
        delete_mock.assert_not_called()

    def test_undo_refuses_to_replace_a_regular_source_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = os.path.join(temp_dir, "source.bin")
            target = os.path.join(temp_dir, "target.bin")
            journal = os.path.join(temp_dir, "migration-journal.json")
            with open(source, "wb") as stream:
                stream.write(b"new-user-data")
            with open(target, "wb") as stream:
                stream.write(b"old-migrated-data")

            with mock.patch.object(main, "_toolbox_migration_journal_path", return_value=journal):
                ok, message = main.undo_link_entry(source, target, "symlink")

            self.assertFalse(ok)
            self.assertIn("已不是迁移链接", message)
            with open(source, "rb") as stream:
                self.assertEqual(stream.read(), b"new-user-data")
            with open(target, "rb") as stream:
                self.assertEqual(stream.read(), b"old-migrated-data")

    def test_undo_can_resume_after_link_was_already_removed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = os.path.join(temp_dir, "restored-cache")
            target = os.path.join(temp_dir, "migrated-cache")
            journal = os.path.join(temp_dir, "migration-journal.json")
            os.makedirs(target)
            payload_path = os.path.join(target, "payload.bin")
            with open(payload_path, "wb") as stream:
                stream.write(b"payload")

            with mock.patch.object(main, "_toolbox_migration_journal_path", return_value=journal):
                self.assertTrue(
                    main.set_migration_record(
                        source,
                        target,
                        "junction",
                        "undoing",
                        "directory",
                    )
                )
                ok, message = main.undo_link_entry(source, target, "junction")
                record = main.get_migration_record(source, target, "junction")

            self.assertTrue(ok, message)
            self.assertFalse(record)
            self.assertFalse(os.path.exists(target))
            with open(os.path.join(source, "payload.bin"), "rb") as stream:
                self.assertEqual(stream.read(), b"payload")

    def test_missing_undo_target_requires_a_completed_checkpoint(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = os.path.join(temp_dir, "restored-cache")
            target = os.path.join(temp_dir, "missing-migrated-cache")
            journal = os.path.join(temp_dir, "migration-journal.json")
            os.makedirs(source)

            with mock.patch.object(main, "_toolbox_migration_journal_path", return_value=journal):
                self.assertTrue(
                    main.set_migration_record(
                        source,
                        target,
                        "junction",
                        "undoing",
                        "directory",
                    )
                )
                ok, message = main.undo_link_entry(source, target, "junction")
                self.assertFalse(ok)
                self.assertIn("尚未标记完成", message)
                self.assertEqual(
                    main.get_migration_record(source, target, "junction").get("state"),
                    "undoing",
                )

                self.assertTrue(
                    main.set_migration_record(
                        source,
                        target,
                        "junction",
                        "undo_moved",
                        "directory",
                    )
                )
                ok, message = main.undo_link_entry(source, target, "junction")
                self.assertTrue(ok, message)
                self.assertFalse(main.get_migration_record(source, target, "junction"))

    def test_cross_volume_file_copy_resumes_from_verified_partial(self):
        class _StopAfterFirstChunk:
            def __init__(self):
                self.calls = 0

            def is_set(self):
                self.calls += 1
                return self.calls >= 3

        with tempfile.TemporaryDirectory() as temp_dir:
            source = os.path.join(temp_dir, "source.bin")
            target = os.path.join(temp_dir, "target", "source.bin")
            payload = b"A" * (main.MIGRATION_COPY_CHUNK_SIZE + 1024)
            with open(source, "wb") as stream:
                stream.write(payload)

            with mock.patch.object(main, "_paths_share_volume", return_value=False):
                ok, message, _size = main._move_regular_file_resumable(
                    source,
                    target,
                    stop_event=_StopAfterFirstChunk(),
                )
                self.assertFalse(ok)
                self.assertIn("下次可从半成品继续", message)
                partial = main._migration_partial_path(target)
                self.assertEqual(os.path.getsize(partial), main.MIGRATION_COPY_CHUNK_SIZE)

                ok, message, moved_size = main._move_regular_file_resumable(
                    source,
                    target,
                    stop_event=threading.Event(),
                )

            self.assertTrue(ok, message)
            self.assertEqual(moved_size, len(payload))
            self.assertFalse(os.path.exists(source))
            self.assertFalse(os.path.exists(partial))
            with open(target, "rb") as stream:
                self.assertEqual(stream.read(), payload)

    def test_dynamic_copy_space_check_preserves_source(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = os.path.join(temp_dir, "source.bin")
            target = os.path.join(temp_dir, "target", "source.bin")
            with open(source, "wb") as stream:
                stream.write(b"A" * 4096)
            no_space = types.SimpleNamespace(free=0)

            with (
                mock.patch.object(main, "_paths_share_volume", return_value=False),
                mock.patch.object(main.shutil, "disk_usage", return_value=no_space),
            ):
                ok, message, _size = main._move_regular_file_resumable(source, target)

            self.assertFalse(ok)
            self.assertIn("空间不足", message)
            self.assertTrue(os.path.exists(source))
            self.assertFalse(os.path.exists(target))

    def test_directory_size_reports_incomplete_scan(self):
        with mock.patch.object(main.os, "scandir", side_effect=PermissionError("denied")):
            result = main.dir_size_detailed(r"C:\\blocked")

        self.assertFalse(result.complete)
        self.assertFalse(result.cancelled)
        self.assertEqual(result.errors, 1)
        self.assertEqual(result.size, 0)

    def test_duplicate_reference_is_hashed_once_per_group(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            reference = os.path.join(temp_dir, "reference.bin")
            candidates = [
                os.path.join(temp_dir, "candidate-1.bin"),
                os.path.join(temp_dir, "candidate-2.bin"),
            ]
            payload = b"same-content"
            for path in [reference, *candidates]:
                with open(path, "wb") as stream:
                    stream.write(payload)
            expectation = {
                "reference": reference,
                "size": len(payload),
                "digest": hashlib.sha256(payload).hexdigest(),
            }
            cache = {}

            with mock.patch.object(
                main,
                "_stable_file_digest",
                wraps=main._stable_file_digest,
            ) as digest_mock:
                for candidate in candidates:
                    ok, message = main.validate_duplicate_deletion_candidate(
                        candidate,
                        expectation,
                        reference_cache=cache,
                    )
                    self.assertTrue(ok, message)

            reference_calls = [
                call for call in digest_mock.call_args_list
                if os.path.normcase(call.args[0]) == os.path.normcase(reference)
            ]
            self.assertEqual(len(reference_calls), 1)

    def test_toolbox_worker_rejects_overlap_without_clearing_cancel(self):
        class _Emitter:
            def emit(self, *_args):
                return None

        started = threading.Event()
        release = threading.Event()
        fake_page = types.SimpleNamespace(
            _task_lock=threading.Lock(),
            _active_toolbox_worker=None,
            _active_toolbox_task_name="",
            stop_event=threading.Event(),
            toolboxTaskStateChanged=_Emitter(),
            main_win=None,
        )

        def long_task():
            started.set()
            release.wait(5)

        first = main.ToolboxPage._start_toolbox_worker(
            fake_page,
            "first",
            long_task,
            notify=False,
        )
        self.assertIsNotNone(first)
        self.assertTrue(started.wait(2))
        fake_page.stop_event.set()

        second = main.ToolboxPage._start_toolbox_worker(
            fake_page,
            "second",
            lambda: None,
            notify=False,
        )
        self.assertIsNone(second)
        self.assertTrue(fake_page.stop_event.is_set())

        release.set()
        first.join(2)
        self.assertFalse(first.is_alive())

    def test_stale_toolbox_completion_cannot_unlock_a_new_task(self):
        class _Button:
            def __init__(self):
                self.enabled = False

            def setEnabled(self, value):
                self.enabled = bool(value)

        fake_page = types.SimpleNamespace(
            _toolbox_task_generation=2,
            _active_toolbox_task_name="second",
            btn_back=_Button(),
        )
        main.ToolboxPage._set_toolbox_task_state(
            fake_page,
            False,
            "first",
            1,
        )

        self.assertFalse(fake_page.btn_back.enabled)
        self.assertEqual(fake_page._active_toolbox_task_name, "second")

    def test_partial_commit_never_overwrites_a_new_target(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = os.path.join(temp_dir, "source.bin")
            target = os.path.join(temp_dir, "target", "source.bin")
            payload = b"migration-payload"
            user_payload = b"new-target-data"
            with open(source, "wb") as stream:
                stream.write(payload)

            def occupy_target(*_args, **_kwargs):
                with open(target, "wb") as stream:
                    stream.write(user_payload)

            with (
                mock.patch.object(main, "_paths_share_volume", return_value=False),
                mock.patch.object(main.shutil, "copystat", side_effect=occupy_target),
            ):
                ok, message, _size = main._move_regular_file_resumable(source, target)

            self.assertFalse(ok)
            self.assertIn("目标文件在提交前被其他程序占用", message)
            self.assertTrue(os.path.exists(source))
            self.assertTrue(os.path.exists(main._migration_partial_path(target)))
            with open(target, "rb") as stream:
                self.assertEqual(stream.read(), user_payload)

    def test_partial_resume_stops_if_source_changes_during_prefix_check(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = os.path.join(temp_dir, "source.bin")
            target = os.path.join(temp_dir, "target", "source.bin")
            payload = b"A" * 8192
            with open(source, "wb") as stream:
                stream.write(payload)
            os.makedirs(os.path.dirname(target))
            partial = main._migration_partial_path(target)
            with open(partial, "wb") as stream:
                stream.write(payload[:1024])

            def mutate_source(*_args, **_kwargs):
                before = os.stat(source, follow_symlinks=False)
                with open(source, "r+b") as stream:
                    stream.seek(0)
                    stream.write(b"B")
                os.utime(
                    source,
                    ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000),
                )
                return True, ""

            with (
                mock.patch.object(main, "_paths_share_volume", return_value=False),
                mock.patch.object(main, "_file_prefix_matches", side_effect=mutate_source),
            ):
                ok, message, _size = main._move_regular_file_resumable(source, target)

            self.assertFalse(ok)
            self.assertIn("半成品校验过程中发生变化", message)
            self.assertTrue(os.path.exists(source))
            self.assertTrue(os.path.exists(partial))
            self.assertFalse(os.path.exists(target))

    def test_completed_partial_can_commit_without_extra_free_space(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target_parent = os.path.join(temp_dir, "target")
            os.makedirs(target_parent)
            no_space = types.SimpleNamespace(free=0)
            with mock.patch.object(main.shutil, "disk_usage", return_value=no_space):
                enough, free = main._copy_space_available(target_parent, 0)

            self.assertTrue(enough)
            self.assertEqual(free, 0)

    def test_cancelled_undo_does_not_touch_the_journal(self):
        stop = threading.Event()
        stop.set()
        with mock.patch.object(main, "get_migration_record") as get_record:
            ok, message = main.undo_link_entry(
                r"C:\cache",
                r"D:\cache",
                "junction",
                stop_event=stop,
            )

        self.assertFalse(ok)
        self.assertEqual(message, "已取消")
        get_record.assert_not_called()

    def test_shutdown_save_completes_synchronously(self):
        class _CleanPage:
            def _sync(self):
                return None

        with tempfile.TemporaryDirectory() as temp_dir:
            custom_path = os.path.join(temp_dir, "custom.json")
            config_path = os.path.join(temp_dir, "config.json")
            fake_window = types.SimpleNamespace(
                global_settings={"auto_save": True},
                pg_clean=_CleanPage(),
                _targets_lock=threading.Lock(),
                targets=[],
                custom_rules_path=custom_path,
                config_path=config_path,
            )

            main.MainWindow._save_before_exit(fake_window)

            with open(custom_path, "r", encoding="utf-8") as stream:
                self.assertEqual(json.load(stream), [])
            with open(config_path, "r", encoding="utf-8") as stream:
                self.assertIsInstance(json.load(stream), dict)

    def test_runtime_translation_cache_is_invalidated_with_pack(self):
        host = types.SimpleNamespace(language_pack={"测试": "First"}, tr_text=lambda text: text)
        self.assertEqual(main._runtime_tr(host, "测试"), "First")
        host.language_pack = {"测试": "Second"}
        self.assertEqual(main._runtime_tr(host, "测试"), "Second")

    def test_english_pack_contains_new_safety_messages(self):
        with open(main.bundled_language_file("en_us.json"), "r", encoding="utf-8") as stream:
            payload = json.load(stream)
            pack = main._normalize_language_pack(payload)
        with open(main.bundled_language_file("manifest.json"), "r", encoding="utf-8") as stream:
            manifest = json.load(stream)
        required = {
            "开始/继续迁移并创建链接",
            "源路径已经是链接，无需重复迁移",
            "源路径不存在，且没有匹配的迁移断点记录",
            "无法保存迁移断点记录，已停止迁移以避免无法安全续传",
            "无法保存撤销断点记录，已停止以避免无法安全恢复",
            "源链接已不再指向迁移目标，已拒绝删除",
            "撤销断点记录与已恢复路径类型不一致",
            "源文件在半成品校验过程中发生变化，已保留源文件",
            "目标文件在提交前被其他程序占用",
            "当前空间可能不足以一次完成，断点续迁会按文件动态检查并保留进度",
            "迁移目标不存在，且撤销记录尚未标记完成，已保留断点供人工确认",
            "重复文件复核",
        }
        self.assertTrue(required.issubset(pack))
        self.assertEqual(payload["version"], manifest["languages"]["en_us"]["version"])


if __name__ == "__main__":
    unittest.main()
