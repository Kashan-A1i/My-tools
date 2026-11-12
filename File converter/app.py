"""
File Converter - Simple, Clean Bulk PPT to PDF & Multi-Format Converter
Built with PyQt6
"""

import os
import sys
import time
from pathlib import Path
from typing import List, Dict, Optional

# Ensure local module imports work regardless of the caller's working directory
_PROJECT_DIR = str(Path(__file__).resolve().parent)
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QDragEnterEvent, QDropEvent
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QFileDialog, QTableWidget,
    QTableWidgetItem, QHeaderView, QProgressBar, QCheckBox,
    QLineEdit, QFrame, QMessageBox, QAbstractItemView
)

from converter_engine import (
    ConversionEngine, ALL_INPUT_EXTENSIONS, ALL_TARGET_FORMATS, can_convert
)


def format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"


class BatchConversionWorker(QThread):
    """Background worker for bulk file conversion."""
    item_started = pyqtSignal(int, str)
    item_finished = pyqtSignal(int, bool, str, str)  # row, success, out_path, status_text
    progress_updated = pyqtSignal(int, int)          # current, total
    batch_completed = pyqtSignal(int, int, float)    # success_count, fail_count, elapsed_sec

    def __init__(
        self,
        items: List[Dict],
        target_ext: str,
        output_dir: Optional[str],
        merge_pdf: bool
    ):
        super().__init__()
        self.items = items
        self.target_ext = target_ext.lower().lstrip(".")
        self.output_dir = output_dir
        self.merge_pdf = merge_pdf
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        start_time = time.time()
        total = len(self.items)
        success_count = 0
        fail_count = 0

        with ConversionEngine(dpi=200) as engine:
            if self.merge_pdf and self.target_ext == "pdf" and total > 1:
                try:
                    for item in self.items:
                        self.item_started.emit(item["row"], "Merging...")
                    first_path = Path(self.items[0]["path"])
                    out_folder = Path(self.output_dir) if self.output_dir else first_path.parent
                    out_folder.mkdir(parents=True, exist_ok=True)
                    merged_out = out_folder / f"Merged_{int(time.time())}.pdf"

                    src_paths = [it["path"] for it in self.items]
                    res_path = engine.merge_to_single_pdf(src_paths, str(merged_out))

                    for idx, item in enumerate(self.items, 1):
                        self.item_finished.emit(item["row"], True, res_path, "✓ Merged (Double-click to open)")
                        self.progress_updated.emit(idx, total)

                    success_count = total
                except Exception as e:
                    fail_count = total
                    for item in self.items:
                        self.item_finished.emit(item["row"], False, "", f"✗ Failed: {e}")
            else:
                for idx, item in enumerate(self.items, 1):
                    if self._cancelled:
                        break

                    row = item["row"]
                    src_path = item["path"]
                    src_ext = Path(src_path).suffix.lower().lstrip(".")

                    self.item_started.emit(row, "Converting...")

                    if not can_convert(src_ext, self.target_ext):
                        self.item_finished.emit(
                            row, False, "", f"✗ Cannot convert .{src_ext} to .{self.target_ext}"
                        )
                        fail_count += 1
                        self.progress_updated.emit(idx, total)
                        continue

                    try:
                        out_path = engine.convert_file(
                            src_path=src_path,
                            dst_ext=self.target_ext,
                            output_dir=self.output_dir,
                            overwrite=True
                        )
                        self.item_finished.emit(row, True, out_path, "✓ Done (Double-click to open)")
                        success_count += 1
                    except Exception as e:
                        err = str(e).splitlines()[0] if str(e) else "Conversion error"
                        self.item_finished.emit(row, False, "", f"✗ {err[:60]}")
                        fail_count += 1

                    self.progress_updated.emit(idx, total)

        elapsed = time.time() - start_time
        self.batch_completed.emit(success_count, fail_count, elapsed)


class DropZone(QFrame):
    """Simple drag-and-drop box with Add Files and Add Folder buttons."""
    files_dropped = pyqtSignal(list)
    add_files_clicked = pyqtSignal()
    add_folder_clicked = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setObjectName("DropZone")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(16)

        text_col = QVBoxLayout()
        text_col.setSpacing(4)
        title = QLabel("Step 1: Add or Drag & Drop Files Here")
        title.setObjectName("StepTitle")
        sub = QLabel("Supports PowerPoint (.pptx, .ppt), PDF, Word, Excel, Images, CSV, and Text")
        sub.setObjectName("StepSub")
        text_col.addWidget(title)
        text_col.addWidget(sub)

        layout.addLayout(text_col, stretch=1)

        btn_files = QPushButton("+ Add Files")
        btn_files.setObjectName("AddBtn")
        btn_files.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_files.clicked.connect(self.add_files_clicked.emit)

        btn_folder = QPushButton("+ Add Folder")
        btn_folder.setObjectName("AddSecondaryBtn")
        btn_folder.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_folder.clicked.connect(self.add_folder_clicked.emit)

        layout.addWidget(btn_files)
        layout.addWidget(btn_folder)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.setProperty("dragActive", True)
            self.style().unpolish(self)
            self.style().polish(self)

    def dragLeaveEvent(self, event):
        self.setProperty("dragActive", False)
        self.style().unpolish(self)
        self.style().polish(self)

    def dropEvent(self, event: QDropEvent):
        self.setProperty("dragActive", False)
        self.style().unpolish(self)
        self.style().polish(self)
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.files_dropped.emit(paths)


class OmniConvertApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("File Converter — PPT to PDF & Bulk File Converter")
        self.resize(860, 600)
        self.setMinimumSize(720, 500)

        self.worker: Optional[BatchConversionWorker] = None
        self.file_records: Dict[str, str] = {}
        self.custom_output_dir: str = ""

        self._init_ui()
        self._apply_stylesheet()

    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        # STEP 1: Drop Zone
        self.drop_zone = DropZone()
        self.drop_zone.files_dropped.connect(self.add_paths)
        self.drop_zone.add_files_clicked.connect(self.browse_files)
        self.drop_zone.add_folder_clicked.connect(self.browse_folder)
        layout.addWidget(self.drop_zone)

        # File List Header
        list_header = QHBoxLayout()
        self.lbl_count = QLabel("0 files selected")
        self.lbl_count.setObjectName("CountLabel")
        list_header.addWidget(self.lbl_count)
        list_header.addStretch()

        self.btn_remove = QPushButton("Remove Selected")
        self.btn_remove.setObjectName("TextBtn")
        self.btn_remove.clicked.connect(self.remove_selected)

        self.btn_clear = QPushButton("Clear List")
        self.btn_clear.setObjectName("TextBtn")
        self.btn_clear.clicked.connect(self.clear_all)

        list_header.addWidget(self.btn_remove)
        list_header.addWidget(self.btn_clear)
        layout.addLayout(list_header)

        # File Table (Clean 4 columns)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["File Name", "Type", "Size", "Status"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.doubleClicked.connect(self._on_row_double_click)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)

        layout.addWidget(self.table, stretch=1)

        # STEP 2 & 3: Bottom Action Card
        bottom_card = QFrame()
        bottom_card.setObjectName("BottomCard")
        bottom_layout = QVBoxLayout(bottom_card)
        bottom_layout.setContentsMargins(18, 16, 18, 16)
        bottom_layout.setSpacing(14)

        # Settings Row
        settings_row = QHBoxLayout()
        settings_row.setSpacing(12)

        lbl_to = QLabel("Step 2: Convert To:")
        lbl_to.setObjectName("StepTitle")
        settings_row.addWidget(lbl_to)

        self.cmb_target = QComboBox()
        self.cmb_target.setMinimumWidth(240)
        for ext, desc in ALL_TARGET_FORMATS:
            self.cmb_target.addItem(f"{ext.upper()}  —  {desc}", ext)
        self.cmb_target.currentIndexChanged.connect(self._on_format_changed)
        settings_row.addWidget(self.cmb_target)

        self.chk_merge = QCheckBox("Combine into one PDF")
        self.chk_merge.setToolTip("Merge all files in the list into a single PDF file")
        settings_row.addWidget(self.chk_merge)

        settings_row.addStretch()

        lbl_save = QLabel("Save to:")
        lbl_save.setObjectName("SubtleLabel")
        settings_row.addWidget(lbl_save)

        self.lbl_output_path = QLabel("Same folder as original files")
        self.lbl_output_path.setObjectName("FolderBadge")
        settings_row.addWidget(self.lbl_output_path)

        btn_change_folder = QPushButton("Change...")
        btn_change_folder.setObjectName("SmallBtn")
        btn_change_folder.clicked.connect(self.choose_output_folder)
        settings_row.addWidget(btn_change_folder)

        self.btn_reset_folder = QPushButton("Reset")
        self.btn_reset_folder.setObjectName("SmallBtn")
        self.btn_reset_folder.setVisible(False)
        self.btn_reset_folder.clicked.connect(self.reset_output_folder)
        settings_row.addWidget(self.btn_reset_folder)

        bottom_layout.addLayout(settings_row)

        # Convert Action Row
        action_row = QHBoxLayout()
        action_row.setSpacing(12)

        self.progress = QProgressBar()
        self.progress.setValue(0)
        self.progress.setFormat("Ready")
        action_row.addWidget(self.progress, stretch=1)

        self.btn_open_folder = QPushButton("Open Output Folder")
        self.btn_open_folder.setObjectName("AddSecondaryBtn")
        self.btn_open_folder.clicked.connect(self.open_output_folder)
        action_row.addWidget(self.btn_open_folder)

        self.btn_convert = QPushButton("Step 3: Convert Files")
        self.btn_convert.setObjectName("ConvertBtn")
        self.btn_convert.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_convert.setMinimumWidth(200)
        self.btn_convert.setMinimumHeight(42)
        self.btn_convert.clicked.connect(self.toggle_conversion)
        action_row.addWidget(self.btn_convert)

        bottom_layout.addLayout(action_row)
        layout.addWidget(bottom_card)

    def _apply_stylesheet(self):
        self.setStyleSheet("""
            QMainWindow, QWidget {
                background-color: #f8fafc;
                color: #1e293b;
                font-family: 'Segoe UI', system-ui, sans-serif;
                font-size: 13px;
            }
            #DropZone {
                background-color: #ffffff;
                border: 2px dashed #94a3b8;
                border-radius: 10px;
            }
            #DropZone[dragActive="true"] {
                background-color: #eff6ff;
                border: 2px dashed #2563eb;
            }
            #BottomCard {
                background-color: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 10px;
            }
            #StepTitle {
                font-size: 14px;
                font-weight: 700;
                color: #0f172a;
            }
            #StepSub, #SubtleLabel {
                font-size: 12px;
                color: #64748b;
            }
            #CountLabel {
                font-size: 13px;
                font-weight: 600;
                color: #475569;
            }
            #FolderBadge {
                background-color: #f1f5f9;
                color: #334155;
                border-radius: 5px;
                padding: 4px 10px;
                font-size: 12px;
            }
            QPushButton#AddBtn {
                background-color: #2563eb;
                color: #ffffff;
                border: none;
                border-radius: 7px;
                padding: 9px 18px;
                font-weight: 600;
            }
            QPushButton#AddBtn:hover {
                background-color: #1d4ed8;
            }
            QPushButton#AddSecondaryBtn {
                background-color: #f1f5f9;
                color: #1e293b;
                border: 1px solid #cbd5e1;
                border-radius: 7px;
                padding: 9px 16px;
                font-weight: 600;
            }
            QPushButton#AddSecondaryBtn:hover {
                background-color: #e2e8f0;
            }
            QPushButton#TextBtn, QPushButton#SmallBtn {
                background-color: transparent;
                color: #475569;
                border: 1px solid #cbd5e1;
                border-radius: 5px;
                padding: 4px 10px;
                font-size: 12px;
            }
            QPushButton#TextBtn:hover, QPushButton#SmallBtn:hover {
                background-color: #e2e8f0;
                color: #0f172a;
            }
            QPushButton#ConvertBtn {
                background-color: #16a34a;
                color: #ffffff;
                border: none;
                border-radius: 8px;
                font-size: 14px;
                font-weight: 700;
                padding: 8px 22px;
            }
            QPushButton#ConvertBtn:hover {
                background-color: #15803d;
            }
            QPushButton#ConvertBtn[stopping="true"] {
                background-color: #dc2626;
            }
            QTableWidget {
                background-color: #ffffff;
                alternate-background-color: #f8fafc;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                color: #1e293b;
                selection-background-color: #dbeafe;
                selection-color: #1e3a8a;
            }
            QHeaderView::section {
                background-color: #f1f5f9;
                color: #475569;
                font-weight: 600;
                padding: 8px 12px;
                border: none;
                border-bottom: 1px solid #e2e8f0;
            }
            QComboBox {
                background-color: #ffffff;
                color: #0f172a;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                padding: 6px 10px;
                font-weight: 500;
            }
            QComboBox:hover {
                border-color: #2563eb;
            }
            QProgressBar {
                background-color: #f1f5f9;
                border: 1px solid #e2e8f0;
                border-radius: 7px;
                text-align: center;
                color: #0f172a;
                font-weight: 600;
                min-height: 26px;
            }
            QProgressBar::chunk {
                background-color: #2563eb;
                border-radius: 6px;
            }
        """)

    # =========================================================================
    # ACTIONS
    # =========================================================================
    def _on_format_changed(self):
        is_pdf = self.cmb_target.currentData() == "pdf"
        self.chk_merge.setVisible(is_pdf)
        if not is_pdf:
            self.chk_merge.setChecked(False)

    def choose_output_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose Output Folder")
        if folder:
            self.custom_output_dir = folder
            short_name = Path(folder).name or folder
            self.lbl_output_path.setText(f"Folder: {short_name}")
            self.lbl_output_path.setToolTip(folder)
            self.btn_reset_folder.setVisible(True)

    def reset_output_folder(self):
        self.custom_output_dir = ""
        self.lbl_output_path.setText("Same folder as original files")
        self.lbl_output_path.setToolTip("")
        self.btn_reset_folder.setVisible(False)

    def open_output_folder(self):
        if self.custom_output_dir and os.path.isdir(self.custom_output_dir):
            os.startfile(self.custom_output_dir)
            return
        if self.table.rowCount() > 0:
            first_path = self.table.item(0, 0).data(Qt.ItemDataRole.UserRole)
            if first_path and os.path.exists(first_path):
                os.startfile(str(Path(first_path).parent))
                return
        os.startfile(os.getcwd())

    def browse_files(self):
        ext_filters = " ".join(f"*.{ext}" for ext in ALL_INPUT_EXTENSIONS)
        filter_str = (
            f"Supported Files ({ext_filters});;"
            "PowerPoint (*.pptx *.ppt);;"
            "PDF (*.pdf);;"
            "Word (*.docx *.doc *.rtf);;"
            "Excel & CSV (*.xlsx *.xls *.csv *.json);;"
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.tiff);;"
            "All Files (*.*)"
        )
        files, _ = QFileDialog.getOpenFileNames(self, "Select Files", "", filter_str)
        if files:
            self.add_paths(files)

    def browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Folder to Add Files From")
        if folder:
            self.add_paths([folder])

    def add_paths(self, paths: List[str]):
        valid_exts = set(ALL_INPUT_EXTENSIONS)
        collected = []

        for p in paths:
            p_obj = Path(p)
            if p_obj.is_file():
                collected.append(p_obj)
            elif p_obj.is_dir():
                for sub in p_obj.rglob("*"):
                    if sub.is_file() and not sub.name.startswith("~$"):
                        if sub.suffix.lower().lstrip(".") in valid_exts:
                            collected.append(sub)

        for fpath in collected:
            norm = os.path.normcase(os.path.abspath(str(fpath)))
            ext = fpath.suffix.lower().lstrip(".")
            if ext not in valid_exts or norm in self.file_records:
                continue

            try:
                size_str = format_size(fpath.stat().st_size)
            except Exception:
                size_str = "-"

            row = self.table.rowCount()
            self.table.insertRow(row)

            name_item = QTableWidgetItem(fpath.name)
            name_item.setData(Qt.ItemDataRole.UserRole, str(fpath.resolve()))
            name_item.setToolTip(str(fpath.resolve()))

            type_item = QTableWidgetItem(ext.upper())
            type_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            size_item = QTableWidgetItem(size_str)
            size_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

            status_item = QTableWidgetItem("Ready")
            status_item.setForeground(QColor("#64748b"))

            self.table.setItem(row, 0, name_item)
            self.table.setItem(row, 1, type_item)
            self.table.setItem(row, 2, size_item)
            self.table.setItem(row, 3, status_item)

            self.file_records[norm] = str(fpath.resolve())

        self._update_count()

    def remove_selected(self):
        rows = sorted({idx.row() for idx in self.table.selectedIndexes()}, reverse=True)
        for row in rows:
            path = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            self.file_records.pop(os.path.normcase(os.path.abspath(path)), None)
            self.table.removeRow(row)
        self._update_count()

    def clear_all(self):
        self.table.setRowCount(0)
        self.file_records.clear()
        self.progress.setValue(0)
        self.progress.setFormat("Ready")
        self._update_count()

    def _update_count(self):
        n = self.table.rowCount()
        self.lbl_count.setText(f"{n} file{'s' if n != 1 else ''} selected")

    def _on_row_double_click(self, index):
        row = index.row()
        out_path = self.table.item(row, 3).data(Qt.ItemDataRole.UserRole)
        src_path = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        target = out_path if (out_path and os.path.exists(out_path)) else src_path
        if target and os.path.exists(target):
            os.startfile(target)

    # =========================================================================
    # CONVERSION START / STOP
    # =========================================================================
    def toggle_conversion(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.btn_convert.setEnabled(False)
            self.btn_convert.setText("Stopping...")
            return

        row_count = self.table.rowCount()
        if row_count == 0:
            QMessageBox.information(self, "No Files", "Please add files or a folder first.")
            return

        target_ext = self.cmb_target.currentData()
        merge_pdf = self.chk_merge.isChecked() and target_ext == "pdf"
        output_dir = self.custom_output_dir if self.custom_output_dir else None

        items = [
            {"row": r, "path": self.table.item(r, 0).data(Qt.ItemDataRole.UserRole)}
            for r in range(row_count)
        ]

        self.progress.setMaximum(row_count)
        self.progress.setValue(0)
        self.progress.setFormat("Converting %v of %m (%p%)")

        self.btn_convert.setText("Stop")
        self.btn_convert.setProperty("stopping", True)
        self.btn_convert.style().unpolish(self.btn_convert)
        self.btn_convert.style().polish(self.btn_convert)

        self.worker = BatchConversionWorker(
            items=items,
            target_ext=target_ext,
            output_dir=output_dir,
            merge_pdf=merge_pdf
        )
        self.worker.item_started.connect(self._on_item_started)
        self.worker.item_finished.connect(self._on_item_finished)
        self.worker.progress_updated.connect(self._on_progress_updated)
        self.worker.batch_completed.connect(self._on_batch_completed)
        self.worker.start()

    def _on_item_started(self, row: int, text: str):
        if row < self.table.rowCount():
            it = self.table.item(row, 3)
            it.setText(text)
            it.setForeground(QColor("#d97706"))

    def _on_item_finished(self, row: int, success: bool, out_path: str, status_text: str):
        if row < self.table.rowCount():
            it = self.table.item(row, 3)
            it.setText(status_text)
            it.setForeground(QColor("#16a34a") if success else QColor("#dc2626"))
            if success and out_path:
                it.setData(Qt.ItemDataRole.UserRole, out_path)
                it.setToolTip(f"Double-click to open: {out_path}")

    def _on_progress_updated(self, current: int, total: int):
        self.progress.setMaximum(total)
        self.progress.setValue(current)

    def _on_batch_completed(self, success_count: int, fail_count: int, elapsed: float):
        self.btn_convert.setEnabled(True)
        self.btn_convert.setText("Step 3: Convert Files")
        self.btn_convert.setProperty("stopping", False)
        self.btn_convert.style().unpolish(self.btn_convert)
        self.btn_convert.style().polish(self.btn_convert)

        if fail_count == 0:
            self.progress.setFormat(f"✓ Completed {success_count} file(s) in {elapsed:.1f}s")
        else:
            self.progress.setFormat(f"Finished: {success_count} done, {fail_count} failed")


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("File Converter")
    window = OmniConvertApp()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
