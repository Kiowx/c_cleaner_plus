import json
import os
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cleaner_core as core
import main
from maintenance_ui import MaintenancePage, PreviewDialog, RowsModel, ReviewPackDialog


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.previous_store = core.current_store()
        self.addCleanup(self.restore_store)
        self.store = core.configure(str(self.root / "config"))
        self.data = self.root / "data"
        self.data.mkdir()

    def restore_store(self):
        core._store = self.previous_store
        core._policy_cache = None
        core._policy_cache_at = 0

    def file(self, relative, data=b"content", age=0):
        path = self.data / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        if age:
            timestamp = time.time() - age * 86400
            os.utime(path, (timestamp, timestamp))
        return path

    def rule(self, kind="dir", path=None, pattern=""):
        return ("Test", str(path or self.data), kind, True, "test", True, pattern)

    def delete(self, path, permanent, log):
        return main.delete_path(path, permanent, log)

    def test_preview_and_execution_respect_unchecked_files_and_new_contents(self):
        selected = self.file("nested/remove.log")
        retained = self.file("nested/keep.log")
        plan = core.build_plan([self.rule()])
        for item in plan["items"]:
            if core.canonical(item["path"]) == core.canonical(str(retained)):
                item["selected"] = False
        # Sorting the preview must not cause parent directory deletion first.
        plan["items"].reverse()
        added = self.file("nested/new.log")
        result = core.execute_plan(plan, self.delete, True)
        self.assertFalse(selected.exists())
        self.assertTrue(retained.exists())
        self.assertTrue(added.exists())
        self.assertEqual(result["success"], 1)
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(len(self.store.history()), 1)
        self.assertEqual(self.store.history()[0]["state"], "finished")

    def test_modified_file_is_revalidated_and_preserved(self):
        path = self.file("change.log")
        plan = core.build_plan([self.rule()])
        path.write_bytes(b"new content changed")
        result = core.execute_plan(plan, self.delete, True)
        self.assertTrue(path.exists())
        self.assertEqual(result["skipped"], 1)
        self.assertIn("变化", result["items"][0]["reason"])

    def test_exclusion_added_after_preview_blocks_execution(self):
        path = self.file("keep.log")
        plan = core.build_plan([self.rule()])
        self.store.set("exclusions", [str(path)])
        result = core.execute_plan(plan, self.delete, True)
        self.assertTrue(path.exists())
        self.assertEqual(result["skipped"], 1)

    def test_wildcard_protects_parent_directory_deletion(self):
        protected = self.file("project/source.psd")
        self.file("project/remove.tmp")
        self.store.set("exclusions", ["*.psd"])
        log = []
        self.assertFalse(main.delete_path(str(protected.parent), True, log.append))
        self.assertTrue(protected.exists())
        self.assertTrue(log)

    def test_exclusion_directory_pattern_protects_descendants(self):
        path = self.file("project/.git/objects/file")
        policy = core.Policy([r"*\.git"])
        self.assertTrue(policy.excluded(str(path)))
        self.assertFalse(policy.excluded(str(self.data / "project" / "file")))

    def test_age_size_and_keep_latest_conditions_are_combined(self):
        self.file("new.log", b"x" * 100, age=1)
        old = self.file("old.log", b"x" * 100, age=40)
        self.file("tiny.log", b"x", age=60)
        rule = self.rule()
        self.store.set("conditions", {core.rule_id(rule): dict(min_age_days=30, min_size_bytes=10, keep_latest=1)})
        plan = core.build_plan([rule])
        self.assertEqual([core.canonical(item["path"]) for item in plan["items"]], [core.canonical(str(old))])

    def test_plan_limit_and_cancelled_scans_cannot_execute(self):
        for i in range(4):
            self.file(f"{i}.log")
        plan = core.build_plan([self.rule()], max_items=2)
        self.assertFalse(plan["complete"])
        with self.assertRaises(ValueError):
            core.execute_plan(plan, self.delete, True)
        stop = threading.Event()
        stop.set()
        self.assertFalse(core.build_plan([self.rule()], stop)["complete"])

    def test_cancelled_execution_is_recorded_without_deletion(self):
        path = self.file("preserve.log")
        plan = core.build_plan([self.rule()])
        stop = threading.Event()
        stop.set()
        result = core.execute_plan(plan, self.delete, True, stop)
        self.assertTrue(result["cancelled"])
        self.assertTrue(path.exists())
        self.assertEqual(self.store.history()[0]["state"], "cancelled")

    def test_hardlink_aliases_are_not_counted_as_independent_duplicates(self):
        original = self.file("original", b"same")
        alias = self.data / "alias"
        try:
            os.link(original, alias)
        except PermissionError:
            self.skipTest("Current filesystem sandbox disallows creating hard links")
        independent = self.file("copy", b"same")
        ordered, count = core.choose_duplicate_reference([str(original), str(alias), str(independent)])
        self.assertEqual(count, 1)
        self.assertEqual(len(ordered), 2)
        usage = core.scan_usage(str(self.data))
        self.assertEqual(usage["total"], 8)

    def test_duplicate_newest_oldest_and_preferred_directory(self):
        old = self.file("archive/old", age=40)
        new = self.file("current/new", age=1)
        paths = [str(old), str(new)]
        self.assertEqual(core.choose_duplicate_reference(paths, "newest")[0][0], str(new))
        self.assertEqual(core.choose_duplicate_reference(paths, "oldest")[0][0], str(old))
        self.assertEqual(core.choose_duplicate_reference(paths, "preferred", str(old.parent))[0][0], str(old))

    def test_complete_snapshots_persist_and_report_growth(self):
        self.file("one/file", b"1234")
        previous = core.scan_usage(str(self.data))
        self.store.save_snapshot(previous)
        self.file("one/file", b"1234567")
        self.file("two/other", b"xx")
        current = core.scan_usage(str(self.data))
        loaded = self.store.snapshots(str(self.data))[0]
        growth = core.compare_snapshots(loaded, current)
        self.assertEqual(next(row["delta"] for row in growth if row["path"] == core.canonical(str(self.data))), 5)
        self.assertEqual(loaded["total"], 4)
        self.assertEqual(current["extensions"]["(无扩展名)"]["size"], 9)

    def test_partial_or_different_policy_snapshots_cannot_be_compared(self):
        first = core.scan_usage(str(self.data))
        incomplete = dict(first, complete=False)
        with self.assertRaises(ValueError):
            self.store.save_snapshot(incomplete)
        with self.assertRaises(ValueError):
            core.compare_snapshots(first, incomplete)
        with self.assertRaises(ValueError):
            core.compare_snapshots(first, dict(first, policy=["*.log"]))

    def test_history_exports_preserve_unicode_and_structured_details(self):
        self.store.record("常规清理", dict(success=1, items=[dict(path="中文.log")]))
        for extension in ("csv", "json"):
            path = self.root / f"history.{extension}"
            self.store.export_history(str(path))
            self.assertIn("中文.log", path.read_text(encoding="utf-8-sig"))

    def test_auto_policy_requires_all_conditions_and_cooldown(self):
        policy = dict(core.DEFAULT_AUTO_POLICY, enabled=True, skip_processes=["Code.exe"])
        values = dict(free=1024, idle=1000, processes=set(), now=100000)
        self.assertTrue(core.check_auto_policy(policy, **values)[0])
        self.assertFalse(core.check_auto_policy(policy, last_run=99999, **values)[0])
        self.assertFalse(core.check_auto_policy(policy, **dict(values, processes={"code.EXE"}))[0])
        self.assertFalse(core.check_auto_policy(policy, **dict(values, idle=0))[0])
        self.assertFalse(core.check_auto_policy(policy, **dict(values, free=100*1024**3))[0])

    def test_job_lock_prevents_concurrent_execution_and_recovers(self):
        errors = []
        def compete():
            try:
                with core.JobLock(str(self.root)):
                    pass
            except RuntimeError as exc:
                errors.append(str(exc))
        with core.JobLock(str(self.root)):
            thread = threading.Thread(target=compete)
            thread.start()
            thread.join(timeout=5)
            self.assertEqual(len(errors), 1)
        with core.JobLock(str(self.root)):
            pass

    def test_mocked_hardlink_ids_collapse_to_one_physical_copy(self):
        paths = [str(self.data / "a"), str(self.data / "alias"), str(self.data / "b")]
        def fake_stat(path, **kwargs):
            return types.SimpleNamespace(st_dev=1, st_ino=11 if str(path).endswith("b") else 10,
                                         st_mtime_ns=123, st_mode=0o100644, st_file_attributes=0)
        with mock.patch.object(core.os, "stat", side_effect=fake_stat):
            ordered, aliases = core.choose_duplicate_reference(paths)
        self.assertEqual(len(ordered), 2)
        self.assertEqual(aliases, 1)

    def test_native_compaction_closes_handle_on_failure(self):
        def open_disk(storage, path, access, flags, parameters, handle):
            self.assertEqual(parameters._obj.Version, 2)
            handle._obj.value = 123
            return 0
        api = types.SimpleNamespace(OpenVirtualDisk=mock.Mock(side_effect=open_disk),
                                    CompactVirtualDisk=mock.Mock(return_value=5))
        kernel = types.SimpleNamespace(CloseHandle=mock.Mock())
        with self.assertRaises(OSError):
            core._compact_vhdx_native("test.vhdx", api, kernel)
        kernel.CloseHandle.assert_called_once()

    def test_tracked_legacy_worker_groups_deletions_and_exclusions(self):
        first = self.file("delete.log")
        keep = self.file("keep.log")
        self.store.set("exclusions", [str(keep)])
        @core.tracked_operation("Legacy")
        def run():
            main.delete_path(str(first), True, lambda s: None)
            main.delete_path(str(keep), True, lambda s: None)
        run()
        rows = self.store.history()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["success"], 1)
        self.assertEqual(rows[0]["skipped"], 1)
        self.assertTrue(keep.exists())

    def test_backup_migrates_exclusions_conditions_and_history(self):
        self.store.set("exclusions", ["*.psd"])
        self.store.record("sample", {"success": 1})
        destination = self.root / "new-config"
        self.store.backup_to(str(destination))
        migrated = core.Store(str(destination))
        self.assertEqual(migrated.get("exclusions"), ["*.psd"])
        self.assertEqual(migrated.history()[0]["success"], 1)

    def test_empty_directory_cannot_remove_an_excluded_descendant_path(self):
        nested = self.data / "nested"
        nested.mkdir()
        plan = core.build_plan([self.rule()])
        self.store.set("exclusions", [str(nested / "future-protected-file")])
        result = core.execute_plan(plan, self.delete, True)
        self.assertTrue(nested.exists())
        self.assertEqual(result["skipped"], 1)

    def test_reparse_ancestor_added_after_preview_blocks_execution(self):
        path = self.file("file.log")
        plan = core.build_plan([self.rule()])
        with mock.patch.object(core, "has_link_ancestor", return_value=True):
            result = core.execute_plan(plan, self.delete, True)
        self.assertEqual(result["skipped"], 1)
        self.assertTrue(path.exists())

    def test_preview_deduplicates_overlapping_rules(self):
        self.file("nested/file.log")
        plan = core.build_plan([self.rule(), ("Nested", str(self.data / "nested"), "dir", True, "", True, "")])
        paths = [item["path"] for item in plan["items"]]
        self.assertEqual(len(paths), len(set(paths)))

    def test_version_is_consistent(self):
        self.assertEqual(main.CURRENT_VERSION, "0.8.2")
        self.assertEqual(core.VERSION, main.CURRENT_VERSION)

    def test_cloud_language_updates_preserve_bundled_new_labels(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({"translations": {"旧标签": "Cloud"}}).encode()
        with mock.patch.object(main, "_urlopen", return_value=response):
            pack = main.load_language_pack("en_us", str(self.root), manifest={"en_us": {"url": "https://example.invalid/pack.json"}})
        self.assertEqual(pack["旧标签"], "Cloud")
        self.assertEqual(pack["维护中心"], "Maintenance Center")

    def test_native_compaction_open_failure_does_not_close_invalid_handle(self):
        api = types.SimpleNamespace(OpenVirtualDisk=mock.Mock(return_value=5), CompactVirtualDisk=mock.Mock())
        kernel = types.SimpleNamespace(CloseHandle=mock.Mock())
        with self.assertRaises(OSError):
            core._compact_vhdx_native("test.vhdx", api, kernel)
        kernel.CloseHandle.assert_not_called()

    def test_space_snapshot_metadata_does_not_load_tree_payloads(self):
        snapshot = core.scan_usage(str(self.data))
        item_id = self.store.save_snapshot(snapshot)
        headers = self.store.snapshot_headers(str(self.data))
        self.assertEqual(headers[0]["id"], item_id)
        self.assertNotIn("nodes", headers[0])
        self.assertEqual(self.store.snapshot(item_id)["root"], snapshot["root"])

    def test_recorded_system_command_retains_failure_details(self):
        with mock.patch.object(core, "command", side_effect=RuntimeError("daemon unavailable")):
            with self.assertRaises(RuntimeError):
                core.recorded_command(["docker", "builder", "prune", "--force"], "Docker 清理")
        row = self.store.history()[0]
        self.assertEqual(row["state"], "failed")
        self.assertIn("daemon unavailable", row["error"])

    def test_exclusions_reject_relative_literal_paths(self):
        with self.assertRaises(ValueError):
            core.Policy(["relative-directory"])

    def test_scheduled_job_skips_before_cleanup_when_conditions_fail(self):
        config = self.store.directory
        self.store.set("auto_policy", dict(core.DEFAULT_AUTO_POLICY, enabled=True, notify=False))
        with (mock.patch.object(main, "load_runtime_targets_and_settings", return_value=(config, {}, [])),
              mock.patch.object(core, "check_auto_policy", return_value=(False, "not idle")),
              mock.patch.object(main, "_run_scheduled_job_impl") as execute):
            self.assertEqual(main.run_scheduled_job(task_name="test"), 0)
        execute.assert_not_called()
        self.assertEqual(self.store.history()[0]["state"], "skipped")

    def test_scheduled_failure_is_recorded_and_returns_nonzero(self):
        config = self.store.directory
        with (mock.patch.object(main, "load_runtime_targets_and_settings", return_value=(config, {}, [])),
              mock.patch.object(main, "_run_scheduled_job_impl", side_effect=RuntimeError("failed cleanup"))):
            self.assertEqual(main.run_scheduled_job(task_name="test"), 1)
        self.assertEqual(self.store.history()[0]["state"], "failed")

    def test_rule_pack_failure_restores_previous_config(self):
        old = self.rule()
        incoming = self.rule(path=self.data / "new")
        window = types.SimpleNamespace(_targets_lock=threading.Lock(), targets=[old],
                    custom_rules_path=str(self.root / "rules.json"), config_path=str(self.root / "state.json"),
                    pg_clean=types.SimpleNamespace(reload_table=mock.Mock()))
        dialog = types.SimpleNamespace(exec=lambda: True, review=core.inspect_pack([list(incoming)]))
        with (mock.patch.object(main, "ReviewPackDialog", return_value=dialog),
              mock.patch.object(self.store, "activate_pack", side_effect=OSError("database unavailable"))):
            with self.assertRaises(OSError):
                main.apply_reviewed_rule_pack(window, [list(incoming)], "pack")
        self.assertEqual(window.targets, [old])
        self.assertEqual(json.loads(Path(window.custom_rules_path).read_text(encoding="utf-8"))[0][0], old[0])

    def test_zero_age_limit_includes_files_with_future_timestamps(self):
        path = self.file("future.log")
        future = time.time() + 86400
        os.utime(path, (future, future))
        self.assertEqual(len(core.build_plan([self.rule()])["items"]), 1)

    def test_reclaimable_disk_size_uses_compressed_and_high_word_size(self):
        path = self.file("compressed.bin", b"12345")
        def native_size(name, high):
            high._obj.value = 2
            return 4096
        api = types.SimpleNamespace(GetCompressedFileSizeW=mock.Mock(side_effect=native_size))
        self.assertEqual(core.reclaimable_disk_bytes(str(path), api), (2 << 32) + 4096)

    def test_reclaimable_disk_size_handles_disappearing_files(self):
        self.assertIsNone(core.reclaimable_disk_bytes(str(self.data / "missing")))

    def test_pack_diff_detects_changes_duplicates_and_broad_scope(self):
        before = [list(self.rule(path=self.data / "old"))]
        after = [list(self.rule(path=self.data / "new")), list(self.rule(path=self.data / "new")),
                 ["Broad", "%USERPROFILE%", "glob", False, "", True, "*"]]
        review = core.inspect_pack(after, before)
        self.assertEqual(len(review["removed"]), 1)
        self.assertEqual(len(review["added"]), 2)
        self.assertTrue(any("重复" in warning for warning in review["warnings"]))
        self.assertTrue(any("范围过宽" in warning for warning in review["warnings"]))
        self.store.activate_pack("pack", before)
        self.store.activate_pack("pack", after)
        self.assertEqual(sum(row["active"] for row in self.store.packs("pack")), 1)

    def test_pack_rejects_invalid_rules(self):
        for rules in ({}, [["Bad", "x", "execute", True, ""]], [["Bad", "x", "dir", True, "", True, []]]):
            with self.subTest(rules=rules), self.assertRaises(ValueError):
                core.inspect_pack(rules)

    def test_development_scan_finds_dependencies_but_not_unmarked_build(self):
        self.file("project/package.json", b"{}")
        dep = self.file("project/node_modules/module/file", b"aaa")
        self.file("project/build/output", b"bb")
        self.file("personal/build/file", b"ccc")
        rows = core.scan_development(str(self.data))
        self.assertEqual({row["category"] for row in rows}, {"node_modules", "build"})
        self.assertEqual(sum(row["size"] for row in rows), 5)

    def test_scheduled_regular_clean_uses_advanced_conditions(self):
        recent = self.file("new.log")
        old = self.file("old.log", age=90)
        rule = self.rule()
        self.store.set("conditions", {core.rule_id(rule): dict(min_age_days=30)})
        main._run_scheduled_clean([rule], True, lambda s: None)
        self.assertTrue(recent.exists())
        self.assertFalse(old.exists())
        self.assertEqual(self.store.history()[0]["kind"], "定时常规清理")

    def test_pack_apply_and_rollback_replace_only_owned_unchanged_rules(self):
        old = self.rule(path=self.data / "old")
        new = self.rule(path=self.data / "new")
        personal = ("Personal", str(self.data / "personal"), "dir", True, "", True, "")
        window = types.SimpleNamespace(_targets_lock=threading.Lock(), targets=[old, personal],
                    custom_rules_path=str(self.root / "rules.json"), config_path=str(self.root / "state.json"),
                    pg_clean=types.SimpleNamespace(reload_table=mock.Mock()))
        self.store.activate_pack("test.json", [list(old)])
        def apply(rules):
            dialog = types.SimpleNamespace(exec=lambda: True, review=core.inspect_pack(rules))
            with mock.patch.object(main, "ReviewPackDialog", return_value=dialog), mock.patch.object(main.InfoBar, "success"):
                main.apply_reviewed_rule_pack(window, rules, "test.json")
        apply([list(new)])
        self.assertEqual(window.targets, [personal, new])
        apply([list(old)])
        self.assertEqual(window.targets, [personal, old])
        self.assertEqual(len(json.loads(Path(window.custom_rules_path).read_text(encoding="utf-8"))), 2)


class MaintenanceWidgetTests(unittest.TestCase):
    setUp = MaintenanceTests.setUp
    restore_store = MaintenanceTests.restore_store
    file = MaintenanceTests.file
    rule = MaintenanceTests.rule
    @classmethod
    def setUpClass(cls):
        cls.app = main.QApplication.instance() or main.QApplication([])

    def test_all_maintenance_pages_construct_and_render(self):
        window = types.SimpleNamespace(_targets_lock=threading.Lock(), targets=[self.rule()])
        page = MaintenancePage(window, main)
        self.addCleanup(self.dispose, page)
        page.resize(1150, 920)
        page.show()
        for index in range(page.tabs.count()):
            page.tabs.setCurrentIndex(index)
            self.app.processEvents()
            self.assertFalse(page.grab().isNull())

    def test_preview_loads_asynchronously_and_can_uncheck_files(self):
        self.file("file.log")
        dialog = PreviewDialog([self.rule()], main)
        self.addCleanup(self.dispose, dialog)
        dialog.show()
        deadline = time.monotonic() + 5
        from PySide6.QtTest import QTest
        while dialog.plan is None and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertIsNotNone(dialog.plan)
        self.assertTrue(dialog.plan["complete"])
        rect = dialog.view.visualRect(dialog.model.index(0, 0))
        from PySide6.QtCore import QPoint
        QTest.mouseClick(dialog.view.viewport(), main.Qt.MouseButton.LeftButton,
                         main.Qt.KeyboardModifier.NoModifier, QPoint(rect.left() + 24, rect.center().y()))
        self.app.processEvents()
        self.assertFalse(dialog.model.rows[0]["selected"])
        dialog.model.select_all(False)
        self.assertFalse(any(item["selected"] for item in dialog.plan["items"]))

    def test_main_window_registers_and_opens_maintenance_lazily(self):
        with (mock.patch.object(main.MainWindow, "_load_config_dir", new=lambda _self: str(self.root / "window-config")),
              mock.patch.object(main.MainWindow, "_should_offer_legacy_migration", new=lambda _self: False),
              mock.patch.object(main.MainWindow, "check_updates", new=lambda *args, **kwargs: None),
              mock.patch.object(main.MainWindow, "_init_tray", new=lambda _self: None)):
            window = main.MainWindow()
            self.addCleanup(self.dispose, window)
            self.assertIsNone(window.pg_maintenance)
            page = window._ensure_lazy_page("pg_maintenance")
            window.switchTo(page)
            self.app.processEvents()
            self.assertIs(page, window.pg_maintenance)
            self.assertEqual(page.tabs.count(), 8)

    def dispose(self, widget):
        from PySide6.QtCore import QCoreApplication, QEvent
        widget.close()
        worker = getattr(widget, "worker", None)
        if worker:
            worker.join(timeout=5)
        widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
