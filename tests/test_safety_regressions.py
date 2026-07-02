import inspect
import io
import os
import tempfile
import threading
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
            os.makedirs(source)
            os.makedirs(target)
            with open(os.path.join(source, "remaining.bin"), "wb") as stream:
                stream.write(b"remaining")

            ok, _message, plan = main.analyze_space_saving_plan(source, destination, "junction")

            self.assertTrue(ok)
            self.assertEqual(plan["target_path"], target)
            self.assertTrue(any("断点续迁" in warning for warning in plan["warnings"]))

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


if __name__ == "__main__":
    unittest.main()
