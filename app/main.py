"""Entry point for the Seismic Volume Explorer.

    python -m app.main [volume.npy ...]
"""

from __future__ import annotations

import os
import sys

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

# Allow "python app/main.py" as well as "python -m app.main".
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from app.ui.main_window import MainWindow, excepthook
else:
    from .ui.main_window import MainWindow, excepthook


def apply_dark_theme(app: QApplication) -> None:
    """A dark palette; seismic sections read much better against it."""
    app.setStyle("Fusion")

    palette = QPalette()
    base = QColor("#16181d")
    surface = QColor("#1f2228")
    text = QColor("#e3e6ea")
    accent = QColor("#4f9dff")

    palette.setColor(QPalette.ColorRole.Window, surface)
    palette.setColor(QPalette.ColorRole.WindowText, text)
    palette.setColor(QPalette.ColorRole.Base, base)
    palette.setColor(QPalette.ColorRole.AlternateBase, surface)
    palette.setColor(QPalette.ColorRole.ToolTipBase, surface)
    palette.setColor(QPalette.ColorRole.ToolTipText, text)
    palette.setColor(QPalette.ColorRole.Text, text)
    palette.setColor(QPalette.ColorRole.Button, surface)
    palette.setColor(QPalette.ColorRole.ButtonText, text)
    palette.setColor(QPalette.ColorRole.BrightText, QColor("#ff5252"))
    palette.setColor(QPalette.ColorRole.Highlight, accent)
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#0d0f12"))
    palette.setColor(
        QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#6b7280")
    )
    palette.setColor(
        QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#6b7280")
    )
    app.setPalette(palette)

    app.setStyleSheet(
        """
        QGroupBox {
            border: 1px solid #333842;
            border-radius: 4px;
            margin-top: 9px;
            padding-top: 6px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 8px;
            padding: 0 4px;
            color: #9aa4b2;
        }
        QTabBar::tab { padding: 6px 14px; }
        QDockWidget::title {
            background: #262a31;
            padding: 5px;
        }
        """
    )


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)

    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    app = QApplication(argv)
    app.setApplicationName("Seismic Volume Explorer")
    apply_dark_theme(app)

    sys.excepthook = excepthook

    window = MainWindow()
    window.show()

    for path in argv[1:]:
        if path.lower().endswith(".npy") and os.path.isfile(path):
            window.open_volume(path)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())