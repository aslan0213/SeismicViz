"""Side-by-side comparison of two seismic sections with synchronised views."""

from __future__ import annotations

import numpy as np
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from ..core.filters import difference_stats
from .display import DisplaySettings
from .slice_view import AxisMap, SliceView


class CompareView(QWidget):
    """Two linked SliceViews with a source selector and difference stats."""

    sourceChanged = pyqtSignal(str)
    roiChanged = pyqtSignal()

    def __init__(
        self,
        settings_left: DisplaySettings,
        settings_right: DisplaySettings,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._syncing = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)

        # -- toolbar ----------------------------------------------------------
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Compare with:"))
        self.source_combo = QComboBox()
        self.source_combo.currentTextChanged.connect(self._source_changed)
        toolbar.addWidget(self.source_combo, 1)

        self.difference_box = QCheckBox("Show difference")
        self.difference_box.toggled.connect(self._difference_toggled)
        toolbar.addWidget(self.difference_box)

        self.roi_box = QCheckBox("ROI")
        self.roi_box.toggled.connect(self._toggle_roi)
        toolbar.addWidget(self.roi_box)

        layout.addLayout(toolbar)

        # -- two panels -------------------------------------------------------
        panels = QHBoxLayout()
        self.left = SliceView(settings_left, "Reference", show_toolbar=False)
        self.right = SliceView(settings_right, "Comparison", show_toolbar=False)
        panels.addWidget(self.left, 1)
        panels.addWidget(self.right, 1)
        layout.addLayout(panels, 1)

        # Link zoom and pan.
        self.right.link_to(self.left)

        # -- stats bar --------------------------------------------------------
        self.stats = QLabel("")
        self.stats.setStyleSheet("color: #999; font-size: 11px;")
        self.stats.setWordWrap(True)
        layout.addWidget(self.stats)

        # -- crosshair mirroring ---------------------------------------------
        self.left.cursorMoved.connect(lambda x, y: self._mirror(self.right, x, y))
        self.right.cursorMoved.connect(lambda x, y: self._mirror(self.left, x, y))
        self.left.cursorClicked.connect(lambda x, y: self._mirror_click(self.right, x, y))
        self.right.cursorClicked.connect(lambda x, y: self._mirror_click(self.left, x, y))

        # -- ROI sync ---------------------------------------------------------
        self.left.roiChanged.connect(lambda: self._sync_roi(self.left, self.right))
        self.right.roiChanged.connect(lambda: self._sync_roi(self.right, self.left))
        self.left.roiChanged.connect(self.roiChanged.emit)
        self.right.roiChanged.connect(self.roiChanged.emit)

    # --------------------------------------------------------------- public

    @property
    def source(self) -> str:
        return self.source_combo.currentText()

    @property
    def show_difference(self) -> bool:
        return self.difference_box.isChecked()

    def set_sources(self, names: list[str]) -> None:
        current = self.source_combo.currentText()
        self.source_combo.blockSignals(True)
        self.source_combo.clear()
        self.source_combo.addItems(names)
        if current in names:
            self.source_combo.setCurrentText(current)
        self.source_combo.blockSignals(False)

    def set_sections(
        self,
        left: np.ndarray | None,
        right: np.ndarray | None,
        axes: AxisMap | None = None,
        left_title: str = "",
        right_title: str = "",
    ) -> None:
        if left is not None:
            self.left.set_section(left, axes, left_title)
        if right is not None:
            self.right.set_section(right, axes, right_title)
        self._update_stats()

    # --------------------------------------------------------------- private

    def _source_changed(self, text: str) -> None:
        self.sourceChanged.emit(text)

    def _difference_toggled(self, _on: bool) -> None:
        self.sourceChanged.emit(self.source)

    def _mirror(self, target: SliceView, x: float, y: float) -> None:
        if self._syncing:
            return
        self._syncing = True
        try:
            target.set_marker(x, y, show_lines=False)
        finally:
            self._syncing = False

    def _mirror_click(self, target: SliceView, x: float, y: float) -> None:
        if self._syncing:
            return
        self._syncing = True
        try:
            target.set_marker(x, y, show_lines=True)
        finally:
            self._syncing = False

    def _toggle_roi(self, on: bool) -> None:
        self.left.set_roi_visible(on)
        self.right.set_roi_visible(on)

    def _sync_roi(self, source: SliceView, target: SliceView) -> None:
        if self._syncing:
            return
        self._syncing = True
        try:
            pos = source.roi.pos()
            size = source.roi.size()
            target.roi.setPos(pos, finish=False)
            target.roi.setSize(size, finish=False)
        finally:
            self._syncing = False

    def _update_stats(self) -> None:
        left = self.left.section
        right = self.right.section
        if left is None or right is None or left.shape != right.shape:
            self.stats.setText("")
            return
        s = difference_stats(left, right)
        if not s:
            self.stats.setText("")
            return
        parts = [
            "RMS orig: %.4g" % s.get("rms_original", 0),
            "RMS diff: %.4g" % s.get("rms_difference", 0),
            "Max |diff|: %.4g" % s.get("max_abs_difference", 0),
        ]
        if "relative_rms_pct" in s:
            parts.append("Rel: %.1f%%" % s["relative_rms_pct"])
        if "correlation" in s:
            parts.append("Corr: %.4f" % s["correlation"])
        self.stats.setText("   ".join(parts))