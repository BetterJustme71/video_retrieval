from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
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
from src.core.assembler import assemble_clips
from src.core.audio import AssemblyAudioOptions
from src.core.best_match import pick_best_matches
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
        self.video_dir_edit = QLineEdit(str(CONFIG.get_default_video_dir()))
        self.script_edit = QLineEdit(str(CONFIG.get_default_script_path()))
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

        audio_settings = CONFIG.get_assembly_audio_settings()
        audio_group = QGroupBox("组装音频")
        audio_layout = QGridLayout(audio_group)
        self.tts_enabled_check = QCheckBox("生成 AI 旁白")
        self.tts_enabled_check.setChecked(bool(audio_settings["tts_enabled"]))
        self.tts_voice_combo = QComboBox()
        self.tts_voice_combo.setEditable(True)
        self.tts_voice_combo.addItems([
            "zh-CN-XiaoxiaoNeural",
            "zh-CN-YunxiNeural",
            "zh-CN-YunjianNeural",
            "zh-CN-XiaoyiNeural",
            "zh-CN-YunyangNeural",
        ])
        self.tts_voice_combo.setCurrentText(str(audio_settings["tts_voice"]))
        self.tts_rate_spin = QSpinBox()
        self.tts_rate_spin.setRange(-50, 100)
        self.tts_rate_spin.setSuffix("%")
        self.tts_rate_spin.setValue(int(audio_settings["tts_rate"]))
        self.bgm_enabled_check = QCheckBox("混入背景音乐")
        self.bgm_enabled_check.setChecked(bool(audio_settings["bgm_enabled"]))
        self.bgm_path_edit = QLineEdit(str(audio_settings["bgm_path"]))
        self.bgm_btn = QPushButton("选择 BGM")
        self.bgm_clear_btn = QPushButton("清空")
        self.bgm_volume_spin = QSpinBox()
        self.bgm_volume_spin.setRange(0, 100)
        self.bgm_volume_spin.setSuffix("%")
        self.bgm_volume_spin.setValue(int(audio_settings["bgm_volume_percent"]))
        self.bgm_btn.clicked.connect(self.choose_bgm)
        self.bgm_clear_btn.clicked.connect(self.clear_bgm)
        self.tts_enabled_check.toggled.connect(self._update_audio_controls)
        self.bgm_enabled_check.toggled.connect(self._update_audio_controls)
        audio_layout.addWidget(self.tts_enabled_check, 0, 0)
        audio_layout.addWidget(QLabel("音色"), 0, 1)
        audio_layout.addWidget(self.tts_voice_combo, 0, 2)
        audio_layout.addWidget(QLabel("语速"), 0, 3)
        audio_layout.addWidget(self.tts_rate_spin, 0, 4)
        audio_layout.addWidget(self.bgm_enabled_check, 1, 0)
        audio_layout.addWidget(self.bgm_path_edit, 1, 1, 1, 2)
        audio_layout.addWidget(self.bgm_btn, 1, 3)
        audio_layout.addWidget(self.bgm_clear_btn, 1, 4)
        audio_layout.addWidget(QLabel("BGM 音量"), 1, 5)
        audio_layout.addWidget(self.bgm_volume_spin, 1, 6)
        layout.addWidget(audio_group)
        self._update_audio_controls()

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
        self.cancel_btn = QPushButton("取消任务")
        self.preview_btn = QPushButton("预览选中片段")
        self.export_clip_btn = QPushButton("导出选中片段")
        self.export_selected_btn = QPushButton("批量导出选中")
        self.thumb_selected_btn = QPushButton("生成选中缩略图")
        self.assemble_btn = QPushButton("一键拼接视频")
        self.scan_btn.clicked.connect(self.scan)
        self.index_first_btn.clicked.connect(lambda: self.index("1"))
        self.index_all_btn.clicked.connect(lambda: self.index("all"))
        self.search_btn.clicked.connect(self.search)
        self.load_latest_btn.clicked.connect(self.load_latest_results)
        self.cancel_btn.clicked.connect(self.cancel_current_task)
        self.preview_btn.clicked.connect(self.preview_selected)
        self.export_clip_btn.clicked.connect(self.export_selected_clip)
        self.export_selected_btn.clicked.connect(self.export_selected_clips)
        self.thumb_selected_btn.clicked.connect(self.export_selected_thumbnails)
        self.assemble_btn.clicked.connect(self.assemble_video)
        self.assemble_btn.setEnabled(False)
        self.cancel_btn.setEnabled(False)
        self.preview_btn.setEnabled(False)
        self.export_clip_btn.setEnabled(False)
        self.export_selected_btn.setEnabled(False)
        self.thumb_selected_btn.setEnabled(False)
        for btn in [self.scan_btn, self.index_first_btn, self.index_all_btn, self.search_btn, self.load_latest_btn, self.cancel_btn, self.assemble_btn, self.preview_btn, self.export_clip_btn, self.export_selected_btn, self.thumb_selected_btn]:
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
            CONFIG.save_recent_paths(video_dir=Path(selected), script_path=Path(self.script_edit.text()))

    def choose_script(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(self, "选择脚本文案", self.script_edit.text(), "Markdown/Text (*.md *.txt);;All Files (*)")
        if selected:
            self.script_edit.setText(selected)
            CONFIG.save_recent_paths(video_dir=Path(self.video_dir_edit.text()), script_path=Path(selected))

    def choose_bgm(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(self, "选择背景音乐", self.bgm_path_edit.text() or str(CONFIG.project_root), "Audio Files (*.mp3 *.wav *.m4a *.aac *.flac *.ogg);;All Files (*)")
        if selected:
            self.bgm_path_edit.setText(selected)
            self.bgm_enabled_check.setChecked(True)
            self._save_audio_settings()

    def clear_bgm(self) -> None:
        self.bgm_path_edit.clear()
        self.bgm_enabled_check.setChecked(False)
        self._save_audio_settings()

    def _rate_text(self) -> str:
        return f"{self.tts_rate_spin.value():+d}%"

    def _save_audio_settings(self) -> None:
        CONFIG.save_assembly_audio_settings(
            tts_enabled=self.tts_enabled_check.isChecked(),
            tts_voice=self.tts_voice_combo.currentText(),
            tts_rate=self.tts_rate_spin.value(),
            bgm_enabled=self.bgm_enabled_check.isChecked(),
            bgm_path=self.bgm_path_edit.text(),
            bgm_volume_percent=self.bgm_volume_spin.value(),
        )

    def _build_audio_options(self) -> AssemblyAudioOptions:
        if self.bgm_enabled_check.isChecked() and not self.bgm_path_edit.text().strip():
            raise ValueError("已勾选混入背景音乐，请先选择 BGM 文件。")
        bgm_path = Path(self.bgm_path_edit.text()) if self.bgm_enabled_check.isChecked() and self.bgm_path_edit.text().strip() else None
        options = AssemblyAudioOptions(
            tts_enabled=self.tts_enabled_check.isChecked(),
            tts_voice=self.tts_voice_combo.currentText(),
            tts_rate=self._rate_text(),
            bgm_path=bgm_path,
            bgm_volume=self.bgm_volume_spin.value() / 100,
        )
        options.validate()
        return options

    def _update_audio_controls(self) -> None:
        tts_enabled = self.tts_enabled_check.isChecked()
        bgm_enabled = self.bgm_enabled_check.isChecked()
        self.tts_voice_combo.setEnabled(tts_enabled)
        self.tts_rate_spin.setEnabled(tts_enabled)
        self.bgm_path_edit.setEnabled(bgm_enabled)
        self.bgm_btn.setEnabled(bgm_enabled)
        self.bgm_clear_btn.setEnabled(bgm_enabled or bool(self.bgm_path_edit.text().strip()))
        self.bgm_volume_spin.setEnabled(bgm_enabled)

    def _save_current_paths(self) -> None:
        CONFIG.save_recent_paths(video_dir=Path(self.video_dir_edit.text()), script_path=Path(self.script_edit.text()))

    def scan(self) -> None:
        self._save_current_paths()
        self._start_worker(scan_videos, Path(self.video_dir_edit.text()), on_result=self._show_scan_result)

    def index(self, episodes_value: str) -> None:
        self._save_current_paths()
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
        if self.thread and self.thread.isRunning():
            QMessageBox.warning(self, "任务进行中", "请等待当前任务完成。")
            return
        script_path = Path(self.script_edit.text())
        if not script_path.exists():
            self._show_error(f"脚本文案不存在：{script_path}")
            return
        self._save_current_paths()
        self.log("正在搜索文案片段…")
        self._start_worker(
            search_script,
            script_path,
            self.top_k_spin.value(),
            progress="signal",
            on_result=self._show_search_result,
        )

    def load_latest_results(self) -> None:
        if self.thread and self.thread.isRunning():
            QMessageBox.warning(self, "任务进行中", "请等待当前任务完成。")
            return
        script_path = Path(self.script_edit.text()) if self.script_edit.text().strip() else None
        self._save_current_paths()
        self.log("正在加载历史搜索结果…")
        self._start_worker(
            load_latest_search_results,
            script_path,
            on_result=self._show_loaded_result,
        )

    def cancel_current_task(self) -> None:
        if self.thread and self.thread.isRunning():
            self.thread.requestInterruption()
            self.thread.quit()
            self.log("已请求取消当前任务；正在执行的外部命令或模型任务可能需要自然结束。")
        else:
            self.log("当前没有正在运行的任务。")

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

    def assemble_video(self) -> None:
        if self.thread and self.thread.isRunning():
            QMessageBox.warning(self, "任务进行中", "请等待当前任务完成。")
            return
        if not self.current_matches:
            QMessageBox.information(self, "没有搜索结果", "请先搜索文案片段。")
            return
        output_dir = QFileDialog.getExistingDirectory(self, "选择组装导出目录", str(CONFIG.exports_dir / "assemblies"))
        if not output_dir:
            return
        try:
            audio_options = self._build_audio_options()
        except Exception as exc:
            self._show_error(str(exc))
            return
        self._save_audio_settings()
        self.log("正在筛选最佳匹配并拼接视频…")
        segments = pick_best_matches(self.current_matches, None)
        if not segments:
            self._show_error("没有足够的最佳匹配，无法组装。")
            return
        self._start_worker(
            assemble_clips,
            segments,
            Path(output_dir),
            "assembled",
            progress="signal",
            audio_options=audio_options,
            on_result=lambda result: self.log(f"拼接完成：{result.get('video_path', '?')}  字幕：{result.get('srt_path', '?')}"),
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
        for btn in [self.scan_btn, self.index_first_btn, self.index_all_btn, self.search_btn, self.load_latest_btn]:
            btn.setEnabled(not busy)
        self.cancel_btn.setEnabled(busy)
        self.top_k_spin.setEnabled(not busy)
        audio_controls = [
            self.tts_enabled_check,
            self.tts_voice_combo,
            self.tts_rate_spin,
            self.bgm_enabled_check,
            self.bgm_path_edit,
            self.bgm_btn,
            self.bgm_clear_btn,
            self.bgm_volume_spin,
        ]
        for control in audio_controls:
            control.setEnabled(not busy)
        if not busy:
            self._update_audio_controls()
        has_matches = bool(self.current_matches)
        self.preview_btn.setEnabled((not busy) and has_matches)
        self.export_clip_btn.setEnabled((not busy) and has_matches)
        self.export_selected_btn.setEnabled((not busy) and has_matches)
        self.thumb_selected_btn.setEnabled((not busy) and has_matches)
        self.assemble_btn.setEnabled((not busy) and has_matches)
        self.status_filter.setEnabled((not busy) and has_matches)
        for btn in [self.mark_pending_btn, self.mark_usable_btn, self.mark_bad_btn, self.export_usable_btn, self.thumb_usable_btn, self.export_checklist_btn, self.assemble_btn]:
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
        if not matches:
            QMessageBox.information(self, "没有历史结果", "没有找到可加载的历史搜索结果。")
            self.log("没有找到可加载的历史搜索结果。")
            return
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
        self._save_audio_settings()
        if self.thread and self.thread.isRunning():
            self.thread.quit()
            self.thread.wait(3000)
        event.accept()


def run_app() -> int:
    app = QApplication([])
    win = MainWindow()
    win.show()
    return app.exec()
