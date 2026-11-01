"""One-click checkup: findings first, settings remain in their own tools."""
from __future__ import annotations

import json
import os
import shutil
import threading
from pathlib import Path

from PySide6.QtCore import Signal, Qt, QTimer
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QDialog, QDialogButtonBox,
                              QStackedWidget, QFileDialog, QLayout, QSizePolicy)
from qfluentwidgets import (ScrollArea, StrongBodyLabel, BodyLabel, CaptionLabel,
                           PrimaryPushButton, PushButton, ComboBox, CheckBox, ProgressBar,
                           CardWidget, TextEdit)

import cleaner_core as core
import health_check as health
from maintenance_ui import PreviewDialog, MaintenancePage, button, hint, table, size, selected_row


class ToolDialog(QDialog):
    def __init__(self, window, host, sections):
        super().__init__(window)
        self.resize(1100, 800)
        self.setWindowTitle("清理工具与设置")
        layout = QVBoxLayout(self)
        self.page = MaintenancePage(window, host, self, sections=sections)
        layout.addWidget(self.page)
        row = QHBoxLayout()
        row.addStretch()
        button(row, "关闭", self.reject)
        layout.addLayout(row)
        window.apply_language_to_widget(self)

    def done(self, result):
        if self.page.busy:
            self.page.stop.set()
            self.page.status.setText("正在停止扫描或等待系统维护完成，请稍后关闭")
            return
        super().done(result)


def open_tool(window, host, sections):
    dialog = ToolDialog(window, host, sections)
    # Keep a reference for shutdown/configuration-directory guards.
    window._active_tool_dialog = dialog
    try:
        return dialog.exec()
    finally:
        window._active_tool_dialog = None
        dialog.deleteLater()


class FindingCard(CardWidget):
    def __init__(self, finding, page):
        super().__init__(page)
        self.finding, self.page = finding, page
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.setMinimumHeight(180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        top = QHBoxLayout()
        self.check = CheckBox()
        self.check.setChecked(finding['selected'])
        self.check.setEnabled(finding['cleanable'])
        self.check.toggled.connect(self.on_checked)
        top.addWidget(self.check)
        top.addWidget(StrongBodyLabel(finding['name']), 1)
        top.addWidget(StrongBodyLabel(size(finding['size'])))
        layout.addLayout(top)
        label = "推荐清理" if finding['recommended'] else "需要确认" if finding['cleanable'] else "查看后处理"
        hint(layout, page.tr_format("{label} · {owner} · {count} 个文件 · {reason}",
             label=page.tr(label), owner=finding['owner'], count=finding['count'], reason=page.tr(finding['reason'])))
        hint(layout, finding['impact'])
        path = CaptionLabel()
        path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        path.setText(finding['path'])
        path.setWordWrap(True)
        layout.addWidget(path)
        actions = QHBoxLayout()
        if finding['cleanable']:
            button(actions, "预览并清理", lambda: page.preview_rows([finding]))
        button(actions, "查看文件", lambda: page.host.open_explorer(finding['path']))
        if finding['category'] == 'install':
            button(actions, "前往卸载", lambda: page.open_uninstall(finding.get('app_name') or finding['owner']))
        elif not finding['cleanable']:
            button(actions, "迁移此目录", lambda: page.open_migration(finding['path']))
        button(actions, "不再扫描此路径", self.exclude)
        actions.addStretch()
        layout.addLayout(actions)

    def on_checked(self, checked):
        self.finding['selected'] = checked
        self.page.update_selection()

    def exclude(self):
        if self.page.busy:
            return
        store = core.current_store()
        entries = store.get('exclusions', [])
        if self.finding['path'] not in entries:
            store.set('exclusions', entries+[self.finding['path']])
        self.check.setChecked(False)
        self.check.setEnabled(False)
        self.finding['cleanable'] = False
        self.page.status.setText("已加入排除，后续扫描和清理将跳过此路径")


class CheckupPage(ScrollArea):
    finished = Signal(str, object, str)
    scanProgress = Signal(str, int)
    cleanProgress = Signal(int, int)

    def __init__(self, window, host, parent=None):
        super().__init__(parent)
        self.main_window, self.host = window, host
        self.stop = threading.Event()
        self.busy = False
        self.report = None
        self.last_plan = self.last_result = None
        self.cards = []
        self.setObjectName('checkupPage')
        self.setWidgetResizable(True)
        self.enableTransparentBackground()
        self.view = QWidget()
        self.setWidget(self.view)
        root = QVBoxLayout(self.view)
        root.setContentsMargins(0, 0, 12, 0)
        root.setSpacing(12)
        hint(root, "扫描常见缓存、下载残留和软件数据，告诉你空间用在哪里，以及每一项可以怎样处理。")
        hero = CardWidget(self.view)
        hero_layout = QVBoxLayout(hero)
        hero_layout.setContentsMargins(18, 16, 18, 16)
        controls = QHBoxLayout()
        controls.addWidget(BodyLabel("扫描范围"))
        self.scope = ComboBox()
        self.scope_paths = list(host.get_available_drives())
        if not self.scope_paths:
            self.scope_paths = [os.path.abspath(os.sep)]
        self.scope.addItems(self.scope_paths)
        self.scope.currentIndexChanged.connect(self.update_disk)
        controls.addWidget(self.scope)
        button(controls, "选择目录", self.choose_scope)
        self.scan_button = button(controls, "开始体检", self.start_scan, True)
        self.stop_button = button(controls, "停止", self.stop.set)
        self.stop_button.setEnabled(False)
        controls.addStretch()
        hero_layout.addLayout(controls)
        self.disk_label = hint(hero_layout, "")
        self.summary = StrongBodyLabel("先体检，再决定清理什么")
        self.summary.setWordWrap(True)
        hero_layout.addWidget(self.summary)
        self.metrics = hint(hero_layout, "缓存、个人数据和软件安装内容会分别展示，个人文件默认不勾选。")
        self.progress = ProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        hero_layout.addWidget(self.progress)
        root.addWidget(hero)
        toolbar = QHBoxLayout()
        self.mode = ComboBox()
        self.mode.addItems(["清理建议", "软件占用排行"])
        self.mode.currentIndexChanged.connect(self.switch_mode)
        toolbar.addWidget(self.mode)
        self.filters = ComboBox()
        self.filters.addItems(["全部建议", "推荐清理", "需要确认", "大目录与软件"])
        self.filters.currentIndexChanged.connect(self.render_findings)
        toolbar.addWidget(self.filters)
        self.select_button = button(toolbar, "只勾选推荐项", self.select_recommended)
        button(toolbar, "清理历史", lambda: open_tool(window, host, [0]))
        button(toolbar, "导出体检报告", self.export_report)
        root.addLayout(toolbar)
        self.stack = QStackedWidget()
        self.stack.setMinimumHeight(350)
        self.findings = QWidget()
        self.finding_layout = QVBoxLayout(self.findings)
        self.finding_layout.setContentsMargins(0, 0, 0, 0)
        self.finding_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.empty = hint(self.finding_layout, "点击“开始体检”，扫描完成后会在这里显示具体建议。")
        self.finding_layout.addStretch()
        self.finding_scroll = ScrollArea()
        self.finding_scroll.setWidget(self.findings)
        self.finding_scroll.setWidgetResizable(True)
        self.finding_scroll.enableTransparentBackground()
        self.stack.addWidget(self.finding_scroll)
        app_page = QWidget()
        apps = QVBoxLayout(app_page)
        hint(apps, "按已识别的软件目录汇总安装内容、缓存和数据。仅统计扫描范围内的位置，排名不等同于可清理空间。")
        self.app_view, self.app_model = table(apps, [('name','软件 / 数据归属',None), ('size','总占用',size),
                       ('cache','缓存',size), ('data','数据',size), ('install','安装内容',size)])
        self.app_view.setWordWrap(False)
        self.app_view.setColumnWidth(0, 230)
        self.app_details = hint(apps, "选中软件可查看统计路径或前往处理。")
        self.app_view.selectionModel().currentRowChanged.connect(self.show_app)
        app_actions = QHBoxLayout()
        button(app_actions, "查看占用目录", self.open_app_path)
        button(app_actions, "查看相关清理建议", self.show_app_findings)
        button(app_actions, "前往卸载", self.uninstall_selected)
        apps.addLayout(app_actions)
        self.stack.addWidget(app_page)
        root.addWidget(self.stack, 1)
        actions = QHBoxLayout()
        self.selection = CaptionLabel("尚未扫描")
        actions.addWidget(self.selection, 1)
        self.retry_button = button(actions, "重试失败项", self.retry_failed)
        self.retry_button.setEnabled(False)
        self.clean_button = button(actions, "预览并清理勾选项", self.clean_selected, True)
        self.clean_button.setEnabled(False)
        root.addLayout(actions)
        self.status = hint(root, "扫描为只读操作，清理前可逐项确认。")
        self.result_detail = TextEdit()
        self.result_detail.setReadOnly(True)
        self.result_detail.setMaximumHeight(150)
        self.result_detail.hide()
        root.addWidget(self.result_detail)
        self.finished.connect(self.on_finished)
        self.scanProgress.connect(lambda text, count: self.status.setText(f"{text} · 已统计 {count} 个文件"))
        self.cleanProgress.connect(lambda value, total: self.progress.setValue(int(value*100/max(1,total))))
        self.update_disk()

    def update_disk(self, *args):
        path = self.scope_paths[self.scope.currentIndex()]
        try:
            usage = shutil.disk_usage(path)
            self.disk_label.setText(self.tr_format("{path}  可用 {free} / 总计 {total}", path=path, free=size(usage.free),total=size(usage.total)))
        except OSError as exc:
            self.disk_label.setText(str(exc))

    def tr(self, text):
        return self.main_window.tr_text(text) if hasattr(self.main_window, 'tr_text') else text

    def tr_format(self, template, **values):
        return self.tr(template).format(**values)

    def choose_scope(self):
        if self.busy:
            return
        path = QFileDialog.getExistingDirectory(self, "选择体检目录")
        if path:
            self.scope_paths.append(path)
            self.scope.addItem(path)
            self.scope.setCurrentIndex(len(self.scope_paths)-1)

    def run(self, key, callback):
        if self.busy:
            return False
        self.busy = True
        self.stop.clear()
        self.scan_button.setEnabled(False)
        self.scope.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.clean_button.setEnabled(False)
        self.select_button.setEnabled(False)
        self.retry_button.setEnabled(False)
        self.progress.setRange(0, 0) if key == 'scan' else self.progress.setRange(0, 100)
        self.result_detail.hide()
        self.status.setText("正在体检…" if key=='scan' else "正在处理文件…")
        def worker():
            try:
                result, error = callback(), ''
            except Exception as exc:
                result, error = None, str(exc)
            self.finished.emit(key, result, error)
        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()
        return True

    def start_scan(self):
        if self.busy:
            return
        self.main_window.pg_clean._sync()
        with self.main_window._targets_lock:
            rules = list(self.main_window.targets)
        builtins = [self.host.parse_rule_entry(rule) for rule in self.host.default_clean_targets()]
        builtins = [rule for rule in builtins if rule]
        deleted = getattr(self.main_window, 'deleted_builtin_rule_keys', set())
        builtins = [rule for rule in builtins if self.host.make_rule_key(rule[0], rule[1], rule[2], rule[6]) not in deleted]
        scope = [self.scope_paths[self.scope.currentIndex()]]
        policy = core.Policy()
        conditions = core.current_store().get('conditions', {})
        def scan():
            installed, errors, error_count = self.host.scan_installed_software_entries(self.stop)
            result = health.scan_checkup(rules, builtins, installed, health.default_data_roots(), self.host.default_download_dirs(),
                     scope, stop=self.stop, progress=self.scanProgress.emit, policy=policy, conditions=conditions,
                     protected=self.host.is_protected_system_path)
            if error_count:
                result['errors'].extend(errors[:8])
                result['complete'] = False
            return result
        self.run('scan', scan)

    def on_finished(self, key, result, error):
        self.busy = False
        self.scan_button.setEnabled(True)
        self.scope.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.select_button.setEnabled(True)
        self.progress.setRange(0, 100)
        self.progress.setValue(0 if error else 100)
        if error:
            self.status.setText("执行失败："+error)
            self.update_selection()
            return
        if key == 'scan':
            self.report = result
            self.visible_limit = 50
            self.last_plan = self.last_result = None
            self.owner_filter = ''
            self.app_model.reset(result['apps'])
            self.summary.setText(self.tr_format("推荐处理 {size} 缓存 · 找到 {count} 条建议",size=size(result['recommended_bytes']),count=len(result['rows'])))
            self.metrics.setText(self.tr_format("已统计 {count} 个文件，覆盖内容 {size}；这是扫描到的逻辑体积，移入回收站后不会立即释放空间。",
                                               count=result['file_count'],size=size(result['total'])))
            self.status.setText(self.tr("扫描已停止，显示已发现的项目。" if result['cancelled'] else
                                "部分位置未完整扫描，当前为部分统计。" if not result['complete'] else
                                "体检完成：先查看建议，再决定如何处理。"))
            if result['errors']:
                self.result_detail.setPlainText('\n'.join(result['errors']))
                self.result_detail.show()
            self.render_findings()
        else:
            self.last_result = result
            self.update_disk()
            self.summary.setText(f"{'已停止' if result['cancelled'] else '处理完成'}：成功 {result['success']}，失败 {result['failed']}，跳过 {result['skipped']}")
            delta = result['free_delta']
            transitions = '；'.join(f"{drive} 可用 {size(before)} → {size(result['free_after'][drive])}"
                          for drive, before in result.get('free_before', {}).items()
                          if before is not None and result.get('free_after', {}).get(drive) is not None)
            self.metrics.setText(f"{transitions}。处理逻辑体积 {size(result['estimated_bytes'])}；磁盘可用空间变化 {size(delta)}。"
                                 "文件默认移入回收站，空间变化还会受到其他程序写入影响。")
            done = {core.canonical(item['path']) for item in result['items'] if item['status']=='deleted'}
            if self.report:
                for row in self.report['rows']:
                    row['files'] = [item for item in row['files'] if core.canonical(item['path']) not in done]
                    if row['cleanable']:
                        row['count'] = len(row['files'])
                        row['size'] = health.unique_size(row['files'])
                    row['selected'] = False
                self.render_findings()
            self.result_detail.setPlainText('\n'.join(f"{item['path']}\n{item['reason'] or item['status']}" for item in result['items']
                                                     if item['status']!='deleted') or "本次勾选文件已全部处理。")
            self.result_detail.show()
            self.status.setText("可查看处理结果、重试失败项，或重新体检刷新软件占用排行。")
        self.retry_button.setEnabled(bool(self.last_result and self.last_result['failed']))
        self.update_selection()

    def render_findings(self, *args):
        if not self.report:
            return
        if args:
            self.visible_limit = 50
        while self.finding_layout.count():
            item = self.finding_layout.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        self.cards = []
        mode = self.filters.currentIndex()
        owner = getattr(self, 'owner_filter', '')
        rows = [row for row in self.report['rows'] if row['size'] and (not owner or row['owner']==owner)
                and (mode==0 or mode==1 and row['recommended'] or mode==2 and row['cleanable'] and not row['recommended']
                     or mode==3 and not row['cleanable'])]
        limit = getattr(self, 'visible_limit', 50)
        for finding in rows[:limit]:
            card = FindingCard(finding, self)
            self.finding_layout.addWidget(card)
            self.cards.append(card)
        if not rows:
            hint(self.finding_layout, "当前没有符合条件的建议。可切换筛选或扫描范围。")
        if len(rows)>limit:
            button(self.finding_layout, f"加载更多建议（还有 {len(rows)-limit} 项）", self.show_more)
        if owner:
            button(self.finding_layout, "返回全部建议", self.clear_owner)
        self.finding_layout.addStretch()
        self.main_window.apply_language_to_widget(self.findings)
        self.update_selection()

    def show_more(self):
        self.visible_limit = getattr(self, 'visible_limit', 50)+50
        self.render_findings()

    def clear_owner(self):
        self.owner_filter = ''
        self.render_findings()

    def switch_mode(self, index):
        self.stack.setCurrentIndex(index)
        self.filters.setVisible(index==0)
        self.select_button.setVisible(index==0)

    def select_recommended(self):
        if self.report and not self.busy:
            for row in self.report['rows']:
                row['selected'] = row['cleanable'] and row['recommended'] and bool(row['files'])
            self.render_findings()

    def update_selection(self):
        rows = [row for row in (self.report or {}).get('rows', []) if row['selected'] and row['cleanable']]
        total = health.unique_size([item for row in rows for item in row['files']])
        self.selection.setText(self.tr_format("已勾选 {count} 项，逻辑体积 {size}",count=len(rows),size=size(total)))
        self.clean_button.setEnabled(bool(rows) and not self.busy)

    def clean_selected(self):
        if self.report:
            self.preview_rows([row for row in self.report['rows'] if row['selected'] and row['cleanable']])

    def preview_rows(self, rows, plan=None):
        if self.busy:
            return
        plan = health.reviewed_plan(rows) if plan is None else plan
        if not plan['items']:
            self.status.setText("没有可处理的文件，请重新体检。")
            return
        dialog = PreviewDialog([], self.host, self.main_window, "体检清理预览", plan=plan)
        accepted = dialog.exec()
        if accepted and any(item['selected'] for item in plan['items']):
            self.last_plan = plan
            def execute():
                with core.JobLock(core.current_store().directory):
                    return core.execute_plan(plan, self.host.delete_path, False, self.stop, protected=self.host.is_protected_system_path,
                                             progress=self.cleanProgress.emit, kind="一键体检清理")
            self.run('clean', execute)
        dialog.deleteLater()

    def retry_failed(self):
        if self.last_plan and self.last_result:
            self.preview_rows([], health.retry_plan(self.last_plan, self.last_result))

    def show_app(self, *args):
        row = selected_row(self.app_view, self.app_model)
        self.app_details.setText('\n'.join(row['paths']) if row else "请选择软件")

    def open_app_path(self):
        row = selected_row(self.app_view, self.app_model)
        if row:
            dialog = QDialog(self.main_window)
            dialog.setWindowTitle("软件占用目录")
            dialog.resize(800, 350)
            layout = QVBoxLayout(dialog)
            hint(layout, row['name'])
            paths = ComboBox()
            paths.addItems(row['paths'])
            layout.addWidget(paths)
            button(layout, "查看文件", lambda: self.host.open_explorer(paths.currentText()))
            layout.addStretch()
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
            buttons.rejected.connect(dialog.reject)
            layout.addWidget(buttons)
            dialog.exec()
            dialog.deleteLater()

    def show_app_findings(self):
        row = selected_row(self.app_view, self.app_model)
        if row:
            self.owner_filter = row['name']
            self.filters.setCurrentIndex(0)
            self.mode.setCurrentIndex(0)
            self.render_findings()

    def uninstall_selected(self):
        row = selected_row(self.app_view, self.app_model)
        if row:
            self.open_uninstall(row['app_name'] or row['name'])

    def open_uninstall(self, name):
        if self.busy:
            return
        page = self.main_window._ensure_lazy_page('pg_uninstall')
        page.search_input.setText(name)
        self.main_window.switchTo(page)
        page.do_scan()

    def open_migration(self, path):
        if self.busy:
            return
        page = self.main_window._ensure_lazy_page('pg_toolbox')
        page._show_softlink_tool()
        page.edit_link_source.setText(path)
        self.main_window.switchTo(page)

    def export_report(self):
        if not self.report:
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出体检报告", "checkup-report.json", "JSON (*.json)")
        if path:
            try:
                core.atomic_text(path, json.dumps(self.report, ensure_ascii=False, indent=2))
                self.status.setText("报告已导出："+path)
            except OSError as exc:
                self.status.setText(str(exc))
