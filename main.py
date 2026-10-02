import sys
from pathlib import Path
import pcb2gcode_wrapper as pcb2gcode

from PyQt6.QtCore import (
    QObject,
    QRectF,
    QRunnable,
    QSize,
    Qt,
    QThread,
    QThreadPool,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import QBrush, QColor, QImage, QImageReader, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QLabel,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

OUTPUT_DIR = Path("output")
# SVGs are rasterized once at this size (longest side, px); higher = sharper zoom, slower render
SVG_RENDER_SIZE = 4000


class FilePickerRow(QWidget):
    def __init__(self, label: str, file_filter: str, parent=None):
        super().__init__(parent)
        self.file_filter = file_filter

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.path_edit = QLineEdit()
        self.path_edit.setReadOnly(True)
        self.path_edit.setPlaceholderText(f"Select {label} file...")
        layout.addWidget(self.path_edit)

        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self.browse)
        layout.addWidget(browse_btn)

    def browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select File", "", self.file_filter
        )
        if path:
            self.path_edit.setText(path)

    def get_path(self) -> str:
        return self.path_edit.text()


class Spinner(QWidget):
    def __init__(self, size: int = 24, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.angle = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._rotate)
        self.hide()

    def start(self):
        self.angle = 0
        self.show()
        self.timer.start(16)

    def stop(self):
        self.timer.stop()
        self.hide()

    def _rotate(self):
        self.angle = (self.angle + 6) % 360
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen_width = max(2, self.width() // 8)
        rect = QRectF(self.rect()).adjusted(
            pen_width / 2, pen_width / 2, -pen_width / 2, -pen_width / 2
        )

        color = self.palette().highlight().color()
        track = QColor(color)
        track.setAlpha(50)
        painter.setPen(QPen(track, pen_width))
        painter.drawEllipse(rect)

        pen = QPen(color, pen_width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        # Qt angles are in 1/16 degree, counter-clockwise; negate for clockwise spin
        painter.drawArc(rect, -self.angle * 16, 90 * 16)


class ProcessWorker(QObject):
    finished = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, front, back, outline, drill):
        super().__init__()
        self.args = (front, back, outline, drill)

    def run(self):
        try:
            pcb2gcode.process(*self.args)
        except Exception as e:
            self.failed.emit(str(e))
        finally:
            self.finished.emit()


class SvgRenderSignals(QObject):
    rendered = pyqtSignal(str, QImage)


class SvgRenderTask(QRunnable):
    def __init__(self, path: str, signals: SvgRenderSignals):
        super().__init__()
        self.path = path
        self.signals = signals

    def run(self):
        reader = QImageReader(self.path)
        size = reader.size()
        if size.isValid():
            reader.setScaledSize(
                size.scaled(
                    QSize(SVG_RENDER_SIZE, SVG_RENDER_SIZE),
                    Qt.AspectRatioMode.KeepAspectRatio,
                )
            )
        self.signals.rendered.emit(self.path, reader.read())


class ZoomableView(QGraphicsView):
    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setBackgroundBrush(QBrush(QColor(80, 80, 80)))

    def wheelEvent(self, event):
        factor = 1.25 ** (event.angleDelta().y() / 120)
        self.scale(factor, factor)

    def fit(self):
        rect = self.scene().itemsBoundingRect()
        if not rect.isEmpty():
            self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)


class SvgViewer(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.cache = {}  # path -> (mtime, QPixmap)
        self.pending = set()
        self.signals = SvgRenderSignals()
        self.signals.rendered.connect(self._on_rendered)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)

        toolbar = QHBoxLayout()
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh)
        fit_btn = QPushButton("Fit")
        fit_btn.clicked.connect(lambda: self.view.fit())
        self.spinner = Spinner(18)
        self.status_label = QLabel()
        toolbar.addWidget(refresh_btn)
        toolbar.addWidget(fit_btn)
        toolbar.addWidget(self.spinner)
        toolbar.addWidget(self.status_label)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        self.file_list = QListWidget()
        self.file_list.currentTextChanged.connect(self._show)

        self.scene = QGraphicsScene(self)
        self.pixmap_item = QGraphicsPixmapItem()
        self.pixmap_item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self.scene.addItem(self.pixmap_item)
        self.view = ZoomableView(self.scene)

        splitter = QSplitter()
        splitter.addWidget(self.file_list)
        splitter.addWidget(self.view)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([200, 500])
        layout.addWidget(splitter)

    def refresh(self):
        current = self.file_list.currentItem()
        current_name = current.text() if current else None
        names = sorted(p.name for p in OUTPUT_DIR.glob("*.svg"))

        self.file_list.blockSignals(True)
        self.file_list.clear()
        self.file_list.addItems(names)
        self.file_list.blockSignals(False)

        if not names:
            self.pixmap_item.setPixmap(QPixmap())
            self.status_label.setText(f"No SVG files in {OUTPUT_DIR}/")
            return
        row = names.index(current_name) if current_name in names else 0
        self.file_list.setCurrentRow(row)
        self._show(names[row])

    def _show(self, name: str):
        if not name:
            return
        path = str(OUTPUT_DIR / name)
        try:
            mtime = Path(path).stat().st_mtime
        except FileNotFoundError:
            return

        cached = self.cache.get(path)
        if cached and cached[0] == mtime:
            self._display(cached[1])
            return

        self.pixmap_item.setPixmap(QPixmap())
        self.status_label.setText(f"Rendering {name}...")
        self.spinner.start()
        if path not in self.pending:
            self.pending.add(path)
            QThreadPool.globalInstance().start(SvgRenderTask(path, self.signals))

    def _on_rendered(self, path: str, image: QImage):
        self.pending.discard(path)
        pixmap = QPixmap.fromImage(image)
        try:
            self.cache[path] = (Path(path).stat().st_mtime, pixmap)
        except FileNotFoundError:
            pass

        current = self.file_list.currentItem()
        if current and str(OUTPUT_DIR / current.text()) == path:
            if image.isNull():
                self.spinner.stop()
                self.status_label.setText(f"Failed to render {current.text()}")
            else:
                self._display(pixmap)

    def _display(self, pixmap: QPixmap):
        self.spinner.stop()
        self.status_label.clear()
        self.pixmap_item.setPixmap(pixmap)
        self.scene.setSceneRect(self.pixmap_item.boundingRect())
        self.view.fit()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Carvera PCB Workflow")
        self.resize(900, 600)

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self.tabs.addTab(self._create_gerber_tab(), "Gerber Files")
        self.viewer = SvgViewer()
        self.tabs.addTab(self.viewer, "Results")
        self.viewer.refresh()

    def _create_gerber_tab(self) -> QWidget:
        tab = QWidget()
        outer_layout = QVBoxLayout(tab)
        outer_layout.setContentsMargins(16, 16, 16, 16)
        outer_layout.setSpacing(12)

        form_layout = QFormLayout()
        form_layout.setSpacing(12)

        gerber_filter = "Gerber Files (*.gbr *.gtl *.gbl *.gbs *.gts *.gbo *.gto *.gm1 *.gm2);;All Files (*)"
        drill_filter = "Drill Files (*.drl *.xln *.exc);;All Files (*)"

        self.front_copper = FilePickerRow("Front Copper", gerber_filter)
        self.bottom_copper = FilePickerRow("Bottom Copper", gerber_filter)
        self.outline = FilePickerRow("Outline", gerber_filter)
        self.drill = FilePickerRow("Drill", drill_filter)

        form_layout.addRow("Front Copper:", self.front_copper)
        form_layout.addRow("Bottom Copper:", self.bottom_copper)
        form_layout.addRow("Outline:", self.outline)
        form_layout.addRow("Drill:", self.drill)

        outer_layout.addLayout(form_layout)
        outer_layout.addStretch()

        process_row = QHBoxLayout()
        self.spinner = Spinner()
        self.status_label = QLabel()
        process_row.addWidget(self.spinner)
        process_row.addWidget(self.status_label)
        process_row.addStretch()

        self.process_btn = QPushButton("Process")
        self.process_btn.clicked.connect(self.on_process)
        process_row.addWidget(self.process_btn)
        outer_layout.addLayout(process_row)

        return tab

    def on_process(self):
        self.process_btn.setEnabled(False)
        self.status_label.setText("Processing...")
        self.spinner.start()

        self.thread = QThread(self)
        self.worker = ProcessWorker(
            self.front_copper.get_path(),
            self.bottom_copper.get_path(),
            self.outline.get_path(),
            self.drill.get_path())
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.failed.connect(self.on_process_failed)
        self.worker.finished.connect(self.on_process_finished)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.start()

    def on_process_finished(self):
        self.spinner.stop()
        self.process_btn.setEnabled(True)
        if self.status_label.text() == "Processing...":
            self.status_label.setText("Done")
            self.viewer.refresh()
            self.tabs.setCurrentWidget(self.viewer)

    def on_process_failed(self, message: str):
        self.status_label.setText("Failed")
        QMessageBox.critical(self, "Processing failed", message)


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
