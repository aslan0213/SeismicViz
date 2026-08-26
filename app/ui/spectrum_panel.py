"""Spectrum panel - the Qt front end for the independent C# module.

The panel never computes a spectrum itself. It packages the current ROI (or
whole section) and hands it to :class:`SpectrumClient`, which forwards it to
``SpectrumService.exe`` over a loopback socket. Work runs on a background
thread so a large ROI cannot freeze the interface.
"""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.spectrum_client import WINDOW_NAMES, SpectrumClient, SpectrumError

CURVE_COLORS = ["#4f9dff", "#ff8a3d", "#40c463", "#e05c8a", "#c084fc"]


class SpectrumWorker(QThread):
    """Runs one batch of spectrum requests off the GUI thread."""

    completed = pyqtSignal(list)     # [(label, freqs, amps), ...]
    failed = pyqtSignal(str)

    def __init__(
        self,
        client: SpectrumClient,
        jobs: list[tuple[str, np.ndarray]],
        dt: float,
        window: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._client = client
        self._jobs = jobs
        self._dt = dt
        self._window = window

    def run(self) -> None:  # noqa: D102 - QThread entry point
        results = []
        try:
            for label, section in self._jobs:
                freqs, amps = self._client.average_spectrum(section, self._dt, self._window)
                results.append((label, freqs, amps))
        except SpectrumError as exc:
            self.failed.emit(str(exc))
            return
        except Exception as exc:                       # unexpected, still surface it
            self.failed.emit("%s: %s" % (type(exc).__name__, exc))
            return
        self.completed.emit(results)


class SpectrumPanel(QWidget):
    """Amplitude-spectrum plot plus the controls that drive the C# module."""

    #: Emitted when the panel wants fresh sections; the host replies via
    #: :meth:`compute`.
    requestSections = pyqtSignal()

    def __init__(self, client: SpectrumClient, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.client = client
        self._worker: SpectrumWorker | None = None
        self._results: list[tuple[str, np.ndarray, np.ndarray]] = []

        self._build_ui()

    # ------------------------------------------------------------------ setup

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        layout.addWidget(self._build_controls())

        self.plot = pg.PlotWidget()
        self.plot.setBackground("#101216")
        self.plot.setMinimumHeight(180)          # keep the axis labels readable
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.plot.setLabel("bottom", "Frequency (Hz)")
        self.plot.setLabel("left", "Average amplitude")
        self.legend = self.plot.addLegend(offset=(-10, 10))
        layout.addWidget(self.plot, 1)

        self.peak_line = pg.InfiniteLine(
            angle=90, movable=False, pen=pg.mkPen("#ffab00", style=Qt.PenStyle.DashLine)
        )
        self.peak_line.setVisible(False)
        self.plot.addItem(self.peak_line)

        self.status = QLabel("Idle.")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color:#9aa4b2;")
        layout.addWidget(self.status)

        self.stats = QLabel("")
        self.stats.setWordWrap(True)
        self.stats.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.stats)

    def _build_controls(self) -> QWidget:
        group = QGroupBox("Spectrum analysis  (computed by the external C# module)")
        grid = QGridLayout(group)

        grid.addWidget(QLabel("Taper"), 0, 0)
        self.window_combo = QComboBox()
        self.window_combo.addItems(list(WINDOW_NAMES.keys()))
        self.window_combo.setCurrentText("Hann")
        self.window_combo.setToolTip("Window applied to every trace before the FFT")
        self.window_combo.currentTextChanged.connect(lambda _: self._maybe_auto())
        grid.addWidget(self.window_combo, 0, 1)

        self.log_box = QCheckBox("dB scale")
        self.log_box.toggled.connect(self._redraw)
        grid.addWidget(self.log_box, 0, 2)

        self.normalise_box = QCheckBox("Normalise")
        self.normalise_box.setToolTip("Scale every curve so its peak is 1")
        self.normalise_box.toggled.connect(self._redraw)
        grid.addWidget(self.normalise_box, 0, 3)

        self.auto_box = QCheckBox("Auto update on ROI change")
        self.auto_box.setChecked(True)
        grid.addWidget(self.auto_box, 1, 0, 1, 2)

        self.compute_button = QPushButton("Compute spectrum")
        self.compute_button.clicked.connect(lambda: self.requestSections.emit())
        grid.addWidget(self.compute_button, 1, 2, 1, 2)

        self.module_label = QLabel(self.client.status_text())
        self.module_label.setStyleSheet("color:#9aa4b2;")
        grid.addWidget(self.module_label, 2, 0, 1, 4)

        return group

    # ---------------------------------------------------------------- compute

    @property
    def auto_update(self) -> bool:
        return self.auto_box.isChecked()

    def _maybe_auto(self) -> None:
        if self.auto_update:
            self.requestSections.emit()

    def request_update(self) -> None:
        """Called by the host when the ROI or the displayed slice changed."""
        if self.auto_update:
            self.requestSections.emit()

    def compute(self, jobs: list[tuple[str, np.ndarray]], dt: float, note: str = "") -> None:
        """Send one or more sections to the C# module.

        ``jobs`` pairs a legend label with a ``(n_traces, n_samples)`` array.
        """
        if self._worker is not None and self._worker.isRunning():
            return                        # a request is already in flight
        if not jobs:
            self.status.setText("Nothing to analyse - load a volume first.")
            return

        window = WINDOW_NAMES[self.window_combo.currentText()]
        sizes = ", ".join("%s %dx%d" % (label, a.shape[0], a.shape[1]) for label, a in jobs)
        self.status.setText("Sending %s to the C# module%s..." % (sizes, note and " (%s)" % note))
        self.compute_button.setEnabled(False)

        worker = SpectrumWorker(self.client, jobs, dt, window, self)
        worker.completed.connect(self._on_completed)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(self._on_finished)
        self._worker = worker
        worker.start()

    def _on_completed(self, results: list) -> None:
        self._results = [(label, np.asarray(f), np.asarray(a)) for label, f, a in results]
        self.status.setText(
            "%s   %d curve(s), %d bins."
            % (self.client.status_text(), len(self._results), len(self._results[0][1]))
        )
        self.module_label.setText(self.client.status_text())
        self._redraw()

    def _on_failed(self, message: str) -> None:
        self.status.setText("Spectrum module error: %s" % message)
        self.module_label.setText(self.client.status_text())

    def _on_finished(self) -> None:
        self.compute_button.setEnabled(True)
        self._worker = None

    # ----------------------------------------------------------------- drawing

    def _redraw(self) -> None:
        # clear() drops the curves but leaves stale legend rows behind.
        self.plot.clear()
        self.legend.clear()
        self.plot.addItem(self.peak_line)
        self.peak_line.setVisible(False)

        if not self._results:
            self.stats.setText("")
            return

        log = self.log_box.isChecked()
        normalise = self.normalise_box.isChecked()
        self.plot.setLabel("left", "Average amplitude (dB)" if log else "Average amplitude")

        lines = []
        for i, (label, freqs, amps) in enumerate(self._results):
            values = amps.astype(np.float64)
            peak = float(values.max()) if values.size else 0.0
            if normalise and peak > 0:
                values = values / peak
            if log:
                reference = float(values.max()) or 1.0
                values = 20.0 * np.log10(np.maximum(values, reference * 1e-6) / reference)

            colour = CURVE_COLORS[i % len(CURVE_COLORS)]
            self.plot.plot(freqs, values, pen=pg.mkPen(colour, width=2), name=label)
            lines.append(self._describe(label, freqs, amps))

        # Mark the dominant frequency of the first curve.
        first_f, first_a = self._results[0][1], self._results[0][2]
        if first_a.size:
            self.peak_line.setPos(float(first_f[int(np.argmax(first_a))]))
            self.peak_line.setVisible(True)

        self.plot.enableAutoRange()
        self.stats.setText("\n".join(lines))

    @staticmethod
    def _describe(label: str, freqs: np.ndarray, amps: np.ndarray) -> str:
        """Dominant frequency, spectral centroid and -6 dB bandwidth."""
        if amps.size == 0:
            return "%s: empty" % label

        values = amps.astype(np.float64)
        peak_index = int(np.argmax(values))
        peak_freq = float(freqs[peak_index])
        total = values.sum()
        centroid = float((freqs * values).sum() / total) if total > 0 else 0.0

        half = values[peak_index] * 0.5          # -6 dB
        above = np.flatnonzero(values >= half)
        if above.size:
            low, high = float(freqs[above[0]]), float(freqs[above[-1]])
        else:
            low = high = peak_freq

        return "%s:  peak %.1f Hz   centroid %.1f Hz   -6 dB band %.1f-%.1f Hz" % (
            label,
            peak_freq,
            centroid,
            low,
            high,
        )

    # ---------------------------------------------------------------- teardown

    def shutdown(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.wait(3000)
