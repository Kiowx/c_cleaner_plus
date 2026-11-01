"""Explainable, read-only storage checkup and reviewed cleanup plans."""
from __future__ import annotations

import fnmatch
import os
import re
import stat
import threading
import time
import uuid
from collections import defaultdict

import cleaner_core as core


# Only these built-in caches are recommended by default. Names from imported
# rules never grant automatic trust, even if they contain the word "cache".
QUICK_RULES = {
    "用户临时文件", "系统临时文件", "缩略图缓存", "Edge Cache", "Edge Code", "Chrome Cache", "Chrome Code",
    "pip Cache", "NuGet Cache", "npm Cache", "Yarn Cache", "Go Build Cache", "Cargo Cache",
}
DOWNLOAD_EXTENSIONS = {".exe", ".msi", ".msix", ".zip", ".7z", ".rar", ".iso", ".crdownload", ".part", ".tmp"}


def application_name(name: str, path: str = "") -> str:
    text = name.lower()
    for tokens, label in ((('edge',), 'Microsoft Edge'), (('chrome',), 'Google Chrome'),
                          (('wechat', 'weixin', '微信'), '微信'), (('tencent files', 'qq'), 'QQ'),
                          (('steam',), 'Steam'),
                          (('pip',), 'Python / pip'), (('npm', 'yarn', 'pnpm'), 'Node.js'),
                          (('nuget',), '.NET / NuGet'), (('cargo',), 'Rust / Cargo'),
                          (('go build', 'go-build'), 'Go'), (('ollama',), 'Ollama'),
                          (('docker',), 'Docker Desktop')):
        if any(re.search(r'(?<![a-z0-9])'+re.escape(token)+r'(?![a-z0-9])', text) for token in tokens):
            return label
    if re.search(r'\b(?:visual studio code|vscode|vscodium|code(?: - insiders)?)\b', text):
        return 'Visual Studio Code'
    # Avoid guessing ownership from unrelated path fragments.
    return name or os.path.basename(path)


def contains(path, root):
    # Inputs are canonical paths; avoid repeated filesystem normalization in
    # the file-to-rule and file-to-software accounting loops.
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def cache_impact(rule) -> str:
    name = str(rule[0]).lower()
    if any(token in name for token in ('chrome', 'edge')):
        return "浏览器会重新生成缓存，首次打开网页可能变慢；不包含密码和书签。"
    if any(token in name for token in ('pip', 'npm', 'yarn', 'nuget', 'cargo', 'go build')):
        return "依赖或构建缓存可重新生成，后续安装、构建可能重新下载或编译。"
    if '缩略图' in name:
        return "缩略图会重新生成，首次浏览图片文件夹可能变慢。"
    return "临时内容可能仍被程序占用；默认保留最近 7 天修改的文件。"


def scan_checkup(rules, builtins, installed, data_roots, download_roots, scopes, *, stop=None,
                 progress=None, policy=None, conditions=None, protected=None, max_files=200000,
                 max_entries=500000, large_threshold=200 * 1024**2, now=None):
    """Scan bounded known locations once, preserving evidence for every action.

    ``data_roots`` contain (display_name, path, group_name) tuples. They are
    independent of cleanup rules and always yield review-only suggestions.
    No payload is opened; file identity, size and modification time use lstat.
    """
    now = time.time() if now is None else now
    policy = policy or core.Policy()
    scopes = [core.canonical(p) for p in scopes if os.path.isdir(p)]
    if not scopes:
        raise ValueError("请选择存在的扫描磁盘或目录")
    store = core.current_store()
    conditions = conditions if conditions is not None else (store.get('conditions', {}) if store else {})
    builtin_ids = {core.rule_id(rule) for rule in builtins}
    files, visited, errors, pending_rules = {}, set(), [], []
    state = dict(complete=True, examined=0, skipped=0, issues=0)
    incomplete_locations = set()
    last_progress = 0.0

    def cancelled():
        return stop is not None and stop.is_set()

    def in_scope(path):
        return any(contains(path, scope) for scope in scopes)

    def eligible(path):
        return in_scope(path) and not policy.excluded(path)

    def emit(label, force=False):
        nonlocal last_progress
        tick = time.monotonic()
        if progress and (force or tick-last_progress > .15):
            progress(label, len(files))
            last_progress = tick

    def note(message):
        state['complete'] = False
        state['issues'] += 1
        if len(errors) < 50:
            errors.append(message)

    def visit(path):
        """Skip reparse points at every level and bound traversal globally."""
        path = core.canonical(path)
        source = path
        issue_count = state['issues']
        try:
            if core.has_link_ancestor(path):
                state['skipped'] += 1
                return False
        except OSError as exc:
            note(f"无法检查父目录 {path}: {exc}")
            return False
        stack = [path]
        while stack:
            if cancelled():
                state['complete'] = False
                break
            if state['examined'] >= max_entries or len(files) >= max_files:
                note("达到本次扫描上限，当前结果为部分统计；可缩小扫描范围后重试。")
                break
            path = stack.pop()
            if path in visited or not eligible(path):
                continue
            visited.add(path)
            state['examined'] += 1
            try:
                st = os.lstat(path)
                if core.link_like(path, st):
                    state['skipped'] += 1
                    continue
                if stat.S_ISREG(st.st_mode):
                    files[path] = dict(path=path, size=st.st_size, mtime=st.st_mtime, signature=core.signature(st),
                                       identity=(st.st_dev, st.st_ino), nlink=st.st_nlink)
                elif stat.S_ISDIR(st.st_mode):
                    with os.scandir(path) as entries:
                        for entry in entries:
                            if cancelled():
                                state['complete'] = False
                                break
                            if state['examined'] + len(stack) >= max_entries:
                                note("达到目录扫描上限，当前结果为部分统计。")
                                break
                            stack.append(core.canonical(entry.path))
                emit("正在统计文件占用")
            except FileNotFoundError:
                # Non-existent rule paths are normal. Files disappearing during
                # traversal make the estimate partial, not a cleanup failure.
                note("扫描期间文件已变化: " + path)
            except OSError as exc:
                note(f"无法完整读取 {path}: {exc}")
                incomplete_locations.add(path)
        complete = not cancelled() and state['issues']==issue_count and not any(contains(p, source) for p in incomplete_locations)
        if not complete:
            incomplete_locations.add(source)
        return complete

    known_rules = {}
    for rule in list(builtins) + list(rules):
        if isinstance(rule, (tuple, list)) and len(rule) >= 5:
            key = core.rule_id(rule)
            # Active custom rules retain their saved flags; built-ins scanned
            # for recommendations need not be checked on the manual page.
            known_rules[key] = tuple(rule)
    for key, rule in known_rules.items():
        if cancelled():
            break
        path = core.canonical(str(rule[1]))
        if rule[2] not in {'dir', 'file', 'glob'} or not eligible(path) or not os.path.lexists(path):
            continue
        if protected and protected(path):
            continue
        if key not in builtin_ids and not bool(rule[3]):
            continue
        emit("正在检查 " + str(rule[0]), True)
        source_complete = visit(path)
        pending_rules.append((key, rule, path, source_complete))

    folders = []
    data_roots = list(data_roots)
    for scope in scopes:
        if os.path.dirname(scope) != scope and not any(contains(scope, core.canonical(row[1])) for row in data_roots):
            data_roots.append((os.path.basename(scope), scope, '所选目录'))
    for name, path, owner in data_roots:
        path = core.canonical(path)
        if eligible(path) and os.path.isdir(path) and not policy.excluded(path):
            emit("正在分析 " + name, True)
            visit(path)
            folders.append(dict(name=name, path=path, owner=owner, kind='data'))
    for app in installed:
        if cancelled():
            break
        path = app.get('location', '')
        if not path:
            continue
        path = core.canonical(path)
        # Registry hints may point to Program Files itself. Never attribute a
        # shared program root to one app or offer it for migration.
        shared_roots = {core.canonical(os.environ[k]) for k in ('ProgramFiles', 'ProgramFiles(x86)', 'ProgramData',
                       'USERPROFILE', 'LOCALAPPDATA', 'APPDATA', 'WINDIR') if os.environ.get(k)}
        if path in shared_roots or os.path.dirname(path) == path or not eligible(path) or not os.path.isdir(path):
            continue
        owner = application_name(str(app.get('name', '')), path)
        emit("正在统计软件 " + owner, True)
        visit(path)
        folders.append(dict(name=app.get('name', owner), path=path, owner=owner, kind='install',
                            app_name=app.get('name', ''), reg=app.get('reg', '')))
    downloads = []
    for root in download_roots:
        root = core.canonical(root)
        if not eligible(root) or not os.path.isdir(root):
            continue
        emit("正在检查下载残留", True)
        visit(root)
        downloads.append(root)

    items = list(files.values())
    recommendations, assigned = [], set()
    # A file is offered for cleanup at most once even if rules overlap.
    for key, rule, path, source_complete in pending_rules:
        matches = []
        for item in items:
            p = item['path']
            if rule[2] == 'file':
                matched = p == path
            else:
                matched = contains(p, path)
                if matched and rule[2] == 'glob':
                    first = os.path.relpath(p, path).split(os.sep)[0]
                    pattern = str(rule[6] if len(rule) > 6 and rule[6] else '*')
                    matched = fnmatch.fnmatchcase(first.lower(), pattern.lower())
            if matched:
                matches.append(item)
        trusted = key in builtin_ids and str(rule[0]) in QUICK_RULES
        cond = core.validate_conditions(conditions.get(key, {}))
        if trusted and '临时文件' in str(rule[0]):
            cond['min_age_days'] = max(cond['min_age_days'], 7)
        matches.sort(key=lambda f: (-f['mtime'], f['path']))
        candidates = []
        for i, item in enumerate(matches):
            if item['path'] in assigned or i < cond['keep_latest'] or item['size'] < cond['min_size_bytes']:
                continue
            if cond['min_age_days'] and now-item['mtime'] < cond['min_age_days']*86400:
                continue
            if protected and protected(item['path']):
                continue
            candidates.append(dict(item, kind='file', rule=str(rule[0]), selected=True))
            assigned.add(item['path'])
        if not candidates:
            continue
        owner = application_name(str(rule[0]), path)
        safe = trusted and source_complete
        recommendations.append(dict(id=key, name=str(rule[0]), path=path, owner=owner, category='cache',
                                    size=unique_size(candidates), count=len(candidates), cleanable=True, recommended=safe,
                                    selected=safe, impact=cache_impact(rule) if trusted else
                                    "此项目来自可选或自定义规则，请核对文件清单后决定；默认不勾选。",
                                    reason='内置缓存规则' if trusted else '已启用的自定义规则' if key not in builtin_ids else '可选清理规则',
                                    files=candidates))

    for root in downloads:
        candidates = [dict(item, kind='file', rule='旧下载文件', selected=True) for item in items
                      if contains(item['path'], root) and item['path'] not in assigned
                      and os.path.dirname(item['path']) == root
                      and os.path.splitext(item['path'])[1].lower() in DOWNLOAD_EXTENSIONS
                      and now-item['mtime'] >= 30*86400 and not (protected and protected(item['path']))]
        if candidates:
            assigned.update(f['path'] for f in candidates)
            recommendations.append(dict(id=uuid.uuid4().hex, name='超过 30 天的安装包和压缩包', path=root, owner='下载目录',
                 category='download', size=unique_size(candidates), count=len(candidates), cleanable=True,
                 recommended=False, selected=False, reason='文件类型与修改时间',
                 impact='可能是仍需保留的安装包或资料，请先查看内容；默认不勾选。', files=candidates))

    # Directory ownership is derived from the most specific known location.
    owners = defaultdict(lambda: dict(paths=set(), identities={}, cache=0, install=0, data=0, app_name='', install_path=''))
    owner_locations = []
    for folder in folders:
        owner_locations.append((folder['path'], folder['owner'], folder['kind'], folder))
    for _key, rule, path, _complete in pending_rules:
        owner_locations.append((path, application_name(str(rule[0]), path), 'cache', {}))
    owner_locations.sort(key=lambda row: (-len(row[0]), row[0]))
    for item in items:
        for path, owner, kind, info in owner_locations:
            if contains(item['path'], path):
                group = owners[owner]
                identity = item['identity']
                if identity not in group['identities']:
                    group['identities'][identity] = item['size']
                    group[kind] += item['size']
                group['paths'].add(path)
                if kind == 'install':
                    group['app_name'], group['install_path'] = info.get('app_name', ''), path
                break
    ranking = [dict(name=name, size=sum(data['identities'].values()), cache=data['cache'], data=data['data'],
                    install=data['install'], paths=sorted(data['paths']), app_name=data['app_name'],
                    path=data['install_path'] or next(iter(sorted(data['paths'])), '')) for name, data in owners.items()]
    ranking.sort(key=lambda row: (-row['size'], row['name']))

    for folder in sorted(folders, key=lambda row: row['path']):
        contained = [f for f in items if contains(f['path'], folder['path'])]
        total = unique_size(contained)
        if total >= large_threshold:
            recommendations.append(dict(id=uuid.uuid4().hex, name=folder['name'], path=folder['path'], owner=folder['owner'],
                category=folder['kind'], size=total, count=len(contained), cleanable=False, recommended=False, selected=False,
                app_name=folder.get('app_name', ''), reason='目录实际占用统计',
                impact='这是软件安装内容，请通过标准卸载释放空间。' if folder['kind']=='install' else
                '可能包含个人数据、聊天记录或模型，不参与一键清理；可查看内容或使用迁移工具。', files=[]))
    recommendations.sort(key=lambda row: (not row['recommended'], not row['cleanable'], -row['size'], row['name']))
    if cancelled():
        state['complete'] = False
        for row in recommendations:
            row['selected'] = row['recommended'] = False
    emit("扫描结束", True)
    return dict(id=uuid.uuid4().hex, created=now, scopes=scopes, rows=recommendations, apps=ranking,
                total=unique_size(items), file_count=len(items), errors=errors, complete=state['complete'], cancelled=cancelled(),
                recommended_bytes=unique_size([f for row in recommendations if row['recommended'] for f in row['files']]),
                skipped=state['skipped'], source_count=len(pending_rules)+len(folders)+len(downloads))


def unique_size(items):
    seen, total = set(), 0
    for item in items:
        identity = tuple(item.get('identity', item.get('signature', [])[:2])) or item['path']
        if identity not in seen:
            total += item['size']
            seen.add(identity)
    return total


def reviewed_plan(rows):
    files, seen = [], set()
    for row in rows:
        if not row.get('cleanable'):
            continue
        for item in row.get('files', []):
            path = core.canonical(item['path'])
            if path in seen:
                continue
            seen.add(path)
            files.append(dict(item, selected=True))
    return dict(id=uuid.uuid4().hex, created=time.time(), items=files, errors=[], omitted=0, complete=True)


def retry_plan(plan, result):
    failed = {core.canonical(item['path']) for item in result.get('items', []) if item['status']=='failed'}
    return dict(plan, id=uuid.uuid4().hex, items=[dict(item, selected=True) for item in plan['items']
                                              if core.canonical(item['path']) in failed])


def default_data_roots(environ=None):
    environ = os.environ if environ is None else environ
    roots = []
    for key in ('LOCALAPPDATA', 'APPDATA'):
        parent = environ.get(key, '')
        if not parent or not os.path.isdir(parent):
            continue
        try:
            with os.scandir(parent) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False) and entry.name.lower() not in {'temp', 'packages', 'application data'}:
                        roots.append((entry.name, entry.path, application_name(entry.name)))
                        # Attribute browser profiles/chat data below shared
                        # vendor folders to their application where known.
                        for child in {'microsoft': ('Edge', 'Teams', 'OneDrive'), 'google': ('Chrome',),
                                      'tencent': ('WeChat', 'Weixin', 'QQ')}.get(entry.name.lower(), ()):
                            child_path = os.path.join(entry.path, child)
                            if os.path.isdir(child_path):
                                roots.append((child, child_path, application_name(child)))
        except OSError:
            continue
    user = environ.get('USERPROFILE', '')
    if user:
        for directory in ('Desktop', 'Documents', 'Downloads', 'Videos', 'Pictures'):
            path = os.path.join(user, directory)
            if os.path.isdir(path):
                roots.append((directory, path, '个人文件 / '+directory))
    return roots
