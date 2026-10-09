import re
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
    QDoubleSpinBox,
    QGraphicsPixmapItem,
    QGroupBox,
    QGraphicsScene,
    QGraphicsView,
    QLabel,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

OUTPUT_DIR = Path("output")
CONFIG_PATH = Path("config.cfg")
# SVGs are rasterized once at this size (longest side, px); higher = sharper zoom, slower render
SVG_RENDER_SIZE = 4000

TOOL_DEF_RE = re.compile(r"^T(\d+)\S*?C([\d.]+)")
TOOL_SELECT_RE = re.compile(r"^T(\d+)$")


def format_diameter(d: float) -> str:
    return f"{d:.3f}".rstrip("0").rstrip(".")


def parse_drill_sizes(path: str) -> dict:
    """Return {diameter_mm: hole_count} from an Excellon drill file."""
    scale = 1.0
    tools = {}  # tool number -> diameter in mm
    counts = {}  # tool number -> hole count
    current = None
    with open(path, errors="replace") as f:
        for line in f:
            line = line.strip().upper()
            if line.startswith("INCH") or line == "M72":
                scale = 25.4
            elif line.startswith("METRIC") or line == "M71":
                scale = 1.0
            elif m := TOOL_DEF_RE.match(line):
                tools[int(m.group(1))] = float(m.group(2)) * scale
            elif m := TOOL_SELECT_RE.match(line):
                current = int(m.group(1))
            elif current is not None and line[:1] in ("X", "Y"):
                counts[current] = counts.get(current, 0) + 1

    sizes = {}
    for tool, diameter in tools.items():
        d = round(diameter, 3)
        if d > 0:
            sizes[d] = sizes.get(d, 0) + counts.get(tool, 0)
    return sizes


def write_drills_available(diameters: list):
    """Replace (or add / remove when empty) the drills-available line in config.cfg."""
    lines = CONFIG_PATH.read_text().splitlines()
    value = ",".join(format_diameter(d) for d in sorted(diameters))
    new_line = f"drills-available={value}" if diameters else None

    out, replaced = [], False
    for line in lines:
        if line.strip().startswith("drills-available="):
            if new_line and not replaced:
                out.append(new_line)
            replaced = True
        else:
            out.append(line)
    if new_line and not replaced:
        out.append(new_line)
    CONFIG_PATH.write_text("\n".join(out) + "\n")


class FilePickerRow(QWidget):
    path_changed = pyqtSignal(str)

    def __init__(self, label: str, file_filter: str, parent=None):
        super().__init__(parent)
        self.file_filter = file_filter

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.path_edit = QLineEdit()
        self.path_edit.setReadOnly(True)
        self.path_edit.setPlaceholderText(f"Select {label} file...")
        self.path_edit.textChanged.connect(self.path_changed)
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


class DrillListWidget(QGroupBox):
    """Checkable list of drill diameters; the enabled ones are written to config.cfg."""

    DIAMETER_ROLE = Qt.ItemDataRole.UserRole
    COUNT_ROLE = Qt.ItemDataRole.UserRole + 1
    CUSTOM_ROLE = Qt.ItemDataRole.UserRole + 2

    def __init__(self, parent=None):
        super().__init__("Available Drills", parent)
        layout = QVBoxLayout(self)

        self.info_label = QLabel("Select a drill file to list its hole sizes.")
        layout.addWidget(self.info_label)

        self.list = QListWidget()
        self.list.itemChanged.connect(self._save)
        self.list.currentItemChanged.connect(self._update_buttons)
        layout.addWidget(self.list)

        add_row = QHBoxLayout()
        self.diameter_spin = QDoubleSpinBox()
        self.diameter_spin.setRange(0.05, 10.0)
        self.diameter_spin.setDecimals(2)
        self.diameter_spin.setSingleStep(0.05)
        self.diameter_spin.setValue(1.0)
        self.diameter_spin.setSuffix(" mm")
        add_btn = QPushButton("Add Drill")
        add_btn.clicked.connect(self._add_custom)
        self.remove_btn = QPushButton("Remove")
        self.remove_btn.setEnabled(False)
        self.remove_btn.clicked.connect(self._remove_selected)
        add_row.addWidget(self.diameter_spin)
        add_row.addWidget(add_btn)
        add_row.addWidget(self.remove_btn)
        add_row.addStretch()
        layout.addLayout(add_row)

    def load_drill_file(self, path: str):
        # user-added drills survive loading another drill file
        custom = [e for e in self._entries() if e[3]]
        sizes = {}
        if path:
            try:
                sizes = parse_drill_sizes(path)
            except OSError as e:
                QMessageBox.warning(self, "Drill file", f"Cannot read drill file:\n{e}")
        if sizes:
            self.info_label.setText(
                f"Found {len(sizes)} hole size(s). Uncheck drills you don't have and add "
                "the ones you do; holes are rounded to the nearest available drill."
            )
        else:
            self.info_label.setText("Select a drill file to list its hole sizes.")

        entries = [(d, count, True, False) for d, count in sizes.items()]
        entries += [e for e in custom if e[0] not in sizes]
        self._rebuild(entries)
        self._save()

    def _entries(self) -> list:
        """List of (diameter, hole_count|None, enabled, custom)."""
        result = []
        for i in range(self.list.count()):
            item = self.list.item(i)
            result.append((
                item.data(self.DIAMETER_ROLE),
                item.data(self.COUNT_ROLE),
                item.checkState() == Qt.CheckState.Checked,
                bool(item.data(self.CUSTOM_ROLE)),
            ))
        return result

    def _rebuild(self, entries):
        self.list.blockSignals(True)
        self.list.clear()
        for d, count, enabled, custom in sorted(entries, key=lambda e: e[0]):
            text = f"{format_diameter(d)} mm"
            if custom:
                text += "  (added)"
            elif count is not None:
                text += f"  ({count} hole{'' if count == 1 else 's'})"
            item = QListWidgetItem(text)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if enabled else Qt.CheckState.Unchecked)
            item.setData(self.DIAMETER_ROLE, d)
            item.setData(self.COUNT_ROLE, count)
            item.setData(self.CUSTOM_ROLE, custom)
            self.list.addItem(item)
        self.list.blockSignals(False)
        self._update_buttons()

    def _add_custom(self):
        d = round(self.diameter_spin.value(), 3)
        entries = self._entries()
        for i, (ed, count, _, custom) in enumerate(entries):
            if ed == d:
                entries[i] = (ed, count, True, custom)  # already listed: just re-enable
                break
        else:
            entries.append((d, None, True, True))
        self._rebuild(entries)
        self._save()

    def _remove_selected(self):
        item = self.list.currentItem()
        if item and item.data(self.CUSTOM_ROLE):
            self.list.takeItem(self.list.row(item))
            self._save()

    def _update_buttons(self, *_):
        item = self.list.currentItem()
        self.remove_btn.setEnabled(bool(item and item.data(self.CUSTOM_ROLE)))

    def _save(self, *_):
        if self.list.count() == 0:
            return  # nothing loaded yet; keep whatever config.cfg has
        try:
            write_drills_available([e[0] for e in self._entries() if e[2]])
        except OSError as e:
            QMessageBox.warning(self, "Config", f"Cannot write {CONFIG_PATH}:\n{e}")


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

        self.drill_list = DrillListWidget()
        self.drill.path_changed.connect(self.drill_list.load_drill_file)
        outer_layout.addWidget(self.drill_list, 1)

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
