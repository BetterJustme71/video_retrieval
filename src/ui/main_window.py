from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QProgressBar,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.app.config import CONFIG
from src.app.main import index_videos, load_latest_search_results, scan_videos, search_script, update_search_match_statuses
from src.core.clipper import export_clip, export_clips, open_match_preview
from src.core.edit_list import export_editing_checklist
from src.core.models import EDIT_LIST_STATUSES, MATCH_STATUS_BAD, MATCH_STATUS_EXPORTED, MATCH_STATUS_PENDING, MATCH_STATUS_USABLE, MATCH_STATUSES
from src.core.thumbnailer import export_thumbnail, export_thumbnails
from src.core.timecode import ms_to_timecode
from src.core.text_utils import summarize

STATUS_VALUES = MATCH_STATUSES
ALL_STATUS = "全部"


class Worker(QObject):
    log = Signal(str)
    progress = Signal(int)
    result = Signal(object)
    error = Signal(str)
    finished = Signal()

    def __init__(self, fn: Callable, *args, **kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs

    @Slot()
    def run(self) -> None:
        try:
            if "progress" in self.kwargs and self.kwargs["progress"] == "signal":
                self.kwargs["progress"] = self.log.emit
            data = self.fn(*self.args, **self.kwargs)
            self.result.emit(data)
        except Exception as exc:
            self.error.emit(str(exc))
        finally:
            self.finished.emit()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("视频片段检索工具")
        self.resize(1280, 820)
        self.thread: QThread | None = None
        self.worker: Worker | None = None
        self.current_matches = []
        self._build_ui()

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)

        path_grid = QGridLayout()
        self.video_dir_edit = QLineEdit(str(CONFIG.default_video_dir))
        self.script_edit = QLineEdit(str(CONFIG.default_script_path))
        video_btn = QPushButton("选择视频目录")
        script_btn = QPushButton("选择文案")
        video_btn.clicked.connect(self.choose_video_dir)
        script_btn.clicked.connect(self.choose_script)
        path_grid.addWidget(QLabel("视频目录"), 0, 0)
        path_grid.addWidget(self.video_dir_edit, 0, 1)
        path_grid.addWidget(video_btn, 0, 2)
        path_grid.addWidget(QLabel("脚本文案"), 1, 0)
        path_grid.addWidget(self.script_edit, 1, 1)
        path_grid.addWidget(script_btn, 1, 2)
        layout.addLayout(path_grid)

        buttons = QHBoxLayout()
        self.top_k_spin = QSpinBox()
        self.top_k_spin.setRange(1, 20)
        self.top_k_spin.setValue(5)
        self.top_k_spin.setToolTip("每个文案段落返回多少条候选片段")
        buttons.addWidget(QLabel("每段候选数"))
        buttons.addWidget(self.top_k_spin)
        self.scan_btn = QPushButton("扫描视频")
        self.index_first_btn = QPushButton("先索引第1集测试")
        self.index_all_btn = QPushButton("全量索引")
        self.search_btn = QPushButton("搜索文案片段")
        self.load_latest_btn = QPushButton("加载最近结果")
        self.preview_btn = QPushButton("预览选中片段")
        self.export_clip_btn = QPushButton("导出选中片段")
        self.export_selected_btn = QPushButton("批量导出选中")
        self.thumb_selected_btn = QPushButton("生成选中缩略图")
        self.scan_btn.clicked.connect(self.scan)
        self.index_first_btn.clicked.connect(lambda: self.index("1"))
        self.index_all_btn.clicked.connect(lambda: self.index("all"))
        self.search_btn.clicked.connect(self.search)
        self.load_latest_btn.clicked.connect(self.load_latest_results)
        self.preview_btn.clicked.connect(self.preview_selected)
        self.export_clip_btn.clicked.connect(self.export_selected_clip)
        self.export_selected_btn.clicked.connect(self.export_selected_clips)
        self.thumb_selected_btn.clicked.connect(self.export_selected_thumbnails)
        self.preview_btn.setEnabled(False)
        self.export_clip_btn.setEnabled(False)
        self.export_selected_btn.setEnabled(False)
        self.thumb_selected_btn.setEnabled(False)
        for btn in [self.scan_btn, self.index_first_btn, self.index_all_btn, self.search_btn, self.load_latest_btn, self.preview_btn, self.export_clip_btn, self.export_selected_btn, self.thumb_selected_btn]:
            buttons.addWidget(btn)
        layout.addLayout(buttons)

        status_bar = QHBoxLayout()
        self.status_filter = QComboBox()
        self.status_filter.addItems([ALL_STATUS, *STATUS_VALUES])
        self.status_filter.currentTextChanged.connect(self.apply_status_filter)
        self.mark_pending_btn = QPushButton("标为待定")
        self.mark_usable_btn = QPushButton("标为可用")
        self.mark_bad_btn = QPushButton("标为不准")
        self.export_usable_btn = QPushButton("导出可用片段")
        self.thumb_usable_btn = QPushButton("生成可用缩略图")
        self.export_checklist_btn = QPushButton("导出剪辑清单")
        self.mark_pending_btn.clicked.connect(lambda: self.mark_selected_status(MATCH_STATUS_PENDING))
        self.mark_usable_btn.clicked.connect(lambda: self.mark_selected_status(MATCH_STATUS_USABLE))
        self.mark_bad_btn.clicked.connect(lambda: self.mark_selected_status(MATCH_STATUS_BAD))
        self.export_usable_btn.clicked.connect(self.export_usable_clips)
        self.thumb_usable_btn.clicked.connect(self.export_usable_thumbnails)
        self.export_checklist_btn.clicked.connect(self.export_editing_checklist)
        status_bar.addWidget(QLabel("状态筛选"))
        status_bar.addWidget(self.status_filter)
        for btn in [self.mark_pending_btn, self.mark_usable_btn, self.mark_bad_btn, self.export_usable_btn, self.thumb_usable_btn, self.export_checklist_btn]:
            btn.setEnabled(False)
            status_bar.addWidget(btn)
        status_bar.addStretch(1)
        layout.addLayout(status_bar)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        layout.addWidget(self.progress)

        self.result_table = QTableWidget(0, 12)
        self.result_table.setHorizontalHeaderLabels([
            "状态", "段落", "文案摘要", "类型", "集数", "视频", "开始", "结束", "预览", "分数", "缩略图", "证据文本",
        ])
        self.result_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.result_table, stretch=3)

        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMaximumHeight(180)
        layout.addWidget(self.log_box)

    def choose_video_dir(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "选择视频目录", self.video_dir_edit.text())
        if selected:
            self.video_dir_edit.setText(selected)

    def choose_script(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(self, "选择脚本文案", self.script_edit.text(), "Markdown/Text (*.md *.txt);;All Files (*)")
        if selected:
            self.script_edit.setText(selected)

    def scan(self) -> None:
        self._start_worker(scan_videos, Path(self.video_dir_edit.text()), on_result=self._show_scan_result)

    def index(self, episodes_value: str) -> None:
        episodes = None if episodes_value == "all" else [int(episodes_value)]
        self._start_worker(
            index_videos,
            Path(self.video_dir_edit.text()),
            episodes,
            CONFIG.whisper_model,
            progress="signal",
            on_result=lambda _data: self.log("索引完成"),
        )

    def search(self) -> None:
        """搜索很快（仅数据库查询），不建新线程以防打包后兼容问题。"""
        if self.thread and self.thread.isRunning():
            QMessageBox.warning(self, "任务进行中", "请等待当前任务完成。")
            return
        script_path = Path(self.script_edit.text())
        if not script_path.exists():
            self._show_error(f"脚本文案不存在：{script_path}")
            return
        try:
            self.log("正在搜索文案片段…")
            matches, csv_path, json_path = search_script(script_path, self.top_k_spin.value())
            self._show_search_result((matches, csv_path, json_path))
        except Exception as exc:
            self._show_error(f"搜索失败：{exc}")

    def load_latest_results(self) -> None:
        if self.thread and self.thread.isRunning():
            QMessageBox.warning(self, "任务进行中", "请等待当前任务完成。")
            return
        script_path = Path(self.script_edit.text()) if self.script_edit.text().strip() else None
        try:
            matches, run_id = load_latest_search_results(script_path)
            if not matches:
                QMessageBox.information(self, "没有历史结果", "没有找到可加载的历史搜索结果。")
                return
            self._show_loaded_result((matches, run_id))
        except Exception as exc:
            self._show_error(f"加载历史结果失败：{exc}")

    def _selected_match(self):
        row = self.result_table.currentRow()
        if row < 0 or row >= len(self.current_matches):
            QMessageBox.information(self, "请选择片段", "请先在结果表格里选中一条搜索结果。")
            return None
        return self.current_matches[row]

    def _selected_matches(self) -> list:
        rows = sorted({index.row() for index in self.result_table.selectedIndexes()})
        matches = [self.current_matches[row] for row in rows if 0 <= row < len(self.current_matches)]
        if not matches:
            QMessageBox.information(self, "请选择片段", "请先在结果表格里选中一条或多条搜索结果。")
        return matches

    def preview_selected(self) -> None:
        match = self._selected_match()
        if not match:
            return
        try:
            open_match_preview(match, progress=self.log)
        except Exception as exc:
            self._show_error(str(exc))

    def export_selected_clip(self) -> None:
        match = self._selected_match()
        if not match:
            return
        output_dir = QFileDialog.getExistingDirectory(self, "选择片段导出目录", str(CONFIG.exports_dir / "clips"))
        if not output_dir:
            return
        self._start_worker(
            export_clip,
            match,
            Path(output_dir),
            True,
            progress="signal",
            on_result=lambda path: self.log(f"片段已导出：{path}"),
        )

    def export_selected_clips(self) -> None:
        matches = self._selected_matches()
        if not matches:
            return
        output_dir = QFileDialog.getExistingDirectory(self, "选择批量导出目录", str(CONFIG.exports_dir / "clips"))
        if not output_dir:
            return
        self._start_worker(
            export_clips,
            matches,
            Path(output_dir),
            None,
            progress="signal",
            on_result=lambda paths: self._after_export(paths, matches),
        )

    def export_selected_thumbnails(self) -> None:
        matches = self._selected_matches()
        if not matches:
            return
        output_dir = QFileDialog.getExistingDirectory(self, "选择缩略图导出目录", str(CONFIG.exports_dir / "thumbnails"))
        if not output_dir:
            return
        self._start_worker(
            export_thumbnails,
            matches,
            Path(output_dir),
            480,
            None,
            progress="signal",
            on_result=lambda paths: self._after_thumbnail_export(paths, matches),
        )

    def mark_selected_status(self, status: str) -> None:
        matches = self._selected_matches()
        if not matches:
            return
        match_ids = [match.match_id for match in matches if match.match_id is not None]
        if len(match_ids) != len(matches):
            QMessageBox.warning(self, "无法保存状态", "部分结果缺少数据库 ID，请重新搜索后再标记。")
            return
        try:
            update_search_match_statuses(match_ids, status)
        except Exception as exc:
            self._show_error(f"保存状态失败：{exc}")
            return
        for match in matches:
            match.status = status
        self.apply_status_filter()
        self.log(f"已标记并保存 {len(matches)} 条为：{status}")

    def export_usable_clips(self) -> None:
        matches = [match for match in self.current_matches if match.status == MATCH_STATUS_USABLE]
        if not matches:
            QMessageBox.information(self, "没有可用片段", "请先把候选结果标记为“可用”。")
            return
        output_dir = QFileDialog.getExistingDirectory(self, "选择可用片段导出目录", str(CONFIG.exports_dir / "usable_clips"))
        if not output_dir:
            return
        self._start_worker(
            export_clips,
            matches,
            Path(output_dir),
            None,
            progress="signal",
            on_result=lambda paths: self._after_export(paths, matches),
        )

    def export_usable_thumbnails(self) -> None:
        matches = [match for match in self.current_matches if match.status in EDIT_LIST_STATUSES]
        if not matches:
            QMessageBox.information(self, "没有可用片段", "请先把候选结果标记为“可用”或导出片段。")
            return
        output_dir = QFileDialog.getExistingDirectory(self, "选择可用缩略图导出目录", str(CONFIG.exports_dir / "thumbnails"))
        if not output_dir:
            return
        self._start_worker(
            export_thumbnails,
            matches,
            Path(output_dir),
            480,
            None,
            progress="signal",
            on_result=lambda paths: self._after_thumbnail_export(paths, matches),
        )

    def _after_export(self, paths, matches) -> None:
        for match, path in zip(matches, paths):
            match.status = MATCH_STATUS_EXPORTED
            match.export_path = str(path)
        match_ids = [match.match_id for match in matches if match.match_id is not None]
        if match_ids:
            try:
                update_search_match_statuses(match_ids, MATCH_STATUS_EXPORTED)
            except Exception as exc:
                self._show_error(f"保存导出状态失败：{exc}")
        self.apply_status_filter()
        self.log(f"导出完成：{len(paths)} 个片段")

    def _after_thumbnail_export(self, paths, matches) -> None:
        for match, path in zip(matches, paths):
            match.thumbnail_path = str(path)
        self.apply_status_filter()
        self.log(f"缩略图生成完成：{len(paths)} 张")

    def export_editing_checklist(self) -> None:
        selected = [match for match in self.current_matches if match.status in {"可用", "已导出"}]
        if not selected:
            QMessageBox.information(self, "没有可导出的清单", "请先把候选结果标记为“可用”，或先导出片段。")
            return
        output_dir = QFileDialog.getExistingDirectory(self, "选择剪辑清单导出目录", str(CONFIG.exports_dir / "edit_lists"))
        if not output_dir:
            return
        csv_path, json_path = export_editing_checklist(self.current_matches, Path(output_dir))
        self.log(f"剪辑清单已导出：CSV={csv_path} JSON={json_path}")

    def apply_status_filter(self) -> None:
        if not self.current_matches:
            return
        selected_status = self.status_filter.currentText()
        visible_rows = 0
        for row, match in enumerate(self.current_matches):
            visible = selected_status == ALL_STATUS or match.status == selected_status
            self.result_table.setRowHidden(row, not visible)
            if visible:
                visible_rows += 1
            status_item = self.result_table.item(row, 0)
            if status_item is not None:
                status_item.setText(match.status)
        self.log(f"状态筛选：{selected_status}，显示 {visible_rows} 条")

    def _start_worker(self, fn: Callable, *args, on_result: Callable | None = None, **kwargs) -> None:
        if self.thread and self.thread.isRunning():
            QMessageBox.warning(self, "任务进行中", "请等待当前任务完成。")
            return
        self._set_busy(True)
        self.thread = QThread()
        self.worker = Worker(fn, *args, **kwargs)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.log.connect(self.log)
        self.worker.error.connect(self._show_error)
        if on_result:
            self.worker.result.connect(on_result)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.finished.connect(lambda: self._set_busy(False))
        self.thread.start()

    def _set_busy(self, busy: bool) -> None:
        self.progress.setVisible(busy)
        for btn in [self.scan_btn, self.index_first_btn, self.index_all_btn, self.search_btn]:
            btn.setEnabled(not busy)
        self.top_k_spin.setEnabled(not busy)
        has_matches = bool(self.current_matches)
        self.preview_btn.setEnabled((not busy) and has_matches)
        self.export_clip_btn.setEnabled((not busy) and has_matches)
        self.export_selected_btn.setEnabled((not busy) and has_matches)
        self.thumb_selected_btn.setEnabled((not busy) and has_matches)
        self.status_filter.setEnabled((not busy) and has_matches)
        for btn in [self.mark_pending_btn, self.mark_usable_btn, self.mark_bad_btn, self.export_usable_btn, self.thumb_usable_btn, self.export_checklist_btn]:
            btn.setEnabled((not busy) and has_matches)

    @Slot(str)
    def log(self, message: str) -> None:
        self.log_box.appendPlainText(message)

    @Slot(str)
    def _show_error(self, message: str) -> None:
        self.log(f"错误：{message}")
        QMessageBox.critical(self, "错误", message)

    @Slot(object)
    def _show_scan_result(self, rows) -> None:
        self.current_matches = []
        self.preview_btn.setEnabled(False)
        self.export_clip_btn.setEnabled(False)
        self.export_selected_btn.setEnabled(False)
        self.thumb_selected_btn.setEnabled(False)
        for btn in [self.mark_pending_btn, self.mark_usable_btn, self.mark_bad_btn, self.export_usable_btn, self.thumb_usable_btn, self.export_checklist_btn]:
            btn.setEnabled(False)
        self.result_table.setRowCount(0)
        self.result_table.setColumnCount(6)
        self.result_table.setHorizontalHeaderLabels(["集数", "视频", "时长", "音频", "字幕", "路径"])
        for row in rows:
            r = self.result_table.rowCount()
            self.result_table.insertRow(r)
            values = [
                str(row["episode_no"] or "?"),
                row["filename"],
                ms_to_timecode(row["duration_ms"]),
                "是" if row["has_audio"] else "否",
                "是" if row["has_subtitle"] else "否",
                row["path"],
            ]
            for c, value in enumerate(values):
                self.result_table.setItem(r, c, QTableWidgetItem(str(value)))
        self.log(f"扫描完成：{len(rows)} 个视频")

    @Slot(object)
    def _show_loaded_result(self, payload) -> None:
        matches, run_id = payload if isinstance(payload, tuple) else (payload, None)
        self._populate_matches_table(matches)
        self.log(f"已加载历史搜索结果：run_id={run_id or '?'} / {len(matches)} 条")

    def _populate_matches_table(self, matches) -> None:
        self.current_matches = list(matches)
        self.result_table.setRowCount(0)
        self.result_table.setColumnCount(12)
        self.result_table.setHorizontalHeaderLabels([
            "状态", "段落", "文案摘要", "类型", "集数", "视频", "开始", "结束", "预览", "分数", "缩略图", "证据文本",
        ])
        for match in matches:
            r = self.result_table.rowCount()
            self.result_table.insertRow(r)
            values = [
                match.status,
                match.query_index,
                summarize(match.query_text, 60),
                match.query_type,
                match.episode_no or "?",
                match.video_filename,
                ms_to_timecode(match.start_ms),
                ms_to_timecode(match.end_ms),
                f"{ms_to_timecode(match.preview_start_ms)} - {ms_to_timecode(match.preview_end_ms)}",
                f"{match.final_score:.3f}",
                match.thumbnail_path,
                summarize(match.evidence_text, 120),
            ]
            for c, value in enumerate(values):
                self.result_table.setItem(r, c, QTableWidgetItem(str(value)))
        self.preview_btn.setEnabled(bool(self.current_matches))
        self.export_clip_btn.setEnabled(bool(self.current_matches))
        self.export_selected_btn.setEnabled(bool(self.current_matches))
        self.thumb_selected_btn.setEnabled(bool(self.current_matches))
        for btn in [self.mark_pending_btn, self.mark_usable_btn, self.mark_bad_btn, self.export_usable_btn, self.thumb_usable_btn, self.export_checklist_btn]:
            btn.setEnabled(bool(self.current_matches))
        self.apply_status_filter()

    @Slot(object)
    def _show_search_result(self, payload) -> None:
        matches, csv_path, json_path = payload
        self._populate_matches_table(matches)
        self.log(f"搜索完成：{len(matches)} 条候选。CSV: {csv_path} JSON: {json_path}")

    def closeEvent(self, event) -> None:
        if self.thread and self.thread.isRunning():
            self.thread.quit()
            self.thread.wait(3000)
        event.accept()


def run_app() -> int:
    app = QApplication([])
    win = MainWindow()
    win.show()
    return app.exec()
