"""Spectrum analysis panel — plots the average amplitude spectrum from the C# module."""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.spectrum_client import WINDOW_NAMES, SpectrumClient, SpectrumError


class SpectrumWorker(QThread):
    """Runs the spectrum call off the GUI thread."""

    completed = pyqtSignal(list)    # list of (label, amplitudes)
    failed = pyqtSignal(str)

    def __init__(self, client: SpectrumClient, jobs: list, dt: float, window: int, parent=None):
        super().__init__(parent)
        self.client = client
        self.jobs = jobs        # [(label, section), ...]
        self.dt = dt
        self.window = window

    def run(self) -> None:
        results = []
        try:
            for label, section in self.jobs:
                freqs, amps = self.client.average_spectrum(section, self.dt, self.window)
                results.append((label, freqs, amps))
        except SpectrumError as exc:
            self.failed.emit(str(exc))
            return
        except Exception as exc:
            self.failed.emit("%s: %s" % (type(exc).__name__, exc))
            return
        self.completed.emit(results)


class SpectrumPanel(QWidget):
    """Plots the frequency spectrum and provides controls for the analysis."""

    requestSections = pyqtSignal()

    def __init__(self, client: SpectrumClient, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.client = client
        self._worker: SpectrumWorker | None = None
        self._results: list = []
        self._pending_update = False

        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # -- toolbar ----------------------------------------------------------
        toolbar = QHBoxLayout()

        self.compute_button = QPushButton("Compute spectrum")
        self.compute_button.clicked.connect(self._on_compute_clicked)
        toolbar.addWidget(self.compute_button)

        toolbar.addWidget(QLabel("Window:"))
        self.window_combo = QComboBox()
        self.window_combo.addItems(list(WINDOW_NAMES.keys()))
        self.window_combo.setCurrentText("Hann")
        toolbar.addWidget(self.window_combo)

        self.normalise_box = QCheckBox("Normalise")
        self.normalise_box.setToolTip("Scale each curve so the peak is 1.0")
        self.normalise_box.setChecked(False)
        self.normalise_box.toggled.connect(self._redraw)
        toolbar.addWidget(self.normalise_box)

        self.db_box = QCheckBox("dB")
        self.db_box.setToolTip("Show amplitudes in decibels (20·log₁₀)")
        self.db_box.setChecked(False)
        self.db_box.toggled.connect(self._redraw)
        toolbar.addWidget(self.db_box)

        self.auto_box = QCheckBox("Auto update")
        self.auto_box.setToolTip("Recompute when the ROI or slice changes")
        self.auto_box.setChecked(True)
        toolbar.addWidget(self.auto_box)

        toolbar.addStretch(1)
        layout.addLayout(toolbar)

        # -- plot -------------------------------------------------------------
        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setLabel("bottom", "Frequency", units="Hz")
        self.plot_widget.setLabel("left", "Amplitude")
        self.plot_widget.showGrid(x=True, y=True, alpha=0.3)
        self.plot_widget.addLegend(offset=(60, 10))
        layout.addWidget(self.plot_widget, 1)

        # -- status -----------------------------------------------------------
        bottom = QHBoxLayout()

        self.module_label = QLabel(self.client.status_text())
        self.module_label.setStyleSheet("color: #777; font-size: 10px;")
        bottom.addWidget(self.module_label)

        self.status = QLabel("")
        self.status.setStyleSheet("color: #999;")
        bottom.addWidget(self.status, 1)

        self.stats = QLabel("")
        self.stats.setStyleSheet("color: #aaa; font-size: 11px;")
        self.stats.setWordWrap(True)
        bottom.addWidget(self.stats, 1)

        layout.addLayout(bottom)

        # Colours for up to 4 curves.
        self._pens = [
            pg.mkPen("#4fc3f7", width=2),
            pg.mkPen("#ff8a65", width=2),
            pg.mkPen("#81c784", width=2),
            pg.mkPen("#ce93d8", width=2),
        ]

    # --------------------------------------------------------------- public

    def request_update(self) -> None:
        """Called when the slice or ROI changes; triggers recompute if auto is on."""
        if self.auto_box.isChecked():
            self._pending_update = True
            self.requestSections.emit()

    def compute(self, jobs: list, dt: float, note: str = "") -> None:
        """Start a background spectrum computation.

        ``jobs`` is a list of ``(label, section_2d)`` pairs.
        """
        if self._worker is not None and self._worker.isRunning():
            return

        window = WINDOW_NAMES.get(self.window_combo.currentText(), 1)
        worker = SpectrumWorker(self.client, jobs, dt, window, self)
        worker.completed.connect(self._on_completed)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(self._on_finished)
        self._worker = worker

        self.status.setText("Computing... " + note)
        self.compute_button.setEnabled(False)
        worker.start()

    def shutdown(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.wait(3000)

    # --------------------------------------------------------------- private

    def _on_compute_clicked(self) -> None:
        self.requestSections.emit()

    def _on_completed(self, results: list) -> None:
        self._results = results
        self.module_label.setText(self.client.status_text())
        self._redraw()

    def _on_failed(self, message: str) -> None:
        self.status.setText("Error: " + message)
        self.module_label.setText(self.client.status_text())

    def _on_finished(self) -> None:
        self.compute_button.setEnabled(True)
        self._worker = None

    def _redraw(self) -> None:
        self.plot_widget.clear()
        if not self._results:
            self.stats.setText("")
            return

        use_db = self.db_box.isChecked()
        normalise = self.normalise_box.isChecked()

        all_descriptions = []
        for i, (label, freqs, amps) in enumerate(self._results):
            values = amps.copy()
            if normalise:
                peak = values.max()
                if peak > 0:
                    values = values / peak

            if use_db:
                ref = values.max() if values.max() > 0 else 1.0
                values = 20.0 * np.log10(np.maximum(values, 1e-12) / ref)

            pen = self._pens[i % len(self._pens)]
            self.plot_widget.plot(freqs, values, pen=pen, name=label)

            desc = self._describe(freqs, amps, label)
            all_descriptions.append(desc)

            # Peak marker.
            peak_idx = int(np.argmax(amps))
            peak_freq = float(freqs[peak_idx])
            peak_line = pg.InfiniteLine(
                pos=peak_freq, angle=90,
                pen=pg.mkPen(pen.color(), width=1, style=Qt.PenStyle.DashLine),
            )
            self.plot_widget.addItem(peak_line)

        self.plot_widget.setLabel("left", "Amplitude (dB)" if use_db else "Amplitude")
        self.stats.setText("\n".join(all_descriptions))
        self.status.setText(
            "%d curve(s), %d bins" % (len(self._results), len(self._results[0][2]))
        )

    @staticmethod
    def _describe(freqs: np.ndarray, amps: np.ndarray, label: str) -> str:
        if amps.size == 0 or amps.max() <= 0:
            return "%s: no signal" % label

        peak_idx = int(np.argmax(amps))
        peak_hz = float(freqs[peak_idx])

        # Spectral centroid.
        total = float(amps.sum())
        centroid = float((freqs * amps).sum() / total) if total > 0 else 0.0

        # -6 dB bandwidth.
        threshold = amps.max() * 0.5  # -6 dB ≈ half amplitude
        above = freqs[amps >= threshold]
        if above.size >= 2:
            bw = "%.0f–%.0f Hz" % (above[0], above[-1])
        else:
            bw = "n/a"

        return "%s:  peak %.1f Hz  centroid %.1f Hz  -6dB %s" % (label, peak_hz, centroid, bw)