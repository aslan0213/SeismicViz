"""Interactive 2D seismic section viewer.

Wraps pyqtgraph so the rest of the application deals in seismic terms - a
section is ``(n_traces, n_samples)``, time runs downwards, and positions are
reported in survey units rather than pixels.

Provides the interaction the assignment asks for: wheel zoom, drag to pan, a
draggable colour bar for the amplitude window, a crosshair whose position can
be mirrored into another view, and a rectangular ROI used to pick the region
sent to the spectrum module.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .display import DisplaySettings

pg.setConfigOptions(antialias=False, imageAxisOrder="col-major")


@dataclass
class AxisMap:
    """Affine mapping from array indices to the units drawn on the axes."""

    x0: float = 0.0
    dx: float = 1.0
    y0: float = 0.0
    dy: float = 1.0
    x_label: str = "Trace"
    y_label: str = "Time (ms)"

    def to_x(self, index: float) -> float:
        return self.x0 + index * self.dx

    def to_y(self, index: float) -> float:
        return self.y0 + index * self.dy

    def to_ix(self, value: float) -> float:
        return (value - self.x0) / self.dx if self.dx else 0.0

    def to_iy(self, value: float) -> float:
        return (value - self.y0) / self.dy if self.dy else 0.0


class SliceView(QWidget):
    """One seismic section with a colour bar, crosshair and optional ROI."""

    #: Cursor moved over the image, in axis units.
    cursorMoved = pyqtSignal(float, float)
    #: Left click on the image, in axis units.
    cursorClicked = pyqtSignal(float, float)
    #: The ROI rectangle was resized or dragged.
    roiChanged = pyqtSignal()

    def __init__(
        self,
        settings: DisplaySettings,
        title: str = "",
        show_toolbar: bool = True,
        invert_y: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.settings = settings
        self._invert_y = invert_y
        self.axes = AxisMap()
        self._section: np.ndarray | None = None
        self._updating_levels = False

        self._build_ui(title, show_toolbar)
        self.settings.changed.connect(self._apply_settings)

    # ------------------------------------------------------------------ setup

    def _build_ui(self, title: str, show_toolbar: bool) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        self.title_label = QLabel(title)
        font = QFont()
        font.setBold(True)
        self.title_label.setFont(font)
        layout.addWidget(self.title_label)

        self.graphics = pg.GraphicsLayoutWidget()
        layout.addWidget(self.graphics, 1)

        self.plot: pg.PlotItem = self.graphics.addPlot(row=0, col=0)
        self.plot.setDefaultPadding(0.0)
        # Seismic convention: time downwards. Map views keep the normal sense.
        self.plot.invertY(self._invert_y)
        self.plot.showGrid(x=False, y=False)
        self.plot.setLabel("bottom", self.axes.x_label)
        self.plot.setLabel("left", self.axes.y_label)
        self.plot.getViewBox().setMouseMode(pg.ViewBox.PanMode)

        self.image = pg.ImageItem()
        self.image.setOpts(axisOrder="col-major")   # image[trace, sample]
        # Average rather than drop samples when a section is larger than the
        # widget; without this a zoomed-out display aliases badly.
        self.image.setAutoDownsample(True)
        self.plot.addItem(self.image)

        self.colorbar = pg.ColorBarItem(
            values=self.settings.levels,
            colorMap=self.settings.pg_colormap(),
            label="Amplitude",
            interactive=True,
            width=16,
        )
        self.colorbar.setImageItem(self.image, insert_in=self.plot)
        self.colorbar.sigLevelsChanged.connect(self._colorbar_dragged)

        self._make_crosshair()
        self._make_roi()

        if show_toolbar:
            layout.addLayout(self._make_toolbar())

        self.readout = QLabel("")
        self.readout.setStyleSheet("color: #888;")
        layout.addWidget(self.readout)

        self.graphics.scene().sigMouseMoved.connect(self._mouse_moved)
        self.graphics.scene().sigMouseClicked.connect(self._mouse_clicked)

    def _make_crosshair(self) -> None:
        pen = pg.mkPen("#00c853", width=1, style=Qt.PenStyle.DashLine)
        self.vline = pg.InfiniteLine(angle=90, movable=False, pen=pen)
        self.hline = pg.InfiniteLine(angle=0, movable=False, pen=pen)
        for line in (self.vline, self.hline):
            line.setZValue(20)
            line.setVisible(False)
            self.plot.addItem(line, ignoreBounds=True)

        self.marker = pg.ScatterPlotItem(
            size=11, pen=pg.mkPen("#00c853", width=2), brush=pg.mkBrush(0, 0, 0, 0)
        )
        self.marker.setZValue(21)
        self.marker.setVisible(False)
        self.plot.addItem(self.marker, ignoreBounds=True)

    def _make_roi(self) -> None:
        self.roi = pg.RectROI(
            [0, 0], [10, 10], pen=pg.mkPen("#ffab00", width=2), invertible=True
        )
        self.roi.addScaleHandle([0, 0], [1, 1])
        self.roi.addScaleHandle([1, 1], [0, 0])
        self.roi.setZValue(15)
        self.roi.setVisible(False)
        self.plot.addItem(self.roi)
        self.roi.sigRegionChanged.connect(lambda *_: self.roiChanged.emit())

    def _make_toolbar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)

        fit = QPushButton("Fit")
        fit.setToolTip("Reset zoom to the whole section")
        fit.clicked.connect(self.fit_view)
        bar.addWidget(fit)

        self.crosshair_box = QCheckBox("Crosshair")
        self.crosshair_box.setChecked(True)
        self.crosshair_box.toggled.connect(self._toggle_crosshair)
        bar.addWidget(self.crosshair_box)

        self.roi_box = QCheckBox("ROI")
        self.roi_box.setToolTip("Rectangle used for the spectrum analysis")
        self.roi_box.toggled.connect(self.set_roi_visible)
        bar.addWidget(self.roi_box)

        bar.addStretch(1)
        return bar

    # ------------------------------------------------------------------- data

    def set_section(
        self,
        section: np.ndarray,
        axes: AxisMap | None = None,
        title: str | None = None,
        autoscale: bool = True,
        keep_view: bool = True,
    ) -> None:
        """Display a ``(n_traces, n_samples)`` section."""
        self._section = np.asarray(section, dtype=np.float32)

        if axes is not None:
            self.axes = axes
            self.plot.setLabel("bottom", axes.x_label)
            self.plot.setLabel("left", axes.y_label)
        if title is not None:
            self.title_label.setText(title)

        if autoscale:
            self.settings.autoscale_to(self._section)

        had_data = self.image.image is not None
        self.image.setImage(self._section, autoLevels=False, levels=self.settings.levels)
        self._place_image()
        self._apply_settings()

        if not had_data or not keep_view:
            self.fit_view()
            self._reset_roi()

    def _place_image(self) -> None:
        """Stretch the pixmap so the axes read in survey units."""
        if self._section is None:
            return
        nx, ny = self._section.shape
        rect = QRectF(
            self.axes.x0 - 0.5 * self.axes.dx,
            self.axes.y0 - 0.5 * self.axes.dy,
            nx * self.axes.dx,
            ny * self.axes.dy,
        )
        self.image.setRect(rect)

    @property
    def section(self) -> np.ndarray | None:
        return self._section

    def clear(self) -> None:
        self._section = None
        self.image.clear()
        self.title_label.setText("")
        self.readout.setText("")

    # ------------------------------------------------------------- appearance

    def _apply_settings(self) -> None:
        lut = self.settings.lut()
        self.image.setLookupTable(lut)
        levels = self.settings.levels
        self.image.setLevels(levels)

        self._updating_levels = True
        try:
            self.colorbar.setColorMap(self.settings.pg_colormap())
            self.colorbar.setLevels(low=levels[0], high=levels[1])
        finally:
            self._updating_levels = False

    def _colorbar_dragged(self, bar: pg.ColorBarItem) -> None:
        """The user dragged a colour-bar handle: push the new window back."""
        if self._updating_levels:
            return
        low, high = bar.levels()
        self.settings.set_levels(float(low), float(high), from_user=True)

    def fit_view(self) -> None:
        if self._section is None:
            return
        nx, ny = self._section.shape
        self.plot.setXRange(self.axes.to_x(-0.5), self.axes.to_x(nx - 0.5), padding=0.0)
        self.plot.setYRange(self.axes.to_y(-0.5), self.axes.to_y(ny - 0.5), padding=0.0)

    # -------------------------------------------------------------- crosshair

    def _toggle_crosshair(self, on: bool) -> None:
        if not on:
            self.vline.setVisible(False)
            self.hline.setVisible(False)
            self.marker.setVisible(False)

    def _mouse_moved(self, scene_pos: QPointF) -> None:
        if self._section is None:
            return
        vb = self.plot.getViewBox()
        if not self.plot.sceneBoundingRect().contains(scene_pos):
            return

        point = vb.mapSceneToView(scene_pos)
        self._update_readout(point.x(), point.y())
        self.cursorMoved.emit(point.x(), point.y())

    def _mouse_clicked(self, event) -> None:
        if self._section is None or event.button() != Qt.MouseButton.LeftButton:
            return
        vb = self.plot.getViewBox()
        scene_pos = event.scenePos()
        if not self.plot.sceneBoundingRect().contains(scene_pos):
            return
        point = vb.mapSceneToView(scene_pos)
        self.set_marker(point.x(), point.y())
        self.cursorClicked.emit(point.x(), point.y())

    def set_marker(self, x: float, y: float, show_lines: bool = True) -> None:
        """Place the crosshair at an axis-unit position (used for view sync)."""
        if not getattr(self, "crosshair_box", None) or self.crosshair_box.isChecked():
            self.vline.setPos(x)
            self.hline.setPos(y)
            self.vline.setVisible(show_lines)
            self.hline.setVisible(show_lines)
            self.marker.setData([x], [y])
            self.marker.setVisible(True)

    def clear_marker(self) -> None:
        self.vline.setVisible(False)
        self.hline.setVisible(False)
        self.marker.setVisible(False)

    def _update_readout(self, x: float, y: float) -> None:
        if self._section is None:
            return
        ix = int(round(self.axes.to_ix(x)))
        iy = int(round(self.axes.to_iy(y)))
        nx, ny = self._section.shape
        if 0 <= ix < nx and 0 <= iy < ny:
            self.readout.setText(
                "%s %.0f   %s %.0f   amplitude %+.4g"
                % (
                    self.axes.x_label.split(" (")[0],
                    x,
                    self.axes.y_label.split(" (")[0],
                    y,
                    self._section[ix, iy],
                )
            )
        else:
            self.readout.setText("")

    def value_at(self, x: float, y: float) -> float | None:
        if self._section is None:
            return None
        ix = int(round(self.axes.to_ix(x)))
        iy = int(round(self.axes.to_iy(y)))
        nx, ny = self._section.shape
        if 0 <= ix < nx and 0 <= iy < ny:
            return float(self._section[ix, iy])
        return None

    # -------------------------------------------------------------------- ROI

    def set_roi_visible(self, visible: bool) -> None:
        if visible and self._section is not None and not self.roi.isVisible():
            self._reset_roi()
        self.roi.setVisible(visible)
        if getattr(self, "roi_box", None) and self.roi_box.isChecked() != visible:
            self.roi_box.blockSignals(True)
            self.roi_box.setChecked(visible)
            self.roi_box.blockSignals(False)
        self.roiChanged.emit()

    @property
    def roi_visible(self) -> bool:
        return self.roi.isVisible()

    def _reset_roi(self) -> None:
        """Put the ROI over the middle half of the section."""
        if self._section is None:
            return
        nx, ny = self._section.shape
        x0 = self.axes.to_x(nx * 0.25)
        y0 = self.axes.to_y(ny * 0.25)
        self.roi.setPos((x0, y0), finish=False)
        self.roi.setSize((nx * 0.5 * self.axes.dx, ny * 0.5 * self.axes.dy), finish=False)

    def roi_index_bounds(self) -> tuple[int, int, int, int] | None:
        """ROI as ``(trace_start, trace_stop, sample_start, sample_stop)``.

        Returns ``None`` when there is no section or the ROI is hidden, in
        which case callers should fall back to the whole section.
        """
        if self._section is None or not self.roi.isVisible():
            return None

        pos = self.roi.pos()
        size = self.roi.size()
        x_lo, x_hi = sorted((pos.x(), pos.x() + size.x()))
        y_lo, y_hi = sorted((pos.y(), pos.y() + size.y()))

        nx, ny = self._section.shape
        i0 = int(np.clip(np.floor(self.axes.to_ix(x_lo) + 0.5), 0, nx - 1))
        i1 = int(np.clip(np.ceil(self.axes.to_ix(x_hi) + 0.5), 1, nx))
        j0 = int(np.clip(np.floor(self.axes.to_iy(y_lo) + 0.5), 0, ny - 1))
        j1 = int(np.clip(np.ceil(self.axes.to_iy(y_hi) + 0.5), 1, ny))

        if i1 <= i0:
            i1 = min(nx, i0 + 1)
        if j1 <= j0:
            j1 = min(ny, j0 + 1)
        return i0, i1, j0, j1

    def roi_section(self) -> np.ndarray | None:
        """The sub-section inside the ROI, or the whole section if it is off."""
        if self._section is None:
            return None
        bounds = self.roi_index_bounds()
        if bounds is None:
            return self._section
        i0, i1, j0, j1 = bounds
        return self._section[i0:i1, j0:j1]

    def roi_description(self) -> str:
        bounds = self.roi_index_bounds()
        if bounds is None:
            if self._section is None:
                return "no data"
            return "full section (%d x %d)" % self._section.shape
        i0, i1, j0, j1 = bounds
        return "ROI traces %d-%d, samples %d-%d (%d x %d)" % (
            i0,
            i1 - 1,
            j0,
            j1 - 1,
            i1 - i0,
            j1 - j0,
        )

    # ----------------------------------------------------------------- linking

    def link_to(self, other: "SliceView") -> None:
        """Share zoom and pan with another view."""
        self.plot.setXLink(other.plot)
        self.plot.setYLink(other.plot)

    def unlink(self) -> None:
        self.plot.setXLink(None)
        self.plot.setYLink(None)
