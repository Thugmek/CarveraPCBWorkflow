import sys
from pathlib import Path
import pcb2gcode_wrapper as pcb2gcode

from PyQt6.QtCore import QObject, QRectF, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (
    QApplication,
    QLabel,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)


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


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Carvera PCB Workflow")
        self.resize(700, 500)

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self.tabs.addTab(self._create_gerber_tab(), "Gerber Files")

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
