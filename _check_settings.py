"""Throwaway harness: open the Settings dialog so it can be screenshotted."""
import os
import sys
import tempfile

os.environ["APPDATA"] = tempfile.mkdtemp(prefix="appdata_settings_")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import Qt, QTimer                    # noqa: E402
from PySide6.QtGui import QGuiApplication                # noqa: E402
from PySide6.QtWidgets import QApplication               # noqa: E402

from potatoplayer import theme                           # noqa: E402
from potatoplayer.app import SpotiPlayerWindow           # noqa: E402

DEMO = r"C:\Users\win11\AppData\Local\Temp\spoti_demo"

app = QApplication(sys.argv)
app.setStyle("Fusion")
try:
    QGuiApplication.styleHints().setColorScheme(Qt.ColorScheme.Dark)
except Exception:
    pass
app.setStyleSheet(theme.stylesheet())

window = SpotiPlayerWindow(initial_folder=DEMO)
window.resize(1380, 930)
window.show()
QTimer.singleShot(3500, window.open_settings)
app.exec()
