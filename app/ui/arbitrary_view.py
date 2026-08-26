"""Arbitrary / composite line extraction (the assignment's optional item).

A map view shows one time slice of the cube. The user drags a polyline across
it; the traverse is resampled on the bin grid, bilinearly interpolated through
the volume, and drawn as a composite section next to the map.
"""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..core.volume import AXIS_TIME, SeismicVolume
from .display import DisplaySettings
from .slice_view import AxisMap, SliceView


class ArbitraryLineView(QWidget):
    """Map + composite section for a user-drawn traverse."""

    sectionChanged = pyqtSignal()

    def __init__(self, settings: DisplaySettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.volume: SeismicVolume | None = None
        self._section: np.ndarray | None = None
        self._path: np.ndarray | None = None
        self._updating = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(self._build_toolbar())

        splitter = QSplitter(Qt.Orientation.Horizontal)
        # The map is a plan view, so it keeps the normal y direction.
        self.map_view = SliceView(settings, "Map - time slice", invert_y=False)
        self.section_view = SliceView(settings, "Composite section")
        splitter.addWidget(self.map_view)
        splitter.addWidget(self.section_view)
        splitter.setSizes([520, 780])
        layout.addWidget(splitter, 1)

        self.info = QLabel("Load a volume to draw an arbitrary line.")
        self.info.setStyleSheet("color:#9aa4b2;")
        layout.addWidget(self.info)

        self._make_line_roi()

    # ------------------------------------------------------------------ setup

    def _build_toolbar(self) -> QHBoxLayout:
        bar = QHBoxLayout()

        bar.addWidget(QLabel("Map time slice"))
        self.time_slider = QSlider(Qt.Orientation.Horizontal)
        self.time_slider.setMaximumWidth(260)
        self.time_slider.valueChanged.connect(self._time_changed)
        bar.addWidget(self.time_slider)

        self.time_label = QLabel("-")
        self.time_label.setMinimumWidth(80)
        bar.addWidget(self.time_label)

        reset = QPushButton("Reset line")
        reset.clicked.connect(self.reset_line)
        bar.addWidget(reset)

        self.live_box = QCheckBox("Live update")
        self.live_box.setChecked(True)
        self.live_box.setToolTip(
            "Recompute while dragging. Turn off on very large cubes; the "
            "section then updates when the mouse is released."
        )
        bar.addWidget(self.live_box)

        extract = QPushButton("Extract now")
        extract.clicked.connect(self.update_section)
        bar.addWidget(extract)

        bar.addStretch(1)
        self.hint = QLabel(
            "Drag the yellow handles to move the traverse; click a segment to add a bend."
        )
        self.hint.setStyleSheet("color:#9aa4b2;")
        bar.addWidget(self.hint)
        return bar

    def _make_line_roi(self) -> None:
        self.line_roi = pg.PolyLineROI(
            [[0, 0], [1, 1]],
            closed=False,
            pen=pg.mkPen("#ffab00", width=2),
            handlePen=pg.mkPen("#ffd54f", width=2),
        )
        self.line_roi.setZValue(30)
        self.map_view.plot.addItem(self.line_roi)
        self.line_roi.sigRegionChanged.connect(self._roi_moved)
        self.line_roi.sigRegionChangeFinished.connect(lambda *_: self.update_section())

    # ----------------------------------------------------------------- volume

    def set_volume(self, volume: SeismicVolume | None) -> None:
        self.volume = volume
        if volume is None:
            self.map_view.clear()
            self.section_view.clear()
            self._section = None
            self.info.setText("Load a volume to draw an arbitrary line.")
            return

        self._updating = True
        try:
            self.time_slider.setRange(0, volume.n_time - 1)
            self.time_slider.setValue(volume.n_time // 3)
        finally:
            self._updating = False

        self._draw_map()
        self.reset_line()

    def _time_changed(self, _value: int) -> None:
        if self._updating or self.volume is None:
            return
        self._draw_map()

    def _draw_map(self) -> None:
        if self.volume is None:
            return
        index = self.time_slider.value()
        section = self.volume.slice(AXIS_TIME, index)
        axes = AxisMap(
            x0=self.volume.geometry.iline_label(0),
            dx=self.volume.geometry.il_step,
            y0=self.volume.geometry.xline_label(0),
            dy=self.volume.geometry.xl_step,
            x_label="Inline",
            y_label="Crossline",
        )
        self.map_view.set_section(
            section, axes, "Map - %s" % self.volume.geometry.axis_label(AXIS_TIME, index)
        )
        self.time_label.setText(self.volume.geometry.axis_label(AXIS_TIME, index))

    # ------------------------------------------------------------------- line

    def reset_line(self) -> None:
        """Lay the traverse diagonally across the survey."""
        if self.volume is None:
            return
        geometry = self.volume.geometry
        n_il, n_xl = self.volume.n_iline, self.volume.n_xline

        points = [
            [geometry.iline_label(int(n_il * 0.10)), geometry.xline_label(int(n_xl * 0.15))],
            [geometry.iline_label(int(n_il * 0.45)), geometry.xline_label(int(n_xl * 0.60))],
            [geometry.iline_label(int(n_il * 0.88)), geometry.xline_label(int(n_xl * 0.35))],
        ]
        self.line_roi.blockSignals(True)
        try:
            self.line_roi.setPoints(points, closed=False)
        finally:
            self.line_roi.blockSignals(False)
        self.update_section()

    def waypoints(self) -> list[tuple[float, float]]:
        """Handle positions converted to array indices."""
        state = self.line_roi.getState()
        origin = self.line_roi.pos()
        axes = self.map_view.axes

        points = []
        for point in state["points"]:
            x = origin.x() + point.x()
            y = origin.y() + point.y()
            points.append((axes.to_ix(x), axes.to_iy(y)))
        return points

    def _roi_moved(self, *_args) -> None:
        if self.live_box.isChecked():
            self.update_section()

    def update_section(self) -> None:
        if self.volume is None:
            return

        points = self.waypoints()
        if len(points) < 2:
            return

        # Clamp to the survey so a handle dragged outside cannot break extraction.
        clamped = [
            (
                float(np.clip(il, 0, self.volume.n_iline - 1)),
                float(np.clip(xl, 0, self.volume.n_xline - 1)),
            )
            for il, xl in points
        ]

        try:
            section, path = self.volume.arbitrary_slice(clamped)
        except ValueError as exc:
            self.info.setText("Cannot extract: %s" % exc)
            return

        geometry = self.volume.geometry
        axes = AxisMap(
            x0=0.0,
            dx=1.0,
            y0=geometry.time_label(0),
            dy=geometry.dt * 1000.0,
            x_label="Distance along line (bins)",
            y_label="Time (ms)",
        )
        self.section_view.set_section(
            section, axes, "Composite section - %d traces" % section.shape[0]
        )

        self._section = section
        self._path = path
        length = float(np.hypot(*np.diff(path, axis=0).T).sum()) if len(path) > 1 else 0.0
        self.info.setText(
            "%d waypoints, %d traces, traverse length %.1f bins"
            % (len(points), section.shape[0], length)
        )
        self.sectionChanged.emit()

    # ------------------------------------------------------------------ access

    @property
    def section(self) -> np.ndarray | None:
        return self._section

    def roi_section(self) -> np.ndarray | None:
        return self.section_view.roi_section()

    def roi_description(self) -> str:
        return self.section_view.roi_description()