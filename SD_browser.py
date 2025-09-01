# -*- coding: utf-8 -*-
import os
import sys
import json
from PIL import Image
from functools import lru_cache
from PyQt5 import QtCore, QtGui, QtWidgets

# --- volitelné moduly ---
try:
    import exifread
except Exception:
    exifread = None

try:
    from sd_parsers import ParserManager
    pm = ParserManager()
except Exception:
    pm = None

SUPPORTED_EXTS = (".png", ".jpg", ".jpeg", ".webp")
THUMB_SIZE = (150, 150)
CONFIG_FILE = "window_config.json"
STATE_FILE = ".image_browser_state.json"


def human_ex(e: Exception) -> str:
    return f"{type(e).__name__}: {e}"


def pil_to_qimage(pil_img: Image.Image) -> QtGui.QImage:
    if pil_img.mode in ("L", "P"):
        pil_img = pil_img.convert("RGBA")
    elif pil_img.mode == "RGB":
        pil_img = pil_img.convert("RGBA")
    data = pil_img.tobytes("raw", "RGBA")
    qimg = QtGui.QImage(data, pil_img.width, pil_img.height, QtGui.QImage.Format_RGBA8888)
    return qimg


class ThumbWorker(QtCore.QObject):
    thumbReady = QtCore.pyqtSignal(int, QtGui.QIcon)

    def __init__(self, files, thumb_size=THUMB_SIZE):
        super().__init__()
        self.files = files
        self.thumb_size = thumb_size
        self._abort = False

    def stop(self):
        self._abort = True

    @QtCore.pyqtSlot()
    def run(self):
        for idx, path in enumerate(self.files):
            if self._abort:
                return
            try:
                icon = self.make_icon(path, self.thumb_size)
                self.thumbReady.emit(idx, icon)
            except Exception:
                continue

    @staticmethod
    @lru_cache(maxsize=4096)
    def make_icon(path, size):
        img = Image.open(path)
        img.thumbnail(size)
        return QtGui.QIcon(QtGui.QPixmap.fromImage(pil_to_qimage(img)))


class ThumbList(QtWidgets.QListWidget):
    stepRequested = QtCore.pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setViewMode(QtWidgets.QListView.ListMode)
        self.setResizeMode(QtWidgets.QListView.Adjust)
        self.setMovement(QtWidgets.QListView.Static)
        self.setIconSize(QtCore.QSize(*THUMB_SIZE))
        self.setUniformItemSizes(True)
        self.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        self.setSpacing(6)
        self.setStyleSheet("""
            QListWidget::item { border: 2px solid transparent; padding: 2px; }
            QListWidget::item:selected { border: 2px solid red; background: rgba(255,0,0,40); }
        """)

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        delta = event.angleDelta().y()
        if delta > 0:
            self.stepRequested.emit(-1)
        elif delta < 0:
            self.stepRequested.emit(1)


class MainWindow(QtWidgets.QMainWindow):
    def wheelEvent(self, event: QtGui.QWheelEvent):
        delta = event.angleDelta().y()
        if delta > 0:
            self.on_step_requested(-1)
        elif delta < 0:
            self.on_step_requested(1)
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SD Browser")
        self.files = []
        self.selected_index = -1

        # --- layout ---
        central = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(central)
        h.setContentsMargins(6, 6, 6, 6)
        h.setSpacing(8)

        # Levý panel
        left_panel = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)
        btn_row = QtWidgets.QHBoxLayout()
        self.btn_select_files = QtWidgets.QPushButton("Select Pictures..")
        self.btn_select_folder = QtWidgets.QPushButton("Select Folder")
        self.btn_reset = QtWidgets.QPushButton("Reset List")
        btn_row.addWidget(self.btn_select_files)
        btn_row.addWidget(self.btn_select_folder)
        btn_row.addWidget(self.btn_reset)
        left_layout.addLayout(btn_row)
        self.list = ThumbList()
        left_layout.addWidget(self.list)


        # Horní řádek s cestou složky
        self.folder_entry = QtWidgets.QLineEdit()
        self.folder_entry.setReadOnly(True)
        left_layout.addWidget(self.folder_entry)

        # Střední panel (preview)
        mid_panel = QtWidgets.QWidget()
        mid_panel.setFixedSize(620, 620)  # fixní velikost panelu
        mid_layout = QtWidgets.QVBoxLayout(mid_panel)
        mid_layout.setContentsMargins(0, 0, 0, 0)
        mid_layout.setSpacing(6)

        self.preview = QtWidgets.QLabel()
        self.preview.setAlignment(QtCore.Qt.AlignCenter)
        self.preview.setFixedSize(620, 620)   # fixní velikost labelu
        self.preview.setStyleSheet("background:#fafafa; border:1px solid #ccc;")
        self.preview.setScaledContents(False)  # pixmapa nebude stretchovat
        mid_layout.addWidget(self.preview)


        # Pravý panel (metadata)
        right_panel = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)
        self.meta = QtWidgets.QPlainTextEdit()
        self.meta.setReadOnly(True)
        right_layout.addWidget(self.meta, 1)

        h.addWidget(left_panel, 0)
        h.addWidget(mid_panel, 0)
        h.addWidget(right_panel, 1)
        central.setLayout(h)
        self.setCentralWidget(central)

        # Akce
        self.btn_select_files.clicked.connect(self.select_files)
        self.btn_select_folder.clicked.connect(self.select_folder)
        self.btn_reset.clicked.connect(self.reset_list)
        self.list.currentRowChanged.connect(self.on_row_changed)
        self.list.stepRequested.connect(self.on_step_requested)
        self.list.setAcceptDrops(True)
        self.list.installEventFilter(self)
        QtWidgets.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key_Up), self, activated=self.step_up)
        QtWidgets.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key_Down), self, activated=self.step_down)

        self._thread = None
        self._worker = None
        self.restore_state()

    def eventFilter(self, obj, event):
        if obj is self.list:
            if event.type() == QtCore.QEvent.DragEnter and event.mimeData().hasUrls():
                event.acceptProposedAction()
                return True
            elif event.type() == QtCore.QEvent.Drop:
                urls = event.mimeData().urls()
                paths = []
                for url in urls:
                    p = url.toLocalFile()
                    if not p:
                        continue
                    if os.path.isdir(p):
                        for root, _, files in os.walk(p):
                            for fn in files:
                                if fn.lower().endswith(SUPPORTED_EXTS):
                                    paths.append(os.path.join(root, fn))
                    else:
                        if p.lower().endswith(SUPPORTED_EXTS):
                            paths.append(p)
                self.handle_dropped_paths(paths)
                event.acceptProposedAction()
                return True
        return super().eventFilter(obj, event)

    def select_files(self):
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(self, "Vyber obrázky", "", "Obrázky (*.png *.jpg *.jpeg *.webp)")
        if paths:
            self.files = list(paths)
            self.folder_entry.setText(os.path.dirname(self.files[0]))
            self.populate_list()
            self.save_state()

    def select_folder(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Vyber složku s obrázky", "")
        if not folder:
            return
        files = []
        for root, _, fns in os.walk(folder):
            for fn in fns:
                if fn.lower().endswith(SUPPORTED_EXTS):
                    files.append(os.path.join(root, fn))
        files.sort(key=lambda p: os.path.getmtime(p))
        if not files:
            QtWidgets.QMessageBox.information(self, "Info", "V adresáři nebyly nalezeny žádné podporované obrázky.")
            return
        self.files = files
        self.folder_entry.setText(folder)
        self.populate_list()
        self.save_state()

    def handle_dropped_paths(self, paths):
        added = False
        for p in paths:
            if p.lower().endswith(SUPPORTED_EXTS) and p not in self.files:
                self.files.append(p)
                added = True
        if added:
            self.populate_list()
            self.save_state()

    def populate_list(self):
        self.stop_worker()
        self.list.clear()
        self.selected_index = -1
        for path in self.files:
            item = QtWidgets.QListWidgetItem(os.path.basename(path))
            item.setToolTip(path)
            item.setIcon(QtGui.QIcon())
            self.list.addItem(item)
        if self.files:
            self.list.setCurrentRow(0)
        self._thread = QtCore.QThread()
        self._worker = ThumbWorker(tuple(self.files))
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.thumbReady.connect(self.on_thumb_ready)
        self._thread.start()

    def stop_worker(self):
        if self._worker:
            self._worker.stop()
        if self._thread:
            self._thread.quit()
            self._thread.wait()
        self._worker = None
        self._thread = None

    @QtCore.pyqtSlot(int, QtGui.QIcon)
    def on_thumb_ready(self, idx, icon):
        if 0 <= idx < self.list.count():
            self.list.item(idx).setIcon(icon)

    # --- Navigace ---
    def on_row_changed(self, row: int):
        if row < 0 or row >= len(self.files):
            return
        QtCore.QTimer.singleShot(0, lambda r=row: self.show_image_if_selected(r))

    def show_image_if_selected(self, row: int):
        if self.list.currentRow() != row:
            return
        self.selected_index = row
        self.show_image(row)

    def refresh_current(self):
        if 0 <= self.selected_index < len(self.files):
            self.show_image(self.selected_index)

    def on_step_requested(self, delta: int):
        if not self.files:
            return
        new_row = max(0, min(self.selected_index + delta if self.selected_index >= 0 else 0, len(self.files) - 1))
        self.list.setCurrentRow(new_row)

    def step_up(self):
        self.on_step_requested(-1)

    def step_down(self):
        self.on_step_requested(1)

    # --- Preview + metadata ---
    def show_image(self, index: int):
        if index < 0 or index >= len(self.files):
            return

        path = self.files[index]
        self.selected_index = index
        self.preview.clear()
        self.meta.clear()
        lines = [f"Soubor: {path}"]

        # Preview
        try:
            img = Image.open(path)
            img.load()
            img.thumbnail((600, 600))
            self.current_pixmap = QtGui.QPixmap.fromImage(pil_to_qimage(img))
            self.preview.setPixmap(self.current_pixmap)
        except Exception as e:
            self.preview.setText(f"Chyba při načtení: {human_ex(e)}")
            self.current_pixmap = None

        # SD parser jen pro JPEG
        try:
            if path.lower().endswith((".jpg", ".jpeg")) and ParserManager:
                pm_local = ParserManager()
                parsed = pm_local.parse(path)
                if parsed and getattr(parsed, "prompts", None):
                    lines.append("\n--- Prompt (sd-parsers) ---")
                    for pr in parsed.prompts:
                        lines.append(str(pr.value))
        except Exception as e:
            lines.append(f"Chyba při načtení SD parseru: {human_ex(e)}")

        # Metadata z PNG a JPEG (bez duplicit)
        try:
            img = Image.open(path)
            img.load()
            if path.lower().endswith(".png"):
                # Jen pozitivní prompt z SD parseru, metadata PNG ignorována
                if ParserManager:
                    pm_local = ParserManager()
                    parsed = pm_local.parse(path)
                    if parsed and getattr(parsed, "prompts", None):
                        # Pouze první prompt (positive)
                        lines.append("\n--- Positive Prompt ---")
                        lines.append(str(parsed.prompts[0].value))
            elif path.lower().endswith((".jpg", ".jpeg")) and exifread:
                with open(path, "rb") as fh:
                    tags = exifread.process_file(fh, details=False)
                if tags:
                    lines.append("\n--- EXIF (exifread) ---")
                    for k, v in tags.items():
                        lines.append(f"{k}: {v}")
        except Exception as e:
            lines.append(f"Chyba při čtení metadat: {human_ex(e)}")

        self.meta.setPlainText("\n".join(lines))
        itm = self.list.item(index)
        if itm:
            self.list.scrollToItem(itm, QtWidgets.QAbstractItemView.PositionAtCenter)


    # --- Reset + stav ---
    def reset_list(self):
        self.stop_worker()
        self.files = []
        self.list.clear()
        self.preview.clear()
        self.meta.clear()
        self.selected_index = -1
        self.save_state()

    def save_state(self):
        try:
            state = {"last_files": self.files, "geometry": str(bytes(self.saveGeometry().toHex()), "ascii")}
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump({"geometry": f"{self.width()}x{self.height()}+{self.x()}+{self.y()}"}, f)
        except Exception:
            pass

    def restore_state(self):
        try:
            if os.path.exists(STATE_FILE):
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                last = data.get("last_files") or []
                geom_hex = data.get("geometry")
                if geom_hex:
                    ba = QtCore.QByteArray.fromHex(bytes(geom_hex, "ascii"))
                    self.restoreGeometry(ba)
                if last:
                    self.files = [p for p in last if os.path.exists(p)]
                    if self.files:
                        self.populate_list()
        except Exception:
            pass

    def closeEvent(self, e: QtGui.QCloseEvent) -> None:
        self.stop_worker()
        self.save_state()
        super().closeEvent(e)


def main():
    app = QtWidgets.QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
