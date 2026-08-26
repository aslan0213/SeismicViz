"""Side-by-side comparison of two sections with synchronised interaction.

Covers the assignment's slice-synchronisation requirement: the same section is
shown from two volumes (or from one volume and a filtered preview of it), zoom
and pan are shared, and the crosshair position in one panel is mirrored in the
other so the same feature can be inspected in both.
"""

from __future__ import annotations

import numpy as np
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import Qt

from ..core.filters import difference_stats
from .display import DisplaySettings
from .slice_view import AxisMap, SliceView


class CompareView(QWidget):
    """Two linked :class:`SliceView` panels plus their synchronisation logic."""

    sourceChanged = pyqtSignal(str)
    roiChanged = pyqtSignal()

    def __init__(
        self,
        left_settings: DisplaySettings,
        right_settings: DisplaySettings,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._syncing = False
        self._linked = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(self._build_toolbar())

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.left = SliceView(left_settings, "Reference")
        self.right = SliceView(right_settings, "Comparison")
        splitter.addWidget(self.left)
        splitter.addWidget(self.right)
        splitter.setSizes([600, 600])
        layout.addWidget(splitter, 1)

        self.stats_label = QLabel("")
        self.stats_label.setStyleSheet("font-family: Consolas, monospace; color:#9aa4b2;")
        layout.addWidget(self.stats_label)

        self._wire_synchronisation()
        self.set_linked(True)

    # ------------------------------------------------------------------ setup

    def _build_toolbar(self) -> QHBoxLayout:
        bar = QHBoxLayout()

        bar.addWidget(QLabel("Compare against"))
        self.source_combo = QComboBox()
        self.source_combo.setMinimumWidth(240)
        self.source_combo.currentTextChanged.connect(self.sourceChanged.emit)
        bar.addWidget(self.source_combo)

        self.link_box = QCheckBox("Link zoom and pan")
        self.link_box.setChecked(True)
        self.link_box.toggled.connect(self.set_linked)
        bar.addWidget(self.link_box)

        self.difference_box = QCheckBox("Show difference")
        self.difference_box.setToolTip(
            "Display comparison minus reference instead of the comparison itself"
        )
        self.difference_box.toggled.connect(lambda _: self.sourceChanged.emit(self.source))
        bar.addWidget(self.difference_box)

        self.roi_box = QCheckBox("ROI")
        self.roi_box.setToolTip("Region sent to the spectrum module, mirrored in both panels")
        self.roi_box.toggled.connect(self.set_roi_visible)
        bar.addWidget(self.roi_box)

        bar.addStretch(1)
        return bar

    def _wire_synchronisation(self) -> None:
        # Crosshair: hovering one panel moves the marker in the other.
        self.left.cursorMoved.connect(lambda x, y: self._mirror(self.right, x, y))
        self.right.cursorMoved.connect(lambda x, y: self._mirror(self.left, x, y))
        self.left.cursorClicked.connect(lambda x, y: self._pin(self.right, x, y))
        self.right.cursorClicked.connect(lambda x, y: self._pin(self.left, x, y))

        # ROI: keep both rectangles identical whichever one is dragged.
        self.left.roiChanged.connect(lambda: self._mirror_roi(self.left, self.right))
        self.right.roiChanged.connect(lambda: self._mirror_roi(self.right, self.left))

        # The per-view ROI checkboxes stay in step with the shared one.
        self.left.roi_box.setVisible(False)
        self.right.roi_box.setVisible(False)

    # ------------------------------------------------------------ interaction

    def _mirror(self, target: SliceView, x: float, y: float) -> None:
        if self._syncing:
            return
        self._syncing = True
        try:
            target.set_marker(x, y)
        finally:
            self._syncing = False

    def _pin(self, target: SliceView, x: float, y: float) -> None:
        self._mirror(target, x, y)
        self._update_stats_at(x, y)

    def _mirror_roi(self, source: SliceView, target: SliceView) -> None:
        if self._syncing:
            return
        self._syncing = True
        try:
            target.roi.setPos(source.roi.pos(), finish=False)
            target.roi.setSize(source.roi.size(), finish=False)
        finally:
            self._syncing = False
        self.roiChanged.emit()

    def set_linked(self, linked: bool) -> None:
        self._linked = linked
        if linked:
            self.right.link_to(self.left)
        else:
            self.right.unlink()

    def set_roi_visible(self, visible: bool) -> None:
        self._syncing = True
        try:
            self.left.set_roi_visible(visible)
            self.right.set_roi_visible(visible)
            if visible:
                self.right.roi.setPos(self.left.roi.pos(), finish=False)
                self.right.roi.setSize(self.left.roi.size(), finish=False)
        finally:
            self._syncing = False
        self.roiChanged.emit()

    @property
    def roi_visible(self) -> bool:
        return self.left.roi_visible

    # -------------------------------------------------------------------- data

    def set_sources(self, names: list[str], keep: str | None = None) -> None:
        current = keep or self.source
        self.source_combo.blockSignals(True)
        self.source_combo.clear()
        self.source_combo.addItems(names)
        if current in names:
            self.source_combo.setCurrentText(current)
        self.source_combo.blockSignals(False)

    @property
    def source(self) -> str:
        return self.source_combo.currentText()

    @property
    def show_difference(self) -> bool:
        return self.difference_box.isChecked()

    def set_sections(
        self,
        left: np.ndarray,
        right: np.ndarray | None,
        axes: AxisMap,
        left_title: str,
        right_title: str,
        keep_view: bool = True,
    ) -> None:
        self.left.set_section(left, axes, left_title, keep_view=keep_view)

        if right is None:
            self.right.clear()
            self.stats_label.setText("")
            return

        self.right.set_section(right, axes, right_title, keep_view=keep_view)
        self._update_difference_stats(left, right)

    def _update_difference_stats(self, left: np.ndarray, right: np.ndarray) -> None:
        if left.shape != right.shape:
            self.stats_label.setText("Sections have different shapes; statistics unavailable.")
            return

        stats = difference_stats(left, right)
        if not stats:
            self.stats_label.setText("")
            return

        self.stats_label.setText(
            "RMS reference %.4g    RMS difference %.4g (%.1f%%)    "
            "max |diff| %.4g    correlation %.4f"
            % (
                stats.get("rms_original", 0.0),
                stats.get("rms_difference", 0.0),
                stats.get("relative_rms_pct", 0.0),
                stats.get("max_abs_difference", 0.0),
                stats.get("correlation", float("nan")),
            )
        )

    def _update_stats_at(self, x: float, y: float) -> None:
        a = self.left.value_at(x, y)
        b = self.right.value_at(x, y)
        if a is None or b is None:
            return
        self.stats_label.setText(
            "At %s %.0f / %s %.0f:   reference %+.5g    comparison %+.5g    difference %+.5g"
            % (
                self.left.axes.x_label.split(" (")[0],
                x,
                self.left.axes.y_label.split(" (")[0],
                y,
                a,
                b,
                b - a,
            )
        )