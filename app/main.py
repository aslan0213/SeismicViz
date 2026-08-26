"""Entry point for the Seismic Volume Explorer application."""

from __future__ import annotations

import os
import sys

# Allow running as both ``python -m app.main`` and ``python app/main.py``.
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from app.ui.main_window import MainWindow, excepthook
else:
    from .ui.main_window import MainWindow, excepthook

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication


def apply_dark_theme(app: QApplication) -> None:
    """Set a dark Fusion palette that works well with seismic displays."""
    app.setStyle("Fusion")

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(30, 32, 36))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(208, 208, 208))
    palette.setColor(QPalette.ColorRole.Base, QColor(25, 27, 30))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(35, 37, 42))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(50, 52, 58))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(220, 220, 220))
    palette.setColor(QPalette.ColorRole.Text, QColor(208, 208, 208))
    palette.setColor(QPalette.ColorRole.Button, QColor(40, 42, 48))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(208, 208, 208))
    palette.setColor(QPalette.ColorRole.BrightText, QColor(255, 90, 90))
    palette.setColor(QPalette.ColorRole.Link, QColor(66, 165, 245))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(42, 130, 218))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(240, 240, 240))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(128, 128, 128))

    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor(110, 110, 110))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(110, 110, 110))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(110, 110, 110))

    app.setPalette(palette)

    app.setStyleSheet(
        """
        QGroupBox { font-weight: bold; border: 1px solid #444; border-radius: 4px;
                     margin-top: 6px; padding-top: 14px; }
        QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 3px; }
        QTabBar::tab { min-width: 90px; padding: 6px 12px; }
        QDockWidget { font-weight: bold; }
        QDockWidget::title { background: #262830; padding: 4px; }
        QStatusBar { background: #1c1e22; }
        """
    )


def main(argv: list[str] | None = None) -> int:
    """Launch the application and return an exit code."""
    if argv is None:
        argv = sys.argv

    # Share the OpenGL context between pyqtgraph (2D) and PyVista (3D).
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)

    app = QApplication(argv)
    app.setApplicationName("Seismic Volume Explorer")

    sys.excepthook = excepthook
    apply_dark_theme(app)

    window = MainWindow()
    window.show()

    # Open any .npy paths passed on the command line.
    for path in argv[1:]:
        if path.lower().endswith(".npy") and os.path.isfile(path):
            window.open_volume(path)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())