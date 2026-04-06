import sys
from pathlib import Path
import pcb2gcode_wrapper as pcb2gcode

from PyQt6.QtWidgets import (
    QApplication,
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

        process_btn = QPushButton("Process")
        process_btn.clicked.connect(self.on_process)
        outer_layout.addWidget(process_btn)

        return tab

    def on_process(self):
        pcb2gcode.process(
            self.front_copper.get_path(),
            self.bottom_copper.get_path(),
            self.outline.get_path(),
            self.drill.get_path())


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
