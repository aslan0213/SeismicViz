"""Arbitrary-line section: draw a polyline on a map, see the composite section."""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.volume import AXIS_TIME, SeismicVolume
from .display import DisplaySettings
from .slice_view import AxisMap, SliceView


class ArbitraryLineView(QWidget):
    """Map view with a draggable polyline and the resulting composite section."""

    def __init__(self, settings: DisplaySettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.volume: SeismicVolume | None = None
        self.section: np.ndarray | None = None
        self._path: np.ndarray | None = None

        self._build_ui()

    # ------------------------------------------------------------------ setup

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)

        # -- toolbar ----------------------------------------------------------
        toolbar = QHBoxLayout()

        self.update_btn = QPushButton("Extract section")
        self.update_btn.clicked.connect(self.update_section)
        toolbar.addWidget(self.update_btn)

        self.live_box = QCheckBox("Live update")
        self.live_box.setToolTip("Recompute the section whenever the line is dragged")
        self.live_box.setChecked(False)
        toolbar.addWidget(self.live_box)

        self.status = QLabel("")
        self.status.setStyleSheet("color: #999;")
        toolbar.addWidget(self.status, 1)
        layout.addLayout(toolbar)

        # -- two panels -------------------------------------------------------
        panels = QHBoxLayout()

        # Left: map (time slice) — y axis NOT inverted for a plan view.
        self.map_view = SliceView(self.settings, "Map view", show_toolbar=False, invert_y=False)
        panels.addWidget(self.map_view, 1)

        # Right: composite section along the polyline.
        self.section_view = SliceView(self.settings, "Composite section", show_toolbar=True)
        panels.addWidget(self.section_view, 1)
        layout.addLayout(panels, 1)

        # -- polyline ROI on the map -----------------------------------------
        handles = [(0.3, 0.3), (0.5, 0.7), (0.7, 0.4)]
        self.polyline = pg.PolyLineROI(
            handles,
            pen=pg.mkPen("#ffab00", width=2),
            handlePen=pg.mkPen("#ffdd57", width=2),
            closed=False,
        )
        self.polyline.setZValue(20)
        self.map_view.plot.addItem(self.polyline)
        self.polyline.sigRegionChanged.connect(self._line_moved)

    # ---------------------------------------------------------------- volume

    def set_volume(self, volume: SeismicVolume | None) -> None:
        self.volume = volume
        self.section = None
        self._path = None
        if volume is None:
            self.map_view.clear()
            self.section_view.clear()
            return

        # Show a time slice in the middle as the map background.
        mid = volume.n_time // 2
        time_section = volume.slice(AXIS_TIME, mid)
        geo = volume.geometry
        axes = AxisMap(
            x0=geo.iline_label(0),
            dx=geo.il_step,
            y0=geo.xline_label(0),
            dy=geo.xl_step,
            x_label="Inline",
            y_label="Crossline",
        )
        self.map_view.set_section(
            time_section, axes,
            "Time slice  %s" % geo.axis_label(AXIS_TIME, mid),
        )

        # Reset the polyline to span the survey.
        n_il, n_xl = volume.n_iline, volume.n_xline
        handles = [
            (axes.to_x(n_il * 0.25), axes.to_y(n_xl * 0.25)),
            (axes.to_x(n_il * 0.50), axes.to_y(n_xl * 0.75)),
            (axes.to_x(n_il * 0.75), axes.to_y(n_xl * 0.50)),
        ]
        self.polyline.blockSignals(True)
        # Remove old handles and set new ones.
        while len(self.polyline.getHandles()) > len(handles):
            self.polyline.removeHandle(0)
        for i, (x, y) in enumerate(handles):
            if i < len(self.polyline.getHandles()):
                self.polyline.getHandles()[i].setPos(x, y)
            else:
                self.polyline.addFreeHandle((x, y))
        self.polyline.blockSignals(False)

        self.section_view.clear()
        self.status.setText("Click 'Extract section' to compute the composite line.")

    # ------------------------------------------------------------ waypoints

    def waypoints(self) -> list[tuple[float, float]]:
        """Polyline handle positions in array-index coordinates."""
        pts: list[tuple[float, float]] = []
        axes = self.map_view.axes
        for handle in self.polyline.getHandles():
            pos = self.polyline.mapToParent(handle.pos())
            il = axes.to_ix(pos.x())
            xl = axes.to_iy(pos.y())
            pts.append((il, xl))
        return pts

    # -------------------------------------------------------- extract section

    def update_section(self) -> None:
        if self.volume is None:
            self.status.setText("No volume loaded.")
            return

        wp = self.waypoints()
        if len(wp) < 2:
            self.status.setText("Need at least two waypoints.")
            return

        vol = self.volume
        # Clamp waypoints inside the survey.
        clamped = [
            (float(np.clip(il, 0, vol.n_iline - 1)),
             float(np.clip(xl, 0, vol.n_xline - 1)))
            for il, xl in wp
        ]

        try:
            section, path = vol.arbitrary_slice(clamped)
        except ValueError as exc:
            self.status.setText(str(exc))
            return

        self.section = section
        self._path = path

        geo = vol.geometry
        axes = AxisMap(
            x0=0,
            dx=1,
            y0=geo.time_label(0),
            dy=geo.dt * 1000.0,
            x_label="Trace along line",
            y_label="Time (ms)",
        )
        self.section_view.set_section(
            section, axes,
            "Arbitrary line  (%d traces)" % section.shape[0],
        )
        self.status.setText(
            "%d traces x %d samples from %d waypoints"
            % (section.shape[0], section.shape[1], len(clamped))
        )

    def _line_moved(self) -> None:
        if self.live_box.isChecked():
            self.update_section()

    # ----------------------------------------------------------- ROI access

    def roi_section(self) -> np.ndarray | None:
        """The composite section (or its ROI sub-region) for the spectrum."""
        return self.section_view.roi_section()

    def roi_description(self) -> str:
        return self.section_view.roi_description()