"""Qt views for the 0.8.3 maintenance services (kept separate from main.py)."""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QAbstractTableModel, QModelIndex, QRectF
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QDialog, QDialogButtonBox,
                              QTreeWidgetItem, QFileDialog, QStackedWidget,
                              QHeaderView, QAbstractItemView, QMenu, QToolTip)
from qfluentwidgets import (ScrollArea, TitleLabel, BodyLabel, CaptionLabel, PushButton, PrimaryPushButton,
                           ComboBox, SpinBox, LineEdit, TextEdit, CheckBox, InfoBar, SegmentedWidget, isDarkTheme)
from qfluentwidgets import TableView as QTableView, TreeWidget as QTreeWidget

import cleaner_core as core


def stamp(value):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(value))


def translate(widget, text):
    current = widget
    while current is not None:
        if hasattr(current, "tr_text"):
            return current.tr_text(text)
        current = current.parent() if hasattr(current, "parent") else None
    return text


def size(value):
    n = float(abs(value))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return ("-" if value < 0 else "") + f"{n:.1f} {unit}"
        n /= 1024


def button(layout, text, callback, primary=False):
    widget = (PrimaryPushButton if primary else PushButton)(text)
    widget.clicked.connect(callback)
    layout.addWidget(widget)
    return widget


def hint(layout, text):
    label = CaptionLabel(text)
    label.setWordWrap(True)
    layout.addWidget(label)
    return label


class RowsModel(QAbstractTableModel):
    def __init__(self, columns, rows=None, checkable=False, parent=None):
        super().__init__(parent)
        self.columns, self.rows, self.checkable = columns, rows or [], checkable

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.columns) + int(self.checkable)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row, col = self.rows[index.row()], index.column()
        if self.checkable and col == 0:
            if role == Qt.ItemDataRole.CheckStateRole:
                return Qt.CheckState.Checked if row.get("selected") else Qt.CheckState.Unchecked
            return None
        key, _label, formatter = self.columns[col - int(self.checkable)]
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            value = row.get(key, "")
            return formatter(value) if formatter else str(value)
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            if self.checkable and section == 0:
                return translate(self.parent(), "选择")
            return translate(self.parent(), self.columns[section - int(self.checkable)][1])
        return None

    def flags(self, index):
        result = super().flags(index)
        if index.isValid() and self.checkable and index.column() == 0:
            result |= Qt.ItemFlag.ItemIsUserCheckable
        return result

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if self.checkable and index.isValid() and index.column() == 0 and role == Qt.ItemDataRole.CheckStateRole:
            self.rows[index.row()]["selected"] = value in (Qt.CheckState.Checked, Qt.CheckState.Checked.value)
            self.dataChanged.emit(index, index, [role])
            return True
        return False

    def reset(self, rows):
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def select_all(self, selected):
        for row in self.rows:
            row["selected"] = selected
        if self.rows:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.rows) - 1, 0), [Qt.ItemDataRole.CheckStateRole])

    def sort(self, column, order=Qt.SortOrder.AscendingOrder):
        column -= int(self.checkable)
        if column < 0:
            return
        key = self.columns[column][0]
        self.beginResetModel()
        self.rows.sort(key=lambda row: row.get(key, ""), reverse=order == Qt.SortOrder.DescendingOrder)
        self.endResetModel()


def table(layout, columns, rows=None, checkable=False):
    view = QTableView()
    model = RowsModel(columns, rows, checkable, view)
    view.setModel(model)
    view.setAlternatingRowColors(True)
    view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    view.setSortingEnabled(True)
    view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    view.verticalHeader().hide()
    view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    view.horizontalHeader().setStretchLastSection(True)
    widths = {"created": 160, "rule": 190, "kind": 140, "project": 240, "category": 140,
              "size": 115, "mtime": 165, "path": 340, "success": 75, "failed": 75,
              "skipped": 75, "state": 100, "estimated_bytes": 150, "free_delta": 150}
    if checkable:
        view.setColumnWidth(0, 44)
    for index, (key, _label, _formatter) in enumerate(columns, int(checkable)):
        view.setColumnWidth(index, widths.get(key, 120))
    view.setMinimumHeight(160)
    layout.addWidget(view, 1)
    return view, model


def selected_row(view, model):
    index = view.currentIndex().row()
    return model.rows[index] if 0 <= index < len(model.rows) else None


def attach_exclusion_menu(view, model, host):
    view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    def show(pos):
        index = view.indexAt(pos)
        if not index.isValid():
            return
        path = model.rows[index.row()].get("path")
        if not path:
            return
        menu = QMenu(view)
        menu.addAction("在资源管理器中定位", lambda: host.open_explorer(path))
        def exclude():
            store = core.current_store()
            entries = store.get("exclusions", [])
            if path not in entries:
                entries.append(path)
                store.set("exclusions", entries)
            InfoBar.success("已加入排除", path, parent=view.window())
        menu.addAction("加入统一排除列表", exclude)
        menu.exec(view.viewport().mapToGlobal(pos))
    view.customContextMenuRequested.connect(show)


class PreviewDialog(QDialog):
    loaded = Signal(object, str)

    def __init__(self, rules, host, parent=None, title="清理预览", plan=None, read_only=False):
        super().__init__(parent)
        self.host, self.plan, self.read_only = host, None, read_only
        self.stop = threading.Event()
        self.setWindowTitle(title)
        self.resize(1050, 650)
        layout = QVBoxLayout(self)
        if read_only:
            hint(layout, "仅预览，不会删除任何文件。")
        self.status = hint(layout, "正在生成文件清单，可随时取消。目录只在清理后确认为空时移除。")
        tools = QHBoxLayout()
        if read_only:
            tools.addStretch()
        else:
            button(tools, "全选", lambda: self.model.select_all(True))
            button(tools, "全不选", lambda: self.model.select_all(False))
        button(tools, "导出预览 JSON", self.export)
        layout.addLayout(tools)
        self.view, self.model = table(layout, [("rule", "命中规则", None), ("kind", "类型", None), ("size", "大小", size),
                                              ("mtime", "修改时间", stamp), ("path", "路径", None)], checkable=not read_only)
        attach_exclusion_menu(self.view, self.model, host)
        if read_only:
            self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
            self.buttons.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        else:
            self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("清理已勾选项")
            self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
            self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.loaded.connect(self._loaded)
        # Snapshot inputs before starting the worker; it never reads widgets.
        snapshot = [tuple(rule) for rule in rules]
        if plan is None:
            self.worker = threading.Thread(target=self._scan, args=(snapshot,), daemon=True)
            self.worker.start()
        else:
            self._loaded(plan, "")
        if parent is not None and hasattr(parent, "apply_language_to_widget"):
            parent.apply_language_to_widget(self)

    def _scan(self, rules):
        try:
            plan = core.build_plan(rules, self.stop, self.host.is_protected_system_path)
            result, error = plan, ""
        except Exception as exc:
            result, error = None, str(exc)
        if not self.stop.is_set():
            try:
                self.loaded.emit(result, error)
            except RuntimeError:
                pass  # The parent window may have been destroyed on shutdown.

    def _loaded(self, plan, error):
        self.plan = plan
        if error:
            self.status.setText(error)
            return
        self.model.reset(plan["items"])
        total = sum(item["size"] for item in plan["items"])
        self.status.setText(f"共 {len(plan['items'])} 项，文件逻辑体积 {size(total)}，按条件跳过 {plan['omitted']} 项。"
                            + ("" if plan["complete"] else ("扫描不完整。\n" if self.read_only else "扫描不完整，禁止执行。\n") + "\n".join(plan["errors"][:8])))
        if not self.read_only:
            self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(plan["complete"] and bool(plan["items"]))

    def export(self):
        if self.plan is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出预览", "clean-preview.json", "JSON (*.json)")
        if path:
            try:
                core.atomic_text(path, json.dumps(self.plan, ensure_ascii=False, indent=2))
            except Exception as exc:
                self.status.setText(str(exc))

    def done(self, code):
        self.stop.set()
        super().done(code)


class ReviewPackDialog(QDialog):
    def __init__(self, rules, name, store, parent=None):
        super().__init__(parent)
        self.setWindowTitle("规则包更新审阅 / " + name)
        self.resize(900, 600)
        versions = store.packs(name)
        previous = next((row["rules"] for row in versions if row["active"]), [])
        self.review = core.inspect_pack(rules, previous)
        layout = QVBoxLayout(self)
        hint(layout, f"新增 {len(self.review['added'])} 条，删除 {len(self.review['removed'])} 条，属性变化 {len(self.review['changed'])} 条\n"
             f"内容 SHA-256: {self.review['digest']}")
        changes = TextEdit()
        changes.setReadOnly(True)
        lines = []
        for key, label in (("added", "新增"), ("removed", "删除"), ("changed", "变化")):
            lines.extend(f"[{label}] {rule[0]} → {rule[1]}" for rule in self.review[key])
        lines.extend("[检查] " + warning for warning in self.review["warnings"])
        changes.setPlainText("\n".join(lines) or "内容没有变化")
        layout.addWidget(changes)
        self.ack = CheckBox("已检查重复目标和宽泛路径，确认应用此规则包")
        self.ack.setChecked(not self.review["warnings"])
        layout.addWidget(self.ack)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("应用并保存版本")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(self.ack.isChecked())
        self.ack.toggled.connect(buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        if parent is not None and hasattr(parent, "apply_language_to_widget"):
            parent.apply_language_to_widget(self)


class Treemap(QWidget):
    activated = Signal(str)

    def __init__(self):
        super().__init__()
        self.rows, self.boxes = [], []
        self.setMinimumHeight(180)
        self.setMouseTracking(True)

    def set_rows(self, rows):
        self.rows = sorted((r for r in rows if r["size"] > 0), key=lambda row: -row["size"])
        if len(self.rows) > 100:
            remaining = self.rows[99:]
            self.rows = self.rows[:99] + [dict(path="(其余目录)", size=sum(row["size"] for row in remaining))]
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#202020" if isDarkTheme() else "#f3f3f3"))
        self.boxes = []
        def draw(rows, rect, depth=0):
            if not rows:
                return
            if len(rows) == 1:
                row = rows[0]
                self.boxes.append((rect, row))
                painter.fillRect(rect.adjusted(1, 1, -1, -1), QColor.fromHsv(abs(hash(row["path"])) % 360, 125, 165 if isDarkTheme() else 200))
                painter.setPen(QColor("white"))
                if rect.width() > 65 and rect.height() > 30:
                    painter.drawText(rect.adjusted(5, 5, -5, -5), Qt.TextFlag.TextWordWrap,
                                     f"{os.path.basename(row['path']) or row['path']}\n{size(row['size'])}")
                return
            total = sum(row["size"] for row in rows)
            cumulative, split = 0, 1
            for i, row in enumerate(rows[:-1], 1):
                cumulative += row["size"]
                split = i
                if cumulative >= total / 2:
                    break
            ratio = cumulative / total
            if rect.width() >= rect.height():
                width = rect.width() * ratio
                left, right = QRectF(rect.x(), rect.y(), width, rect.height()), QRectF(rect.x()+width, rect.y(), rect.width()-width, rect.height())
            else:
                height = rect.height() * ratio
                left, right = QRectF(rect.x(), rect.y(), rect.width(), height), QRectF(rect.x(), rect.y()+height, rect.width(), rect.height()-height)
            draw(rows[:split], left, depth+1)
            draw(rows[split:], right, depth+1)
        try:
            draw(self.rows, QRectF(self.rect()))
        finally:
            # The recursive layout closure retains painter until cyclic GC.
            # End painting while the widget's paint device is still alive.
            painter.end()

    def mouseMoveEvent(self, event):
        for rect, row in self.boxes:
            if rect.contains(event.position()):
                QToolTip.showText(event.globalPosition().toPoint(), f"{row['path']}\n{size(row['size'])}", self)
                break

    def mouseDoubleClickEvent(self, event):
        for rect, row in self.boxes:
            if rect.contains(event.position()):
                self.activated.emit(row["path"])
                break


class MaintenancePage(ScrollArea):
    completed = Signal(str, object, str)

    def __init__(self, main_window, host, parent=None, sections=None, embedded=False):
        super().__init__(parent)
        self.main_window, self.host = main_window, host
        self.stop = threading.Event()
        self.busy = False
        self.snapshot = None
        self.setObjectName("maintenancePage")
        self.setWidgetResizable(True)
        self.enableTransparentBackground()
        self.view = QWidget()
        self.setWidget(self.view)
        layout = QVBoxLayout(self.view)
        layout.setContentsMargins(*((0, 0, 12, 0) if embedded else (28, 12, 28, 20)))
        titles = ["清理历史", "统一排除", "高级规则条件", "空间分析与增长", "运行条件", "规则包版本", "重复文件策略", "开发环境空间"]
        sections = list(range(8)) if sections is None else list(sections)
        if not embedded:
            layout.addWidget(TitleLabel(titles[sections[0]] if len(sections)==1 else "清理设置"))
        self.tabs = SegmentedWidget()
        for position, section in enumerate(sections):
            self.tabs.addItem(str(position), titles[section])
        self.tabs.setVisible(len(sections)>1)
        layout.addWidget(self.tabs, 0, Qt.AlignmentFlag.AlignLeft)
        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)
        self.tabs.currentItemChanged.connect(lambda key: self.stack.setCurrentIndex(int(key)))
        self.tabs.setCurrentItem("0")
        scannable = bool({3, 7} & set(sections))
        self.status = hint(layout, "配置和历史保存在当前配置目录。扫描可取消。" if scannable else "")
        bottom = QHBoxLayout()
        bottom.addStretch()
        self.cancel_button = button(bottom, "停止当前扫描", self.stop.set)
        self.cancel_button.setEnabled(False)
        self.cancel_button.setVisible(scannable)
        layout.addLayout(bottom)
        builders = [self._history_page, self._exclusion_page, self._conditions_page, self._space_page,
                    self._auto_page, self._packs_page, self._duplicates_page, self._development_page]
        for i in sections:
            builders[i]()
        self.completed.connect(self._completed)
        self.refresh_history()

    @property
    def store(self):
        return core.current_store()

    def page(self, description):
        page = QWidget()
        layout = QVBoxLayout(page)
        hint(layout, description)
        self.stack.addWidget(page)
        return layout

    def run(self, key, fn):
        if self.busy:
            self.status.setText("请先等待或停止当前任务")
            return
        self.busy = True
        self.stop.clear()
        self.cancel_button.setEnabled(key in {"空间扫描", "开发目录扫描", "开发目录清理"})
        self.status.setText("正在执行: " + key)
        def worker():
            try:
                self.completed.emit(key, fn(), "")
            except Exception as exc:
                self.completed.emit(key, None, str(exc))
        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    def _completed(self, key, result, error):
        self.busy = False
        self.cancel_button.setEnabled(False)
        if error:
            self.status.setText(error)
            return
        self.status.setText("完成: " + key)
        if key == "空间扫描":
            self._show_snapshot(result["snapshot"], result["baselines"], result["saved"])
        elif key == "快照对比":
            self.space_model.reset(result)
            self.status.setText("增长按目录分别计算，父目录与子目录数值不可相加。")
        elif key == "开发目录扫描":
            self.dev_model.reset([dict(row, selected=False) for row in result])
            self.status.setText(f"找到 {len(result)} 个候选目录，{size(sum(row['size'] for row in result))}。勾选后生成文件级清理预览。")
        elif key == "虚拟磁盘识别":
            self.disks = result
            self.disk_combo.clear()
            for row in result:
                self.disk_combo.addItem(f"{row['name']} · {size(row['size'])} · {row['path']}")
            self.dev_output.setPlainText("\n".join(row["path"] for row in result) or "未识别到 WSL/Docker 虚拟磁盘")
        elif key == "条件检查":
            self.status.setText(result[1])
        elif isinstance(result, str):
            self.dev_output.setPlainText(result)
        self.refresh_history()

    def _history_page(self):
        layout = self.page("记录操作结果与文件明细。逻辑体积与磁盘可用空间变化分别记录；移入回收站不代表立即释放空间。")
        row = QHBoxLayout()
        row.addStretch()
        button(row, "刷新", self.refresh_history)
        button(row, "导出 JSON / CSV", self.export_history)
        layout.addLayout(row)
        self.history_view, self.history_model = table(layout, [("created", "时间", stamp), ("kind", "操作", None),
              ("state", "状态", None), ("success", "成功", None), ("failed", "失败", None), ("skipped", "跳过", None),
              ("estimated_bytes", "已处理逻辑体积", size), ("free_delta", "可用空间变化", size)])
        self.history_detail = TextEdit()
        self.history_detail.setReadOnly(True)
        self.history_detail.setMaximumHeight(180)
        layout.addWidget(self.history_detail)
        self.history_view.selectionModel().currentRowChanged.connect(self.show_history_detail)

    def refresh_history(self):
        if not hasattr(self, "history_model"):
            return
        try:
            self.history_model.reset(self.store.history())
        except Exception as exc:
            self.status.setText(str(exc))

    def show_history_detail(self, *args):
        row = selected_row(self.history_view, self.history_model)
        self.history_detail.setPlainText(json.dumps(row, ensure_ascii=False, indent=2) if row else "")

    def export_history(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出历史", "clean-history.json", "JSON (*.json);;CSV (*.csv)")
        if path:
            try:
                self.store.export_history(path)
                self.status.setText("已导出: " + path)
            except Exception as exc:
                self.status.setText(str(exc))

    def _exclusion_page(self):
        layout = self.page("每行一个保护路径或通配符，例如 D:\\Work、*.psd、*\\.git。排除目录会同时保护其子项。各清理入口和定时任务共用。")
        self.exclusions = TextEdit()
        self.exclusions.setPlainText("\n".join(self.store.get("exclusions", [])))
        layout.addWidget(self.exclusions, 1)
        row = QHBoxLayout()
        button(row, "添加目录", self.pick_exclusion)
        row.addStretch()
        button(row, "重新读取", lambda: self.exclusions.setPlainText("\n".join(self.store.get("exclusions", []))))
        button(row, "保存排除列表", self.save_exclusions, True)
        layout.addLayout(row)

    def pick_exclusion(self):
        path = QFileDialog.getExistingDirectory(self, "选择保护目录")
        if path:
            self.exclusions.append(path.replace("/", "\\"))

    def save_exclusions(self):
        try:
            entries = list(dict.fromkeys(line.strip() for line in self.exclusions.toPlainText().splitlines() if line.strip()))
            core.Policy(entries)
            self.store.set("exclusions", entries)
            self.status.setText(f"已保存 {len(entries)} 条排除规则")
        except Exception as exc:
            self.status.setText(str(exc))

    def _conditions_page(self):
        layout = self.page("条件按规则保存，常规清理、文件预览和定时清理一致生效。保留最新 N 份按本规则命中的全部文件排序，再应用时间和大小条件。")
        row = QHBoxLayout()
        self.rule_combo = ComboBox()
        row.addWidget(self.rule_combo, 1)
        button(row, "刷新规则", self.refresh_rules)
        layout.addLayout(row)
        self.age = self.spin(layout, "最小修改年龄（天，0 不限）", 36500)
        self.minimum = self.spin(layout, "最小文件大小（MB，0 不限）", 100000000)
        self.keep = self.spin(layout, "保留最新文件数量（0 不保留）", 1000000)
        row = QHBoxLayout()
        row.addStretch()
        button(row, "保存当前规则条件", self.save_conditions, True)
        layout.addLayout(row)
        layout.addStretch()
        self.rule_combo.currentIndexChanged.connect(self.load_conditions)
        self.refresh_rules()

    def spin(self, layout, label, maximum, default=0):
        row = QHBoxLayout()
        row.addWidget(BodyLabel(label), 1)
        value = SpinBox()
        value.setRange(0, maximum)
        value.setValue(default)
        row.addWidget(value)
        layout.addLayout(row)
        return value

    def refresh_rules(self):
        with self.main_window._targets_lock:
            self.rules = list(self.main_window.targets)
        self.rule_combo.clear()
        self.rule_combo.addItems([str(rule[0]) + " · " + str(rule[1]) for rule in self.rules])
        self.load_conditions()

    def load_conditions(self, *args):
        index = self.rule_combo.currentIndex()
        if 0 <= index < len(self.rules):
            cond = self.store.get("conditions", {}).get(core.rule_id(self.rules[index]), {})
            self.age.setValue(cond.get("min_age_days", 0))
            self.minimum.setValue(cond.get("min_size_bytes", 0) // 1024**2)
            self.keep.setValue(cond.get("keep_latest", 0))

    def save_conditions(self):
        index = self.rule_combo.currentIndex()
        if 0 <= index < len(self.rules):
            conditions = self.store.get("conditions", {})
            conditions[core.rule_id(self.rules[index])] = dict(min_age_days=self.age.value(), min_size_bytes=self.minimum.value() * 1024**2, keep_latest=self.keep.value())
            try:
                self.store.set("conditions", conditions)
                self.status.setText("规则条件已保存")
            except Exception as exc:
                self.status.setText(str(exc))

    def _space_page(self):
        layout = self.page("完整扫描后自动保存快照，可与同一目录的历史快照对比。统计不跟随链接，同一硬链接文件只计一次。矩形面积代表逻辑体积。")
        row = QHBoxLayout()
        self.space_root = LineEdit()
        self.space_root.setPlaceholderText("选择磁盘或目录")
        row.addWidget(self.space_root, 1)
        button(row, "选择目录", lambda: self.browse(self.space_root))
        button(row, "扫描并保存快照", self.scan_space, True)
        layout.addLayout(row)
        self.space_tree = QTreeWidget()
        self.space_tree.setHeaderLabels(["目录", "体积", "文件数"])
        self.space_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.space_tree.setColumnWidth(1, 130)
        self.space_tree.setColumnWidth(2, 110)
        self.space_tree.setMinimumHeight(170)
        layout.addWidget(self.space_tree, 1)
        self.map = Treemap()
        layout.addWidget(self.map)
        self.space_tree.currentItemChanged.connect(self.select_space_node)
        self.map.activated.connect(self.select_map_node)
        row = QHBoxLayout()
        self.baseline = ComboBox()
        row.addWidget(self.baseline, 1)
        button(row, "与选中快照对比", self.compare_space)
        button(row, "按文件类型汇总", lambda: self.show_extensions() if self.snapshot else None)
        button(row, "导出当前快照", self.export_snapshot)
        layout.addLayout(row)
        self.space_results, self.space_model = table(layout, [("path", "目录 / 类型", None), ("before", "之前", size),
                                                                           ("after", "当前", size), ("delta", "增长", size)])
        attach_exclusion_menu(self.space_results, self.space_model, self.host)

    def browse(self, edit):
        path = QFileDialog.getExistingDirectory(self, "选择目录", edit.text())
        if path:
            edit.setText(path)

    def scan_space(self):
        root = self.space_root.text().strip()
        store = self.store
        def scan():
            baselines = store.snapshot_headers(root)
            snapshot = core.scan_usage(root, stop=self.stop)
            saved = store.save_snapshot(snapshot) if snapshot["complete"] else ""
            return dict(snapshot=snapshot, baselines=baselines, saved=saved)
        self.run("空间扫描", scan)

    def _show_snapshot(self, snapshot, baselines=None, saved=""):
        self.snapshot = snapshot
        self.space_tree.clear()
        self.tree_items = {}
        # Build parents first, only materialize the first 10000 nodes. Full
        # scan data remains available for reports and growth comparisons.
        for path in sorted(snapshot["nodes"], key=lambda p: (len(p), p))[:10000]:
            node = snapshot["nodes"][path]
            parent = self.tree_items.get(node["parent"])
            item = QTreeWidgetItem([os.path.basename(path) or path, size(node["size"]), str(node["files"])])
            item.setData(0, Qt.ItemDataRole.UserRole, path)
            if parent:
                parent.addChild(item)
            else:
                self.space_tree.addTopLevelItem(item)
            self.tree_items[path] = item
        if self.space_tree.topLevelItemCount():
            first = self.space_tree.topLevelItem(0)
            first.setExpanded(True)
            self.space_tree.setCurrentItem(first)
        self.baseline.clear()
        self.baselines = baselines or []
        for baseline in self.baselines:
            self.baseline.addItem(stamp(baseline["created"]))
        if snapshot["complete"]:
            if self.baselines:
                self.compare_space()
            else:
                self.show_extensions()
            self.status.setText(f"{'已保存快照' if saved else '扫描完成'}，共 {size(snapshot['total'])}；目录树最多显示 10000 项，报告包含全部扫描数据。")
        else:
            self.show_extensions()
            self.status.setText("扫描不完整，未保存快照。\n" + "\n".join(snapshot["errors"][:6]))

    def select_space_node(self, current, previous=None):
        if not current or not self.snapshot:
            return
        path = current.data(0, Qt.ItemDataRole.UserRole)
        nodes = self.snapshot["nodes"]
        node = nodes[path]
        rows = [nodes[p] for p in node["children"] if p in nodes]
        if node["direct_size"]:
            rows.append(dict(path=os.path.join(path, "(当前目录文件)"), size=node["direct_size"]))
        self.map.set_rows(rows)

    def select_map_node(self, path):
        item = self.tree_items.get(path)
        if item:
            self.space_tree.setCurrentItem(item)
            item.setExpanded(True)
            self.space_tree.scrollToItem(item)

    def show_extensions(self):
        self.space_model.reset([dict(path=ext, before=0, after=row["size"], delta=0) for ext, row in self.snapshot["extensions"].items()])

    def compare_space(self):
        index = self.baseline.currentIndex()
        if self.snapshot and 0 <= index < len(self.baselines):
            item_id, snapshot, store = self.baselines[index]["id"], self.snapshot, self.store
            self.run("快照对比", lambda: core.compare_snapshots(store.snapshot(item_id), snapshot))

    def export_snapshot(self):
        if not self.snapshot:
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出快照", "space-snapshot.json", "JSON (*.json)")
        if path:
            try:
                core.atomic_text(path, json.dumps(self.snapshot, ensure_ascii=False, indent=2))
            except Exception as exc:
                self.status.setText(str(exc))

    def _auto_page(self):
        layout = self.page("这些条件统一约束现有 Windows 定时任务。在定时任务页设置每隔几分钟检查一次；仅当所有条件满足时执行。手动清理不受此限制。")
        saved = dict(core.DEFAULT_AUTO_POLICY, **self.store.get("auto_policy", {}))
        self.auto_enabled = CheckBox("启用条件式自动维护")
        self.auto_enabled.setChecked(saved["enabled"])
        layout.addWidget(self.auto_enabled)
        self.auto_drive = LineEdit()
        self.auto_drive.setText(saved["drive"])
        layout.addWidget(BodyLabel("监控磁盘路径"))
        layout.addWidget(self.auto_drive)
        self.free_gb = self.spin(layout, "剩余空间低于（GB）", 100000, saved["free_below_gb"])
        self.idle = self.spin(layout, "用户空闲至少（分钟）", 10080, saved["idle_minutes"])
        self.cooldown = self.spin(layout, "两次执行最小间隔（分钟）", 525600, saved["cooldown_minutes"])
        self.skip_processes = LineEdit()
        self.skip_processes.setText(", ".join(saved["skip_processes"]))
        layout.addWidget(BodyLabel("这些进程运行时跳过（逗号分隔，进程文件名）"))
        layout.addWidget(self.skip_processes)
        self.notify = CheckBox("维护结束后显示 Windows 通知")
        self.notify.setChecked(saved["notify"])
        layout.addWidget(self.notify)
        row = QHBoxLayout()
        row.addStretch()
        button(row, "检查当前是否满足条件", self.check_auto)
        button(row, "保存条件", self.save_auto, True)
        layout.addLayout(row)
        layout.addStretch()

    def auto_value(self):
        drive = self.auto_drive.text().strip()
        if not os.path.isabs(drive) or not os.path.isdir(drive):
            raise ValueError("监控磁盘路径必须存在且为绝对路径")
        return dict(enabled=self.auto_enabled.isChecked(), drive=drive, free_below_gb=self.free_gb.value(), idle_minutes=self.idle.value(),
                    cooldown_minutes=self.cooldown.value(), skip_processes=[p.strip() for p in self.skip_processes.text().replace("，", ",").split(",") if p.strip()], notify=self.notify.isChecked())

    def save_auto(self):
        try:
            self.store.set("auto_policy", self.auto_value())
            self.status.setText("运行条件已保存；在定时任务页设置检查频率")
        except Exception as exc:
            self.status.setText(str(exc))

    def check_auto(self):
        try:
            policy = self.auto_value()
            last = self.store.get("auto_last_run", 0)
            self.run("条件检查", lambda: core.check_auto_policy(policy, last))
        except Exception as exc:
            self.status.setText(str(exc))

    def _packs_page(self):
        layout = self.page("规则商店导入时先审阅变化并保存版本。选中历史版本可审阅并回退。回退只替换该规则包中未被手动修改的规则。")
        row = QHBoxLayout()
        button(row, "刷新版本", self.refresh_packs)
        button(row, "审阅并恢复选中版本", self.restore_pack)
        layout.addLayout(row)
        self.packs_view, self.packs_model = table(layout, [("created", "时间", stamp), ("name", "规则包", None), ("active", "当前", None), ("digest", "SHA-256", None)])
        self.refresh_packs()

    def refresh_packs(self):
        self.packs_model.reset(self.store.packs())

    def restore_pack(self):
        row = selected_row(self.packs_view, self.packs_model)
        if row:
            try:
                if self.host.apply_reviewed_rule_pack(self.main_window, row["rules"], row["name"]):
                    self.refresh_packs()
                    self.status.setText("已恢复所选规则包版本")
            except Exception as exc:
                self.status.setText(str(exc))

    def _duplicates_page(self):
        layout = self.page("重复文件查找默认每组保留一份。硬链接别名合并处理，删除前仍做完整哈希复核。此处保存后对下一次扫描生效。")
        saved = self.store.get("duplicate_policy", {})
        self.duplicate_strategy = ComboBox()
        self.duplicate_strategy.addItems(["按路径顺序保留", "保留最新修改的一份", "保留最旧修改的一份", "优先保留指定目录中的一份"])
        self.duplicate_strategy.setCurrentIndex(["path", "newest", "oldest", "preferred"].index(saved.get("strategy", "path")) if saved.get("strategy", "path") in ["path", "newest", "oldest", "preferred"] else 0)
        layout.addWidget(self.duplicate_strategy)
        self.duplicate_preferred_row = QWidget()
        row = QHBoxLayout(self.duplicate_preferred_row)
        row.setContentsMargins(0, 0, 0, 0)
        self.duplicate_preferred = LineEdit()
        self.duplicate_preferred.setText(saved.get("preferred", ""))
        self.duplicate_preferred.setPlaceholderText("优先保留目录")
        row.addWidget(self.duplicate_preferred, 1)
        button(row, "选择保留目录", lambda: self.browse(self.duplicate_preferred))
        layout.addWidget(self.duplicate_preferred_row)
        self.duplicate_preferred_row.setVisible(self.duplicate_strategy.currentIndex() == 3)
        self.duplicate_strategy.currentIndexChanged.connect(lambda index: self.duplicate_preferred_row.setVisible(index == 3))
        row = QHBoxLayout()
        row.addStretch()
        button(row, "保存重复文件策略", self.save_duplicate_policy, True)
        layout.addLayout(row)
        layout.addStretch()

    def save_duplicate_policy(self):
        strategy = ["path", "newest", "oldest", "preferred"][self.duplicate_strategy.currentIndex()]
        preferred = self.duplicate_preferred.text().strip()
        if strategy == "preferred" and not os.path.isdir(preferred):
            self.status.setText("请选择存在的优先保留目录")
            return
        try:
            self.store.set("duplicate_policy", dict(strategy=strategy, preferred=preferred))
            self.status.setText("保留策略已保存，下次重复文件扫描生效")
        except Exception as exc:
            self.status.setText(str(exc))

    def _development_page(self):
        layout = self.page("按项目汇总依赖、虚拟环境和构建产物。勾选目录后会生成文件清单供确认。Docker 清理仅处理构建缓存与悬空镜像；WSL 压缩会停止全部发行版，需先退出 Docker。")
        row = QHBoxLayout()
        self.dev_root = LineEdit()
        self.dev_root.setPlaceholderText("项目根目录")
        row.addWidget(self.dev_root, 1)
        button(row, "选择目录", lambda: self.browse(self.dev_root))
        button(row, "扫描开发目录", self.scan_dev)
        layout.addLayout(row)
        self.dev_view, self.dev_model = table(layout, [("project", "项目", None), ("category", "类型", None), ("size", "体积", size), ("path", "路径", None)], checkable=True)
        attach_exclusion_menu(self.dev_view, self.dev_model, self.host)
        row = QHBoxLayout()
        button(row, "预览勾选目录的清理内容", self.clean_dev, True)
        button(row, "Docker 空间分析", lambda: self.run("Docker 空间分析", lambda: core.command(["docker", "system", "df", "-v"], timeout=120)))
        button(row, "清理 Docker 构建缓存", lambda: self.docker_cleanup("builder"))
        button(row, "清理 Docker 悬空镜像", lambda: self.docker_cleanup("image"))
        layout.addLayout(row)
        row = QHBoxLayout()
        self.disk_combo = ComboBox()
        row.addWidget(self.disk_combo, 1)
        button(row, "识别 WSL 磁盘", lambda: self.run("虚拟磁盘识别", core.wsl_disks))
        button(row, "停止 WSL 并压缩所选磁盘", self.compact_disk)
        layout.addLayout(row)
        self.disks = []
        self.dev_output = TextEdit()
        self.dev_output.setReadOnly(True)
        self.dev_output.setMaximumHeight(180)
        layout.addWidget(self.dev_output)

    def scan_dev(self):
        root = self.dev_root.text().strip()
        self.run("开发目录扫描", lambda: core.scan_development(root, self.stop))

    def clean_dev(self):
        if self.busy:
            self.status.setText("请先等待当前任务完成")
            return
        rows = [row for row in self.dev_model.rows if row.get("selected")]
        if not rows:
            self.status.setText("请先勾选开发目录")
            return
        rules = [(row["category"], row["path"], "dir", True, "开发目录清理", True, "") for row in rows]
        dialog = PreviewDialog(rules, self.host, self.window(), "开发目录清理预览")
        if dialog.exec() == QDialog.DialogCode.Accepted:
            plan = dialog.plan
            self.run("开发目录清理", lambda: self._execute_dev(plan))

    def _execute_dev(self, plan):
        store = self.store
        with core.JobLock(store.directory):
            result = core.execute_plan(plan, self.host.delete_path, False, self.stop,
                                       protected=self.host.is_protected_system_path, kind="开发目录清理")
        return f"清理完成：成功 {result['success']}，失败 {result['failed']}，跳过 {result['skipped']}。文件已移入回收站。"

    def docker_cleanup(self, kind):
        if self.busy:
            return
        label = "构建缓存" if kind == "builder" else "悬空镜像"
        if not self.host.MessageBox("确认 Docker 清理", f"将通过 Docker CLI 清理未使用的{label}。后续构建可能需要重新下载或编译。继续？", self.window()).exec():
            return
        def clean():
            args = ["docker", kind, "prune", "--force"]
            return core.recorded_command(args, "Docker 清理", timeout=1200)
        self.run("Docker 清理", clean)

    def compact_disk(self):
        index = self.disk_combo.currentIndex()
        if self.busy or not 0 <= index < len(self.disks):
            return
        path = self.disks[index]["path"]
        if not self.host.MessageBox("确认停止 WSL 并压缩", f"将停止所有 WSL 发行版，压缩虚拟磁盘：\n{path}\n\n请先保存 WSL 中的工作并完全退出 Docker Desktop。继续？", self.window()).exec():
            return
        def compact():
            with core.JobLock(self.store.directory):
                return core.compact_wsl_disk(path)
        self.run("WSL 磁盘压缩", compact)
