"""Persistent maintenance services. No Qt or administrator privileges required.

All timestamps in storage are UTC epoch seconds. Scans never follow reparse
points; a reviewed plan contains regular files and empty directories only.
"""
from __future__ import annotations

import contextlib
import csv
import fnmatch
import functools
import hashlib
import io
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

VERSION = "0.8.3"
_store: Store | None = None
_local = threading.local()
_policy_cache = None
_policy_cache_at = 0.0


def canonical(path: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.expandvars(os.path.expanduser(path))))


def within(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([canonical(path), canonical(root)]) == canonical(root)
    except ValueError:
        return False


def link_like(path: str, st=None) -> bool:
    st = st or os.lstat(path)
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & 0x400)


def has_link_ancestor(path: str) -> bool:
    parent = os.path.dirname(os.path.abspath(path))
    while parent:
        if link_like(parent):
            return True
        next_parent = os.path.dirname(parent)
        if next_parent == parent:
            break
        parent = next_parent
    return False


def signature(st) -> list[int]:
    return [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns]


def atomic_text(path: str, text: str) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".maintenance-", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Store:
    def __init__(self, directory: str):
        self.directory = os.path.abspath(directory)
        os.makedirs(self.directory, exist_ok=True)
        self.path = os.path.join(self.directory, "maintenance.sqlite3")
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS history
                    (id TEXT PRIMARY KEY, created REAL NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS snapshots
                    (id TEXT PRIMARY KEY, created REAL NOT NULL, root TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS packs
                    (id TEXT PRIMARY KEY, created REAL NOT NULL, name TEXT NOT NULL, digest TEXT NOT NULL,
                     payload TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 0);
            """)

    @contextlib.contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=15)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def get(self, key: str, default=None):
        with self.connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key: str, value) -> None:
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, json.dumps(value, ensure_ascii=False)))
        if key == "exclusions":
            global _policy_cache_at
            _policy_cache_at = 0.0

    def record(self, kind: str, payload: dict) -> str:
        record_id = uuid.uuid4().hex
        with self.connect() as db:
            db.execute("INSERT INTO history VALUES (?,?,?,?)", (record_id, time.time(), kind, json.dumps(payload, ensure_ascii=False)))
        return record_id

    def history(self, limit: int = 500) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT id,created,kind,payload FROM history ORDER BY created DESC LIMIT ?", (limit,)).fetchall()
        return [dict(id=i, created=t, kind=k, **json.loads(p)) for i, t, k, p in rows]

    def export_history(self, path: str) -> None:
        rows = self.history(100000)
        if path.lower().endswith(".csv"):
            stream = io.StringIO(newline="")
            writer = csv.writer(stream)
            writer.writerow(["id", "created_utc", "kind", "success", "failed", "skipped", "estimated_bytes", "free_delta", "details_json"])
            for row in rows:
                writer.writerow([row["id"], time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(row["created"])), row["kind"],
                                 row.get("success", 0), row.get("failed", 0), row.get("skipped", 0),
                                 row.get("estimated_bytes", 0), row.get("free_delta", 0), json.dumps(row, ensure_ascii=False)])
            atomic_text(path, "\ufeff" + stream.getvalue())
        else:
            atomic_text(path, json.dumps(rows, ensure_ascii=False, indent=2))

    def save_snapshot(self, snapshot: dict) -> str:
        if not snapshot.get("complete"):
            raise ValueError("扫描不完整，不可作为增长对比快照")
        item_id = uuid.uuid4().hex
        with self.connect() as db:
            db.execute("INSERT INTO snapshots VALUES (?,?,?,?)", (item_id, time.time(), canonical(snapshot["root"]), json.dumps(snapshot, ensure_ascii=False)))
            # Retain 20 full snapshots per root to bound persistent storage.
            db.execute("DELETE FROM snapshots WHERE root=? AND id NOT IN (SELECT id FROM snapshots WHERE root=? ORDER BY created DESC LIMIT 20)",
                       (canonical(snapshot["root"]), canonical(snapshot["root"])))
        return item_id

    def snapshots(self, root: str) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT id,created,payload FROM snapshots WHERE root=? ORDER BY created DESC", (canonical(root),)).fetchall()
        return [{**json.loads(p), "id": i, "created": t} for i, t, p in rows]

    def snapshot_headers(self, root: str) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT id,created,root FROM snapshots WHERE root=? ORDER BY created DESC", (canonical(root),)).fetchall()
        return [dict(id=i, created=t, root=r) for i, t, r in rows]

    def snapshot(self, item_id: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT payload FROM snapshots WHERE id=?", (item_id,)).fetchone()
        if row is None:
            raise ValueError("快照已被移除，请重新扫描")
        return json.loads(row[0])

    def packs(self, name: str = "") -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT id,created,name,digest,payload,active FROM packs WHERE (?='' OR name=?) ORDER BY created DESC", (name, name)).fetchall()
        return [dict(id=i, created=t, name=n, digest=d, rules=json.loads(p), active=bool(a)) for i, t, n, d, p, a in rows]

    def activate_pack(self, name: str, rules: list) -> str:
        payload = json.dumps(rules, ensure_ascii=False, sort_keys=True)
        item_id = uuid.uuid4().hex
        digest = hashlib.sha256(payload.encode()).hexdigest()
        with self.connect() as db:
            db.execute("UPDATE packs SET active=0 WHERE name=?", (name,))
            db.execute("INSERT INTO packs VALUES (?,?,?,?,?,1)", (item_id, time.time(), name, digest, payload))
        return item_id

    def backup_to(self, directory: str) -> None:
        os.makedirs(directory, exist_ok=True)
        target = os.path.join(directory, "maintenance.sqlite3")
        if canonical(target) == canonical(self.path):
            return
        with self.connect() as source:
            destination = sqlite3.connect(target, timeout=15)
            try:
                source.backup(destination)
            finally:
                destination.close()


def configure(directory: str) -> Store:
    global _store, _policy_cache_at, _policy_cache
    _store = Store(directory)
    _policy_cache_at, _policy_cache = 0.0, None
    return _store


def current_store() -> Store | None:
    return _store


def active_policy():
    global _policy_cache, _policy_cache_at
    now = time.monotonic()
    if _policy_cache is None or now - _policy_cache_at >= 0.5:
        _policy_cache = Policy()
        _policy_cache_at = now
    return _policy_cache


def rule_id(rule) -> str:
    pattern = str(rule[6] if len(rule) > 6 else "")
    value = [str(rule[0]), canonical(str(rule[1])), str(rule[2]), pattern.lower()]
    return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode()).hexdigest()


def validate_conditions(value: dict) -> dict:
    result = {}
    for key in ("min_age_days", "min_size_bytes", "keep_latest"):
        raw = value.get(key, 0)
        if isinstance(raw, bool) or not isinstance(raw, (float, int)) or raw < 0 or raw > 2**63 - 1:
            raise ValueError(f"无效条件: {key}")
        result[key] = int(raw)
    return result


class Policy:
    def __init__(self, entries=None):
        if entries is None:
            entries = _store.get("exclusions", []) if _store else []
        if not isinstance(entries, (list, tuple)):
            raise ValueError("排除配置必须为路径或通配符列表")
        self.entries = [str(p).strip() for p in entries if str(p).strip()]
        for entry in self.entries:
            if "\x00" in entry or (not any(c in entry for c in "*?[") and not os.path.isabs(os.path.expandvars(os.path.expanduser(entry)))):
                raise ValueError(f"排除路径必须为绝对路径或通配符: {entry}")
        self.paths = [canonical(p) for p in self.entries if not any(c in p for c in "*?[")]
        self.patterns = [os.path.normcase(os.path.expandvars(p)).replace("/", "\\") for p in self.entries if any(c in p for c in "*?[")]

    def excluded(self, path: str) -> bool:
        target = canonical(path)
        if any(within(target, root) for root in self.paths):
            return True
        # Match every ancestor too: excluding *.git protects its whole subtree.
        probe = target
        while True:
            normalized = probe.replace("/", "\\")
            if any(fnmatch.fnmatchcase(normalized if "\\" in p else os.path.normcase(os.path.basename(probe)), p) for p in self.patterns):
                return True
            parent = os.path.dirname(probe)
            if parent == probe:
                return False
            probe = parent

    def deletion_reason(self, path: str) -> str:
        if self.excluded(path):
            return "命中统一排除规则"
        if any(within(root, path) for root in self.paths):
            return "目录包含受排除规则保护的路径"
        if not self.patterns or not os.path.isdir(path) or link_like(path):
            return ""
        # Whole-directory deletion must not bypass a protected descendant.
        stack = [path]
        while stack:
            try:
                with os.scandir(stack.pop()) as entries:
                    for entry in entries:
                        if self.excluded(entry.path):
                            return f"目录包含排除项: {entry.path}"
                        if entry.is_dir(follow_symlinks=False) and not link_like(entry.path):
                            stack.append(entry.path)
            except OSError as exc:
                return f"无法完整检查排除项: {exc}"
        return ""


def audit_deletion(path: str, permanent: bool, action: Callable[[], bool], log: Callable[[str], None]) -> bool:
    """Shared gate for every existing file-deletion entry point."""
    reason = active_policy().deletion_reason(path)
    if not reason and os.path.lexists(path):
        try:
            if has_link_ancestor(path):
                reason = "父路径包含链接或重解析点"
        except OSError as exc:
            reason = f"无法复核父路径: {exc}"
    if reason:
        log(f"[统一排除] {path}: {reason}")
        _append_operation_item(dict(path=path, status="skipped", reason=reason, size=0))
        return False
    if getattr(_local, "batch", False) or _store is None:
        return action()
    started = time.time()
    operation = getattr(_local, "operation", None)
    volume = os.path.splitdrive(os.path.abspath(path))[0] + os.sep
    free_before = _volume_free(volume)
    if operation is not None:
        operation.setdefault("volumes", {}).setdefault(volume, free_before)
    try:
        st = os.lstat(path)
        logical_size = st.st_size if stat.S_ISREG(st.st_mode) and st.st_nlink <= 1 else 0
    except OSError:
        logical_size = 0
    result = False
    try:
        result = action()
        return result
    finally:
        try:
            item = dict(path=path, permanent=bool(permanent), size=logical_size,
                        status="deleted" if result and not os.path.lexists(path) else "pending" if result else "failed")
            if not _append_operation_item(item):
                _store.record("文件清理", {"success": int(result), "failed": int(not result), "permanent": bool(permanent),
                                         "items": [item], "started": started, "free_delta": free_change(volume, free_before),
                                         "estimated_bytes": logical_size if item["status"] == "deleted" else 0})
        except Exception as exc:
            log(f"[历史记录失败] {exc}")


def _append_operation_item(item: dict) -> bool:
    operation = getattr(_local, "operation", None)
    if operation is None:
        return False
    operation["items"].append(item)
    return True


def tracked_operation(kind: str):
    """Group legacy deletion workers into one durable history entry."""
    def decorate(fn):
        @functools.wraps(fn)
        def wrapped(*args, **kwargs):
            store = _store
            if store is None or getattr(_local, "operation", None) is not None:
                return fn(*args, **kwargs)
            with JobLock(store.directory):
                operation = dict(started=time.time(), items=[], state="running")
                record_id = store.record(kind, operation)
                _local.operation = operation
                try:
                    return fn(*args, **kwargs)
                except Exception as exc:
                    operation["error"] = str(exc)
                    operation["state"] = "failed"
                    raise
                finally:
                    _local.operation = None
                    operation["finished"] = time.time()
                    if operation["state"] == "running":
                        operation["state"] = "finished"
                    operation["success"] = sum(item["status"] == "deleted" for item in operation["items"])
                    operation["failed"] = sum(item["status"] == "failed" for item in operation["items"])
                    operation["skipped"] = sum(item["status"] in {"skipped", "pending"} for item in operation["items"])
                    operation["estimated_bytes"] = sum(item.get("size", 0) for item in operation["items"] if item["status"] == "deleted")
                    # Unknown legacy directory sizes stay 0 instead of an
                    # expensive extra traversal immediately before deletion.
                    operation["free_delta"] = sum(free_change(root, before) for root, before in operation.pop("volumes", {}).items())
                    with store.connect() as db:
                        db.execute("UPDATE history SET payload=? WHERE id=?", (json.dumps(operation, ensure_ascii=False), record_id))
        return wrapped
    return decorate


def tracked_resource(kind: str):
    def decorate(fn):
        @functools.wraps(fn)
        def wrapped(target, *args, **kwargs):
            result = fn(target, *args, **kwargs)
            if _store:
                item = dict(path=str(target), resource=kind, size=0,
                            status="deleted" if result is True or result in ("deleted", "missing") else "failed")
                if not _append_operation_item(item):
                    _store.record(kind, dict(items=[item], success=int(item["status"] == "deleted"), failed=int(item["status"] == "failed")))
            return result
        return wrapped
    return decorate


def tracked_uninstall(fn):
    @functools.wraps(fn)
    def wrapped(name, *args, **kwargs):
        result = fn(name, *args, **kwargs)
        if _store:
            state = result[0]
            item = dict(path=name, resource="应用卸载", size=0,
                        status="deleted" if state == "ok" else "skipped" if state == "skipped" else "failed",
                        detail=str(result[1]))
            if not _append_operation_item(item):
                _store.record("应用卸载", dict(items=[item], state="finished" if state == "ok" else state,
                                             success=int(state == "ok"), failed=int(state not in {"ok", "skipped"})))
        return result
    return wrapped


def build_plan(rules: list, stop: threading.Event | None = None, protected: Callable[[str], bool] | None = None,
               max_items: int = 200000, condition_overrides: dict | None = None, policy: Policy | None = None) -> dict:
    policy = policy or Policy()
    conditions = condition_overrides if condition_overrides is not None else (_store.get("conditions", {}) if _store else {})
    plan = dict(id=uuid.uuid4().hex, created=time.time(), items=[], errors=[], complete=True, omitted=0)
    seen = set()
    now = time.time()

    def cancelled():
        return stop is not None and stop.is_set()

    for rule in rules:
        if cancelled():
            plan["complete"] = False
            break
        if not isinstance(rule, (list, tuple)) or len(rule) < 5 or not rule[3]:
            continue
        name, root, kind = str(rule[0]), canonical(str(rule[1])), str(rule[2])
        if kind not in {"dir", "file", "glob"}:
            plan["errors"].append(f"{name}: 未知规则类型")
            continue
        cond = validate_conditions(conditions.get(rule_id(rule), {}))
        candidates = []
        directories = []
        if policy.excluded(root) or (protected and protected(root)):
            plan["omitted"] += 1
            continue
        try:
            if not os.path.lexists(root):
                continue
            if link_like(root) or has_link_ancestor(root):
                plan["omitted"] += 1
                continue
            stack = []
            if kind == "file":
                candidates.append(root)
            else:
                with os.scandir(root) as entries:
                    pattern = str(rule[6] if len(rule) > 6 and rule[6] else "*")
                    for entry in entries:
                        if kind != "glob" or fnmatch.fnmatchcase(entry.name.lower(), pattern.lower()):
                            stack.append(entry.path)
            while stack:
                if cancelled():
                    plan["complete"] = False
                    break
                path = stack.pop()
                if policy.excluded(path) or (protected and protected(path)) or link_like(path):
                    plan["omitted"] += 1
                    continue
                if os.path.isdir(path):
                    directories.append(path)
                    with os.scandir(path) as entries:
                        stack.extend(entry.path for entry in entries)
                else:
                    candidates.append(path)
                if len(candidates) + len(directories) + len(plan["items"]) > max_items:
                    plan["complete"] = False
                    plan["errors"].append("清理预览达到项目上限，请缩小范围后重试")
                    break
            files = []
            for path in candidates:
                if cancelled():
                    plan["complete"] = False
                    break
                try:
                    st = os.lstat(path)
                    if not stat.S_ISREG(st.st_mode) or link_like(path, st) or policy.excluded(path) or (protected and protected(path)):
                        continue
                    files.append((path, st))
                except OSError as exc:
                    plan["errors"].append(f"{path}: {exc}")
            files.sort(key=lambda item: (-item[1].st_mtime_ns, canonical(item[0])))
            for index, (path, st) in enumerate(files):
                if index < cond["keep_latest"] or st.st_size < cond["min_size_bytes"] or (cond["min_age_days"] and now - st.st_mtime < cond["min_age_days"] * 86400):
                    plan["omitted"] += 1
                    continue
                key = canonical(path)
                if key in seen:
                    continue
                seen.add(key)
                plan["items"].append(dict(path=path, rule=name, kind="file", size=st.st_size, mtime=st.st_mtime,
                                          signature=signature(st), selected=True, nlink=st.st_nlink))
            # Never recursively delete a directory from a preview. Only rmdir
            # after the reviewed files have gone, and only with no age filters.
            if not any(cond.values()):
                for path in sorted(directories, key=len, reverse=True):
                    if canonical(path) in seen:
                        continue
                    seen.add(canonical(path))
                    st = os.lstat(path)
                    plan["items"].append(dict(path=path, rule=name, kind="empty-directory", size=0, mtime=st.st_mtime,
                                              signature=signature(st), selected=True))
        except OSError as exc:
            plan["errors"].append(f"{name}: {exc}")
        if not plan["complete"]:
            break
    if plan["errors"]:
        plan["complete"] = False
    return plan


def _volume_free(path: str):
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None


def free_change(root: str, before) -> int:
    current = _volume_free(root)
    return current - before if current is not None and before is not None else 0


def execute_plan(plan: dict, delete: Callable, permanent: bool, stop=None, log=lambda s: None,
                 protected=None, progress=None, kind="常规清理") -> dict:
    if not plan.get("complete"):
        raise ValueError("扫描不完整，请缩小范围或解决读取错误后重新预览")
    selected = [dict(item) for item in plan["items"] if item.get("selected")]
    selected.sort(key=lambda item: (item["kind"] != "file", -len(item["path"]) if item["kind"] != "file" else 0))
    policy = Policy()
    roots = {os.path.splitdrive(item["path"])[0] + os.sep for item in selected}
    free_before = {root: _volume_free(root) for root in roots}
    result = dict(plan_id=plan["id"], started=time.time(), permanent=bool(permanent), success=0, failed=0,
                  skipped=0, estimated_bytes=0, items=[], cancelled=False)
    # Reserve the journal before the first side effect. Retain a crash marker.
    store = _store
    journal_id = store.record(kind, dict(result, state="running")) if store else None
    prior_batch = getattr(_local, "batch", False)
    _local.batch = True
    try:
        for index, item in enumerate(selected):
            if stop is not None and stop.is_set():
                result["cancelled"] = True
                break
            path = item["path"]
            status, reason = "skipped", ""
            try:
                st = os.lstat(path)
                if link_like(path, st) or has_link_ancestor(path):
                    reason = "路径已变为链接"
                elif policy.deletion_reason(path) or (protected and protected(path)):
                    reason = "路径受保护或已加入排除"
                elif item["kind"] == "empty-directory":
                    if [st.st_dev, st.st_ino] != item["signature"][:2]:
                        reason = "目录身份已变化"
                    elif os.listdir(path):
                        reason = "目录不为空，保留未勾选或新建内容"
                    else:
                        os.rmdir(path)
                        status = "deleted"
                elif signature(st) != item["signature"]:
                    reason = "文件在预览后发生变化"
                elif not stat.S_ISREG(st.st_mode):
                    reason = "目标不再是普通文件"
                else:
                    # The shared deletion gate rechecks exclusions immediately.
                    messages = []
                    def capture(message):
                        messages.append(str(message))
                        if len(messages)>6:
                            del messages[0]
                        log(message)
                    ok = delete(path, permanent, capture)
                    status = "deleted" if ok and not os.path.lexists(path) else "pending" if ok else "failed"
                    if not ok:
                        reason = "\n".join(messages[-3:]) or "删除未完成；文件可能被占用或权限不足。"
                if status == "deleted":
                    result["success"] += 1
                    # Hard links consume space until their final name is gone.
                    if item.get("nlink", 1) <= 1:
                        result["estimated_bytes"] += item["size"]
                elif status == "pending":
                    result["skipped"] += 1
                    reason = "已安排重启删除，尚未释放空间"
                elif status == "failed":
                    result["failed"] += 1
                else:
                    result["skipped"] += 1
            except FileNotFoundError:
                reason = "目标已不存在"
                result["skipped"] += 1
            except OSError as exc:
                status, reason = "failed", str(exc)
                result["failed"] += 1
            result["items"].append(dict(path=path, rule=item["rule"], size=item["size"], status=status, reason=reason))
            if reason:
                log(f"[{status}] {path}: {reason}")
            if progress:
                progress(index + 1, len(selected))
    finally:
        _local.batch = prior_batch
        result["finished"] = time.time()
        result["free_before"] = free_before
        result["free_after"] = {root: _volume_free(root) for root in free_before}
        result["free_delta"] = sum(result["free_after"][root]-before for root, before in free_before.items()
                                   if before is not None and result["free_after"][root] is not None)
        result["state"] = "cancelled" if result["cancelled"] else "finished"
        if store and journal_id:
            with store.connect() as db:
                db.execute("UPDATE history SET payload=? WHERE id=?", (json.dumps(result, ensure_ascii=False), journal_id))
    return result


def scan_usage(root: str, stop=None, max_nodes=200000, policy: Policy | None = None) -> dict:
    root = canonical(root)
    policy = policy or Policy()
    if not os.path.isdir(root) or link_like(root) or has_link_ancestor(root) or policy.excluded(root):
        raise ValueError("请选择未被排除的普通目录")
    nodes, extensions, errors = {}, {}, []
    stack = [root]
    visited_ids = set()
    seen_files = set()
    complete = True
    while stack:
        if stop is not None and stop.is_set():
            complete = False
            break
        path = stack.pop()
        try:
            st = os.lstat(path)
            if link_like(path, st) or policy.excluded(path):
                continue
            identity = (st.st_dev, st.st_ino)
            if identity in visited_ids:
                continue
            visited_ids.add(identity)
            node = dict(path=path, parent=os.path.dirname(path), size=0, files=0, direct_size=0, children=[])
            nodes[path] = node
            with os.scandir(path) as entries:
                for entry in entries:
                    if stop is not None and stop.is_set():
                        complete = False
                        break
                    try:
                        # DirEntry.stat() can report zero file IDs on Windows;
                        # lstat supplies real IDs for hard-link accounting.
                        est = os.lstat(entry.path)
                        if link_like(entry.path, est) or policy.excluded(entry.path):
                            continue
                        if stat.S_ISDIR(est.st_mode):
                            node["children"].append(canonical(entry.path))
                            stack.append(canonical(entry.path))
                        elif stat.S_ISREG(est.st_mode):
                            file_id = (est.st_dev, est.st_ino)
                            if file_id in seen_files:
                                continue
                            seen_files.add(file_id)
                            node["direct_size"] += est.st_size
                            node["files"] += 1
                            ext = Path(entry.name).suffix.lower() or "(无扩展名)"
                            ext_row = extensions.setdefault(ext, dict(size=0, files=0))
                            ext_row["size"] += est.st_size
                            ext_row["files"] += 1
                    except OSError as exc:
                        if len(errors) < 50:
                            errors.append(f"{entry.path}: {exc}")
                        complete = False
            if len(nodes) >= max_nodes:
                errors.append("目录数达到上限，请缩小扫描范围")
                complete = False
                break
        except OSError as exc:
            if len(errors) < 50:
                errors.append(f"{path}: {exc}")
            complete = False
    for path in sorted(nodes, key=len, reverse=True):
        node = nodes[path]
        node["size"] += node["direct_size"]
        parent = nodes.get(node["parent"])
        if parent:
            parent["size"] += node["size"]
            parent["files"] += node["files"]
    return dict(root=root, created=time.time(), complete=complete, nodes=nodes, extensions=extensions, errors=errors,
                policy=policy.entries, total=nodes.get(root, {}).get("size", 0))


def compare_snapshots(previous: dict, current: dict) -> list[dict]:
    if canonical(previous["root"]) != canonical(current["root"]) or previous.get("policy", []) != current.get("policy", []):
        raise ValueError("对比需要相同根目录与排除规则")
    if not previous.get("complete") or not current.get("complete"):
        raise ValueError("不完整扫描不能计算增长")
    old, new = previous["nodes"], current["nodes"]
    rows = [dict(path=p, before=old.get(p, {}).get("size", 0), after=new.get(p, {}).get("size", 0),
                 delta=new.get(p, {}).get("size", 0) - old.get(p, {}).get("size", 0)) for p in old.keys() | new.keys()]
    return sorted((row for row in rows if row["delta"]), key=lambda r: (-r["delta"], r["path"]))


def choose_duplicate_reference(paths: list[str], strategy="path", preferred="") -> tuple[list[str], int]:
    """Collapse hard-link aliases before choosing one physical copy to keep."""
    rows, seen = [], set()
    aliases = 0
    for path in sorted(paths, key=canonical):
        try:
            st = os.stat(path, follow_symlinks=False)
            identity = (st.st_dev, st.st_ino)
            if identity in seen:
                aliases += 1
                continue
            if link_like(path, st):
                continue
            seen.add(identity)
            rows.append((path, st.st_mtime_ns))
        except OSError:
            continue
    rows.sort(key=lambda item: ((0 if preferred and within(item[0], preferred) else 1) if strategy == "preferred" else 0,
                                -item[1] if strategy == "newest" else item[1] if strategy == "oldest" else 0, canonical(item[0])))
    return [row[0] for row in rows], aliases


def reclaimable_disk_bytes(path: str, api=None) -> int | None:
    """Account for sparse/compressed files and retained hard-link aliases."""
    try:
        st = os.stat(path, follow_symlinks=False)
        if not stat.S_ISREG(st.st_mode) or link_like(path, st):
            return None
        if st.st_nlink > 1:
            return 0
        if os.name != "nt":
            return getattr(st, "st_blocks", (st.st_size + 511) // 512) * 512
        import ctypes
        from ctypes import wintypes
        api = api or ctypes.WinDLL("kernel32.dll", use_last_error=True)
        api.GetCompressedFileSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
        api.GetCompressedFileSizeW.restype = wintypes.DWORD
        high = wintypes.DWORD()
        ctypes.set_last_error(0)
        low = api.GetCompressedFileSizeW(path, ctypes.byref(high))
        if low == 0xFFFFFFFF and ctypes.get_last_error():
            return None
        return (high.value << 32) | low
    except OSError:
        return None


def inspect_pack(rules: list, previous: list | None = None) -> dict:
    if not isinstance(rules, list) or len(rules) > 50000:
        raise ValueError("规则包必须是最多 50000 条规则的数组")
    warnings, normalized, targets = [], [], set()
    for index, rule in enumerate(rules):
        if not isinstance(rule, (list, tuple)) or len(rule) < 5 or not all(isinstance(rule[i], str) for i in (0, 1, 2, 4)):
            raise ValueError(f"第 {index + 1} 条规则格式错误")
        if rule[2] not in {"file", "dir", "glob"} or not rule[0].strip() or not rule[1].strip() or not isinstance(rule[3], bool) or "\x00" in rule[1]:
            raise ValueError(f"第 {index + 1} 条规则名称、类型或路径无效")
        pattern = rule[6] if len(rule) > 6 else ""
        if not isinstance(pattern, str):
            raise ValueError("匹配模式必须为字符串")
        target = (canonical(rule[1]), rule[2], pattern.lower())
        if target in targets:
            warnings.append(f"重复目标: {rule[0]} → {rule[1]}")
        targets.add(target)
        path = canonical(rule[1])
        sensitive = [os.environ.get(k, "") for k in ("USERPROFILE", "APPDATA", "LOCALAPPDATA", "WINDIR", "ProgramFiles", "ProgramFiles(x86)")]
        if os.path.dirname(path) == path or path in [canonical(p) for p in sensitive if p] or rule[1].strip() in {"%USERPROFILE%", "%APPDATA%", "%LOCALAPPDATA%", "%WINDIR%"}:
            warnings.append(f"范围过宽: {rule[0]} → {rule[1]}")
        if rule[2] == "glob" and pattern in {"", "*", "*.*"}:
            warnings.append(f"宽泛匹配: {rule[0]} → {pattern or '*'}")
        normalized.append(list(rule))
    def identity(rule):
        return (str(rule[0]), canonical(str(rule[1])), str(rule[2]), str(rule[6] if len(rule) > 6 else "").lower())
    before = {identity(r): list(r) for r in (previous or [])}
    after = {identity(r): list(r) for r in normalized}
    return dict(rules=normalized, warnings=warnings, added=[after[k] for k in after.keys() - before.keys()],
                removed=[before[k] for k in before.keys() - after.keys()],
                changed=[after[k] for k in before.keys() & after.keys() if before[k] != after[k]],
                digest=hashlib.sha256(json.dumps(normalized, ensure_ascii=False, sort_keys=True).encode()).hexdigest())


DEFAULT_AUTO_POLICY = dict(enabled=False, drive="C:\\", free_below_gb=10, idle_minutes=10, cooldown_minutes=360,
                           skip_processes=["chrome.exe", "msedge.exe", "Code.exe", "Docker Desktop.exe"], notify=True)


def idle_seconds() -> float:
    if os.name != "nt":
        return 0.0
    import ctypes
    from ctypes import wintypes
    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]
    value = LASTINPUTINFO()
    value.cbSize = ctypes.sizeof(value)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(value)):
        raise OSError("无法读取用户空闲时间")
    return ((ctypes.windll.kernel32.GetTickCount() - value.dwTime) & 0xFFFFFFFF) / 1000


def command(args: list[str], timeout=60) -> str:
    result = subprocess.run(args, capture_output=True, timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    def decode(value):
        if b"\x00" in value[:100]:
            return value.decode("utf-16-le", errors="replace").strip().lstrip("\ufeff")
        try:
            return value.decode("utf-8").strip()
        except UnicodeDecodeError:
            import ctypes
            encoding = f"cp{ctypes.windll.kernel32.GetOEMCP()}" if os.name == "nt" else "utf-8"
            return value.decode(encoding, errors="replace").strip()
    output = decode(result.stdout)
    errors = decode(result.stderr)
    if result.returncode:
        raise RuntimeError(errors or output or f"命令退出码 {result.returncode}")
    return output


def recorded_command(args: list[str], kind: str, timeout=1200) -> str:
    store = _store
    if store is None:
        raise RuntimeError("维护存储尚未初始化")
    with JobLock(store.directory):
        payload = dict(command=args, state="running", started=time.time())
        item_id = store.record(kind, payload)
        try:
            output = command(args, timeout)
            payload.update(output=output, state="finished", success=1)
            return output
        except Exception as exc:
            payload.update(state="failed", failed=1, error=str(exc))
            raise
        finally:
            payload["finished"] = time.time()
            with store.connect() as db:
                db.execute("UPDATE history SET payload=? WHERE id=?", (json.dumps(payload, ensure_ascii=False), item_id))


def running_processes() -> set[str]:
    if os.name != "nt":
        return set()
    return {row[0].lower() for row in csv.reader(io.StringIO(command(["tasklist", "/fo", "csv", "/nh"]))) if row}


def notify_maintenance(message: str) -> None:
    import base64
    encoded = base64.b64encode(message.encode("utf-8")).decode("ascii")
    script = (
        "$ErrorActionPreference='Stop'; "
        "[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime] > $null; "
        "[Windows.Data.Xml.Dom.XmlDocument,Windows.Data.Xml.Dom.XmlDocument,ContentType=WindowsRuntime] > $null; "
        f"$message=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded}')); "
        "$xml=New-Object Windows.Data.Xml.Dom.XmlDocument; "
        "$xml.LoadXml('<toast><visual><binding template=\"ToastGeneric\"><text>C Cleaner Plus</text><text>'"
        "+[Security.SecurityElement]::Escape($message)+'</text></binding></visual></toast>'); "
        "$toast=[Windows.UI.Notifications.ToastNotification]::new($xml); "
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('C Cleaner Plus').Show($toast)"
    )
    payload = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    command(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", payload], timeout=30)


def check_auto_policy(policy: dict, last_run=0.0, free=None, idle=None, processes=None, now=None) -> tuple[bool, str]:
    if not policy.get("enabled"):
        return True, "条件式维护未启用，按原计划执行"
    now = time.time() if now is None else now
    if now - last_run < max(0, float(policy.get("cooldown_minutes", 360))) * 60:
        return False, "仍处于执行冷却时间"
    free = shutil.disk_usage(policy.get("drive", "C:\\")).free if free is None else free
    if free >= float(policy.get("free_below_gb", 10)) * 1024**3:
        return False, "剩余空间高于触发阈值"
    idle = idle_seconds() if idle is None else idle
    if idle < float(policy.get("idle_minutes", 10)) * 60:
        return False, "用户空闲时间未达到条件"
    processes = running_processes() if processes is None else processes
    blocked = {p.lower() for p in policy.get("skip_processes", [])} & {p.lower() for p in processes}
    if blocked:
        return False, "运行中应用: " + ", ".join(sorted(blocked))
    return True, "空间、空闲时间及应用条件均满足"


class JobLock:
    """OS-held lock, automatically released after a crash."""
    def __init__(self, directory: str):
        self.path = os.path.join(directory, "maintenance-job.lock")
        self.stream = None
        self.reentrant = False

    def __enter__(self):
        if getattr(_local, "lock_path", None) == canonical(self.path):
            self.reentrant = True
            return self
        self.stream = os.fdopen(os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600), "r+b")
        try:
            self.stream.seek(0)
            if os.name == "nt":
                import msvcrt
                if os.fstat(self.stream.fileno()).st_size == 0:
                    self.stream.write(b"0")
                    self.stream.flush()
                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.stream.close()
            self.stream = None
            raise RuntimeError("已有维护任务正在执行") from None
        _local.lock_path = canonical(self.path)
        return self

    def __exit__(self, *args):
        if self.stream:
            self.stream.close()
            _local.lock_path = None


DEVELOPMENT_DIRS = {"node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".next", ".nuxt", ".gradle"}


def scan_development(root: str, stop=None, max_dirs=100000) -> list[dict]:
    root = canonical(root)
    if not os.path.isdir(root) or link_like(root):
        raise ValueError("请选择普通项目目录")
    policy = Policy()
    stack, rows, count = [root], [], 0
    while stack:
        if stop is not None and stop.is_set():
            break
        parent = stack.pop()
        count += 1
        if count > max_dirs:
            raise ValueError("项目目录过多，请缩小扫描范围")
        if policy.excluded(parent) or link_like(parent):
            continue
        with os.scandir(parent) as entries:
            for entry in entries:
                if not entry.is_dir(follow_symlinks=False) or link_like(entry.path) or policy.excluded(entry.path) or entry.name == ".git":
                    continue
                # Generic build/dist/target directories need a project marker.
                marked = any(os.path.isfile(os.path.join(parent, marker)) for marker in ("package.json", "pyproject.toml", "Cargo.toml", "pom.xml", "build.gradle", "CMakeLists.txt"))
                if entry.name in DEVELOPMENT_DIRS or (marked and entry.name in {"build", "dist", "target"}):
                    usage = scan_usage(entry.path, stop=stop)
                    rows.append(dict(project=parent, path=entry.path, category=entry.name, size=usage["total"], complete=usage["complete"]))
                else:
                    stack.append(entry.path)
    return sorted(rows, key=lambda row: -row["size"])


def wsl_disks() -> list[dict]:
    rows = []
    if os.name == "nt":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Lxss") as root:
                for index in range(winreg.QueryInfoKey(root)[0]):
                    with winreg.OpenKey(root, winreg.EnumKey(root, index)) as key:
                        base = winreg.QueryValueEx(key, "BasePath")[0]
                        name = winreg.QueryValueEx(key, "DistributionName")[0]
                        path = os.path.join(base, "ext4.vhdx")
                        if os.path.isfile(path):
                            rows.append(dict(name=name, path=canonical(path), size=os.path.getsize(path)))
        except OSError:
            pass
    docker_base = os.path.expandvars(r"%LOCALAPPDATA%\Docker\wsl")
    if os.path.isdir(docker_base):
        for parent, dirs, files in os.walk(docker_base, followlinks=False):
            dirs[:] = [d for d in dirs if not link_like(os.path.join(parent, d))]
            for filename in files:
                path = os.path.join(parent, filename)
                if filename.lower().endswith(".vhdx") and not link_like(path):
                    rows.append(dict(name="Docker WSL", path=canonical(path), size=os.path.getsize(path)))
    return list({row["path"]: row for row in rows}.values())


def compact_wsl_disk(path: str) -> str:
    path = canonical(path)
    if path not in {row["path"] for row in wsl_disks()} or '"' in path or "\n" in path or "\r" in path:
        raise ValueError("只能压缩已识别的 WSL/Docker 虚拟磁盘")
    if Policy().deletion_reason(path):
        raise ValueError("该虚拟磁盘受排除规则保护")
    if any("docker" in p for p in running_processes()):
        raise ValueError("请先完全退出 Docker Desktop，再压缩虚拟磁盘")
    before = os.path.getsize(path)
    command(["wsl.exe", "--shutdown"], timeout=90)
    try:
        _compact_vhdx_native(path)
        after = os.path.getsize(path)
        result = dict(path=path, before=before, after=after, success=1, state="finished")
        if _store:
            _store.record("WSL 磁盘压缩", result)
        return f"压缩完成。执行前 {before} 字节，执行后 {after} 字节，减少 {max(0, before-after)} 字节。"
    except Exception as exc:
        if _store:
            _store.record("WSL 磁盘压缩", dict(path=path, state="failed", failed=1, error=str(exc)))
        raise


def _compact_vhdx_native(path: str, api=None, kernel=None) -> None:
    """Use native status codes instead of parsing localized DiskPart output.

    https://learn.microsoft.com/windows/win32/api/virtdisk/nf-virtdisk-compactvirtualdisk
    Version-2 opens request no explicit access mask, as in Microsoft's VHDX sample.
    """
    import ctypes
    from ctypes import wintypes

    class StorageType(ctypes.Structure):
        _fields_ = [("DeviceId", wintypes.ULONG), ("VendorId", ctypes.c_ubyte * 16)]

    class OpenParameters(ctypes.Structure):
        _fields_ = [("Version", ctypes.c_int), ("GetInfoOnly", wintypes.BOOL),
                    ("ReadOnly", wintypes.BOOL), ("ResiliencyGuid", ctypes.c_ubyte * 16)]

    class CompactParameters(ctypes.Structure):
        _fields_ = [("Version", ctypes.c_int), ("Reserved", wintypes.ULONG)]

    api = api or ctypes.WinDLL("virtdisk.dll", use_last_error=True)
    kernel = kernel or ctypes.WinDLL("kernel32.dll", use_last_error=True)
    api.OpenVirtualDisk.argtypes = [ctypes.POINTER(StorageType), wintypes.LPCWSTR, ctypes.c_int,
                                   ctypes.c_int, ctypes.POINTER(OpenParameters), ctypes.POINTER(wintypes.HANDLE)]
    api.OpenVirtualDisk.restype = wintypes.DWORD
    api.CompactVirtualDisk.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.POINTER(CompactParameters), ctypes.c_void_p]
    api.CompactVirtualDisk.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    storage, parameters, handle = StorageType(), OpenParameters(), wintypes.HANDLE()
    parameters.Version = 2
    code = api.OpenVirtualDisk(ctypes.byref(storage), path, 0, 0, ctypes.byref(parameters), ctypes.byref(handle))
    if code:
        raise ctypes.WinError(code)
    try:
        compact = CompactParameters()
        compact.Version = 1
        code = api.CompactVirtualDisk(handle, 0, ctypes.byref(compact), None)
        if code:
            raise ctypes.WinError(code)
    finally:
        kernel.CloseHandle(handle)
