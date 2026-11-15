import os
import stat
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cleaner_core as core
import health_check as health
import main


class IncrementalCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        prior = core.current_store()
        self.addCleanup(setattr, core, "_store", prior)
        core.configure(str(self.root / "config"))
        self.data = self.root / "data"
        self.data.mkdir()

    def rule(self, path=None, kind="dir", pattern=""):
        return ("test", str(path or self.data), kind, True, "", True, pattern)

    def file(self, name, age=0):
        p = self.data / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"data")
        if age:
            t = time.time() - age * 86400
            os.utime(p, (t, t))
        return p

    def delete(self, path, permanent, log):
        os.unlink(path)
        log("deleted " + path)
        return True

    def test_cleanup_starts_before_later_rules_are_scanned(self):
        a = self.file("one/a")
        b = self.file("two/b")
        original = os.scandir
        def scandir(path):
            if core.canonical(str(path)) == core.canonical(str(b.parent)):
                self.assertFalse(a.exists(), "cleanup must start before the second rule scan")
            return original(path)
        with mock.patch.object(core.os, "scandir", new=scandir), mock.patch.object(core, "build_plan", side_effect=AssertionError("no full preview")):
            result = core.clean_rules([self.rule(a.parent), self.rule(b.parent)], self.delete, True)
        self.assertEqual(result["success"], 2)
        self.assertFalse(b.exists())

    def test_incremental_cleanup_preserves_conditions_and_protected_files(self):
        latest = self.file("latest", 1)
        old = self.file("old", 40)
        protected = self.file("keep.psd", 60)
        core.current_store().set("exclusions", ["*.psd"])
        rule = self.rule()
        core.current_store().set("conditions", {core.rule_id(rule): dict(keep_latest=1, min_age_days=30, min_size_bytes=1)})
        result = core.clean_rules([rule], self.delete, True)
        self.assertTrue(latest.exists())
        self.assertTrue(protected.exists())
        self.assertFalse(old.exists())
        self.assertEqual(result["success"], 1)

    def test_incremental_cancel_stops_and_is_recorded(self):
        for i in range(20):
            self.file(str(i))
        stop = threading.Event()
        def delete(path, permanent, log):
            value = self.delete(path, permanent, log)
            stop.set()
            return value
        result = core.clean_rules([self.rule()], delete, True, stop)
        self.assertEqual(result["success"], 1)
        self.assertTrue(result["cancelled"])
        self.assertFalse(result["scan_complete"])
        self.assertEqual(core.current_store().history()[0]["state"], "cancelled")
        self.assertEqual(len(list(self.data.iterdir())), 19)

    def test_new_exclusion_during_streaming_is_rechecked(self):
        a = self.file("one")
        b = self.file("two")
        def delete(path, permanent, log):
            core.current_store().set("exclusions", [str(a), str(b)])
            return main.delete_path(path, permanent, log)
        result = core.clean_rules([self.rule()], delete, True)
        self.assertTrue(a.exists() and b.exists())
        self.assertEqual(result["success"], 0)

    def test_preview_reuses_overlapping_directory_reads_and_file_ids(self):
        p = self.file("nested/item")
        original = os.scandir
        reads = []
        def scandir(path):
            reads.append(core.canonical(str(path)))
            return original(path)
        with mock.patch.object(core.os, "scandir", new=scandir):
            plan = core.build_plan([self.rule(), self.rule(p.parent)])
        self.assertEqual(reads.count(core.canonical(str(p.parent))), 1)
        item = next(i for i in plan["items"] if i["kind"] == "file")
        self.assertEqual(item["signature"], core.signature(os.lstat(p)))
        self.assertEqual(core.execute_plan(plan, self.delete, True)["success"], 2)

    def test_no_success_log_flood_and_progress_includes_final_count(self):
        for i in range(100):
            self.file(str(i))
        logs, updates = [], []
        with mock.patch.object(core.time, "monotonic", return_value=10):
            result = core.clean_rules([self.rule()], self.delete, True, log=logs.append,
                                      progress=lambda value, total: updates.append((value, total)))
        self.assertEqual(result["success"], 100)
        self.assertEqual(logs, [])
        self.assertEqual(updates, [(1, 0), (100, 0)])
        self.assertEqual(len(core.current_store().history()[0]["items"]), 100)


class CheckupScaleTests(unittest.TestCase):
    def scan(self, file_count, folders, progress=None, stop=None, limit=None):
        # Virtual filesystem: no payload files or personal directories accessed.
        drive = os.path.abspath(os.sep)
        roots = [core.canonical(os.path.join(drive, "checkup-test", str(i))) for i in range(folders)]
        dirs = set(roots)
        each = file_count // folders
        class Entries:
            def __init__(self, path):
                self.path = path
            def __enter__(self):
                return (types.SimpleNamespace(path=os.path.join(self.path, str(i))) for i in range(each))
            def __exit__(self, *args):
                pass
        def lstat(path):
            path = core.canonical(path)
            return types.SimpleNamespace(st_mode=(stat.S_IFDIR if path in dirs else stat.S_IFREG) | 0o700,
                 st_dev=1, st_ino=path, st_size=1, st_mtime=1, st_mtime_ns=1, st_ctime_ns=1, st_nlink=1, st_file_attributes=0)
        original = health.contains
        calls = [0]
        def contains(path, root):
            calls[0] += 1
            return original(path, root)
        policy = types.SimpleNamespace(excluded=lambda path: False)
        with (mock.patch.object(core.os.path, "isdir", new=lambda path: path in dirs or path == drive),
              mock.patch.object(core.os, "lstat", new=lstat), mock.patch.object(core.os, "scandir", new=Entries),
              mock.patch.object(core, "has_link_ancestor", new=lambda path: False), mock.patch.object(health, "contains", new=contains)):
            report = health.scan_checkup([], [], [], [(p, p, p) for p in roots], [], [drive],
                       conditions={}, policy=policy, max_files=limit or file_count, progress=progress, stop=stop, large_threshold=1)
        return report, calls[0]

    def test_summary_does_not_compare_all_files_with_every_folder(self):
        report, comparisons = self.scan(10000, 200)
        self.assertEqual(report["file_count"], 10000)
        self.assertEqual(report["total"], 10000)
        self.assertEqual(sum(app["size"] for app in report["apps"]), 10000)
        self.assertLess(comparisons, 30000)

    def test_cancel_during_summary_returns_without_recommending_cleanup(self):
        stop = threading.Event()
        phases = []
        def progress(label, count):
            phases.append(label)
            if label == "正在汇总文件归属":
                stop.set()
        report, _calls = self.scan(10000, 100, progress, stop)
        self.assertTrue(report["cancelled"])
        self.assertFalse(report["complete"])
        self.assertEqual(report["recommended_bytes"], 0)
        self.assertEqual(phases[-1], "扫描结束")

    def test_file_limit_reports_once_and_keeps_partial_totals(self):
        report, _calls = self.scan(12000, 200, limit=10000)
        self.assertEqual(report["file_count"], 10000)
        self.assertFalse(report["complete"])
        self.assertEqual(len(report["errors"]), 1)
        self.assertEqual(report["total"], 10000)


if __name__ == "__main__":
    unittest.main()
