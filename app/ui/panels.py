"""Dock panels: navigation, display, filters and volume management."""

from __future__ import annotations

import numpy as np
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..core.filters import SharpenParams, SmoothParams
from ..core.volume import AXIS_ILINE, AXIS_NAMES, AXIS_TIME, AXIS_XLINE, SeismicVolume
from .display import COLORMAPS, DisplaySettings


# ===================================================================
#  Slice Navigator
# ===================================================================


class SliceNavigator(QWidget):
    """Direction combo + index slider/spin for picking a 2D section."""

    sliceChanged = pyqtSignal(int, int)   # axis, index

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._volume: SeismicVolume | None = None
        self._updating = False
        # Remember the last position on each axis so switching back
        # returns to where the user was.
        self._indices: dict[int, int] = {AXIS_ILINE: 0, AXIS_XLINE: 0, AXIS_TIME: 0}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # -- direction -------------------------------------------------------
        dir_row = QHBoxLayout()
        dir_row.addWidget(QLabel("Direction"))
        self.direction = QComboBox()
        for axis in (AXIS_ILINE, AXIS_XLINE, AXIS_TIME):
            self.direction.addItem(AXIS_NAMES[axis], axis)
        dir_row.addWidget(self.direction, 1)
        layout.addLayout(dir_row)

        # -- slider -----------------------------------------------------------
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setMinimum(0)
        self.slider.setMaximum(0)
        layout.addWidget(self.slider)

        # -- spin + label -----------------------------------------------------
        spin_row = QHBoxLayout()
        spin_row.addWidget(QLabel("Index"))
        self.spin = QSpinBox()
        self.spin.setMinimum(0)
        self.spin.setMaximum(0)
        spin_row.addWidget(self.spin, 1)
        self.pos_label = QLabel("")
        spin_row.addWidget(self.pos_label)
        layout.addLayout(spin_row)

        # -- step buttons -----------------------------------------------------
        step_row = QHBoxLayout()
        for text, delta in (("◀◀", -10), ("◀", -1), ("▶", 1), ("▶▶", 10)):
            btn = QPushButton(text)
            btn.setMaximumWidth(42)
            btn.clicked.connect(lambda _=False, d=delta: self._step(d))
            step_row.addWidget(btn)
        layout.addLayout(step_row)

        layout.addStretch(1)

        # -- wiring -----------------------------------------------------------
        self.direction.currentIndexChanged.connect(self._direction_changed)
        self.slider.valueChanged.connect(self._index_changed)
        self.spin.valueChanged.connect(self._index_changed)

    # --------------------------------------------------------------- public

    @property
    def axis(self) -> int:
        return self.direction.currentData()

    @property
    def index(self) -> int:
        return self.slider.value()

    def set_volume(self, volume: SeismicVolume | None) -> None:
        self._volume = volume
        self._indices = {AXIS_ILINE: 0, AXIS_XLINE: 0, AXIS_TIME: 0}
        if volume is not None:
            for ax in (AXIS_ILINE, AXIS_XLINE, AXIS_TIME):
                self._indices[ax] = volume.axis_size(ax) // 2
        self._apply_axis()

    def set_slice(self, axis: int, index: int) -> None:
        """Programmatic jump (e.g. from the 3D view)."""
        self._updating = True
        try:
            combo_idx = self.direction.findData(axis)
            if combo_idx >= 0 and self.direction.currentIndex() != combo_idx:
                self._indices[self.axis] = self.slider.value()
                self.direction.setCurrentIndex(combo_idx)
            self.slider.setValue(int(index))
            self.spin.setValue(int(index))
        finally:
            self._updating = False
        self._emit()

    # --------------------------------------------------------------- private

    def _apply_axis(self) -> None:
        self._updating = True
        try:
            axis = self.axis
            vol = self._volume
            n = vol.axis_size(axis) if vol else 0
            maximum = max(0, n - 1)
            idx = min(self._indices.get(axis, 0), maximum)
            self.slider.setMaximum(maximum)
            self.spin.setMaximum(maximum)
            self.slider.setValue(idx)
            self.spin.setValue(idx)
            self._update_label()
        finally:
            self._updating = False
        self._emit()

    def _direction_changed(self, _combo_idx: int) -> None:
        prev_axis = None
        for ax in (AXIS_ILINE, AXIS_XLINE, AXIS_TIME):
            if ax != self.axis:
                if self._indices.get(ax) == self.slider.value():
                    prev_axis = ax
                    break
        if prev_axis is not None:
            self._indices[prev_axis] = self.slider.value()
        else:
            for ax in (AXIS_ILINE, AXIS_XLINE, AXIS_TIME):
                if ax != self.axis:
                    pass
            self._indices[self.axis] = self._indices.get(self.axis, 0)
        self._apply_axis()

    def _index_changed(self, value: int) -> None:
        if self._updating:
            return
        self._updating = True
        try:
            self.slider.setValue(value)
            self.spin.setValue(value)
            self._indices[self.axis] = value
            self._update_label()
        finally:
            self._updating = False
        self._emit()

    def _step(self, delta: int) -> None:
        self.slider.setValue(self.slider.value() + delta)

    def _update_label(self) -> None:
        vol = self._volume
        if vol is None:
            self.pos_label.setText("")
            return
        self.pos_label.setText(vol.geometry.axis_label(self.axis, self.slider.value()))

    def _emit(self) -> None:
        if not self._updating:
            self.sliceChanged.emit(self.axis, self.index)


# ===================================================================
#  Display Controls
# ===================================================================


class DisplayControls(QWidget):
    """Colormap picker and amplitude window controls."""

    def __init__(self, settings: DisplaySettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._updating = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # -- colormap ---------------------------------------------------------
        cmap_group = QGroupBox("Colour map")
        cmap_box = QVBoxLayout(cmap_group)

        cmap_row = QHBoxLayout()
        self.cmap_combo = QComboBox()
        self.cmap_combo.addItems(COLORMAPS)
        self.cmap_combo.setCurrentText(settings.cmap)
        cmap_row.addWidget(self.cmap_combo, 1)

        self.reverse_box = QCheckBox("Reverse")
        self.reverse_box.setChecked(settings.reverse_cmap)
        cmap_row.addWidget(self.reverse_box)
        cmap_box.addLayout(cmap_row)
        layout.addWidget(cmap_group)

        # -- amplitude window -------------------------------------------------
        amp_group = QGroupBox("Amplitude window")
        amp_box = QVBoxLayout(amp_group)

        self.auto_box = QCheckBox("Auto clip")
        self.auto_box.setChecked(settings.auto_levels)
        amp_box.addWidget(self.auto_box)

        pct_row = QHBoxLayout()
        pct_row.addWidget(QLabel("Percentile"))
        self.pct_spin = QDoubleSpinBox()
        self.pct_spin.setRange(50.0, 100.0)
        self.pct_spin.setSingleStep(0.5)
        self.pct_spin.setValue(settings.percentile)
        pct_row.addWidget(self.pct_spin)
        amp_box.addLayout(pct_row)

        min_row = QHBoxLayout()
        min_row.addWidget(QLabel("Min"))
        self.min_spin = QDoubleSpinBox()
        self.min_spin.setRange(-1e9, 1e9)
        self.min_spin.setDecimals(4)
        self.min_spin.setValue(settings.levels[0])
        min_row.addWidget(self.min_spin)
        amp_box.addLayout(min_row)

        max_row = QHBoxLayout()
        max_row.addWidget(QLabel("Max"))
        self.max_spin = QDoubleSpinBox()
        self.max_spin.setRange(-1e9, 1e9)
        self.max_spin.setDecimals(4)
        self.max_spin.setValue(settings.levels[1])
        max_row.addWidget(self.max_spin)
        amp_box.addLayout(max_row)

        btn_row = QHBoxLayout()
        sym_btn = QPushButton("Make symmetric")
        sym_btn.setToolTip("Set min = -max so zero stays in the middle of the colormap")
        sym_btn.clicked.connect(self._make_symmetric)
        btn_row.addWidget(sym_btn)

        rescale_btn = QPushButton("Rescale now")
        rescale_btn.setToolTip("Recompute levels from the currently displayed section")
        rescale_btn.clicked.connect(self._rescale_now)
        btn_row.addWidget(rescale_btn)
        amp_box.addLayout(btn_row)

        layout.addWidget(amp_group)
        layout.addStretch(1)

        # -- wiring -----------------------------------------------------------
        self.cmap_combo.currentTextChanged.connect(self._cmap_changed)
        self.reverse_box.toggled.connect(self._cmap_changed)
        self.auto_box.toggled.connect(self._auto_changed)
        self.pct_spin.valueChanged.connect(self._pct_changed)
        self.min_spin.valueChanged.connect(self._levels_changed)
        self.max_spin.valueChanged.connect(self._levels_changed)
        settings.changed.connect(self._pull_from_settings)

    # --------------------------------------------------------------- slots

    def _cmap_changed(self, _: object = None) -> None:
        self.settings.set_cmap(self.cmap_combo.currentText(), self.reverse_box.isChecked())

    def _auto_changed(self, on: bool) -> None:
        self.settings.set_auto_levels(on)

    def _pct_changed(self, val: float) -> None:
        self.settings.set_percentile(val)

    def _levels_changed(self, _: float = 0.0) -> None:
        if self._updating:
            return
        self.settings.set_levels(self.min_spin.value(), self.max_spin.value(), from_user=True)

    def _make_symmetric(self) -> None:
        lim = max(abs(self.min_spin.value()), abs(self.max_spin.value()))
        self.settings.set_levels(-lim, lim, from_user=True)

    def _rescale_now(self) -> None:
        self.settings.set_auto_levels(True)
        self.auto_box.setChecked(True)

    def _pull_from_settings(self) -> None:
        self._updating = True
        try:
            self.cmap_combo.setCurrentText(self.settings.cmap)
            self.reverse_box.setChecked(self.settings.reverse_cmap)
            self.auto_box.setChecked(self.settings.auto_levels)
            self.pct_spin.setValue(self.settings.percentile)
            vmin, vmax = self.settings.levels
            self.min_spin.setValue(vmin)
            self.max_spin.setValue(vmax)
        finally:
            self._updating = False


# ===================================================================
#  Filter Controls
# ===================================================================


class FilterControls(QWidget):
    """Gaussian smoothing and sharpening parameter panel."""

    previewRequested = pyqtSignal(str)    # "smooth" | "sharpen"
    applyToVolume = pyqtSignal(str)       # "smooth" | "sharpen"
    paramsChanged = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # -- smooth -----------------------------------------------------------
        smooth_group = QGroupBox("Gaussian smoothing")
        sg = QVBoxLayout(smooth_group)

        row = QHBoxLayout()
        row.addWidget(QLabel("σ trace"))
        self.sigma_trace = QDoubleSpinBox()
        self.sigma_trace.setRange(0.0, 50.0)
        self.sigma_trace.setSingleStep(0.5)
        self.sigma_trace.setValue(1.5)
        row.addWidget(self.sigma_trace)
        row.addWidget(QLabel("σ time"))
        self.sigma_time = QDoubleSpinBox()
        self.sigma_time.setRange(0.0, 50.0)
        self.sigma_time.setSingleStep(0.5)
        self.sigma_time.setValue(1.5)
        row.addWidget(self.sigma_time)
        sg.addLayout(row)

        btn_row = QHBoxLayout()
        preview_smooth = QPushButton("Preview on slice")
        preview_smooth.clicked.connect(lambda: self.previewRequested.emit("smooth"))
        btn_row.addWidget(preview_smooth)
        apply_smooth = QPushButton("Apply to volume")
        apply_smooth.clicked.connect(lambda: self.applyToVolume.emit("smooth"))
        btn_row.addWidget(apply_smooth)
        sg.addLayout(btn_row)
        layout.addWidget(smooth_group)

        # -- sharpen ----------------------------------------------------------
        sharpen_group = QGroupBox("Image sharpening")
        shg = QVBoxLayout(sharpen_group)

        method_row = QHBoxLayout()
        method_row.addWidget(QLabel("Method"))
        self.method_combo = QComboBox()
        self.method_combo.addItems(["unsharp", "laplacian"])
        method_row.addWidget(self.method_combo, 1)
        shg.addLayout(method_row)

        params_row = QHBoxLayout()
        params_row.addWidget(QLabel("σ"))
        self.sharpen_sigma = QDoubleSpinBox()
        self.sharpen_sigma.setRange(0.1, 50.0)
        self.sharpen_sigma.setSingleStep(0.5)
        self.sharpen_sigma.setValue(1.0)
        params_row.addWidget(self.sharpen_sigma)
        params_row.addWidget(QLabel("Amount"))
        self.amount = QDoubleSpinBox()
        self.amount.setRange(0.0, 20.0)
        self.amount.setSingleStep(0.1)
        self.amount.setValue(1.0)
        params_row.addWidget(self.amount)
        shg.addLayout(params_row)

        btn_row2 = QHBoxLayout()
        preview_sharpen = QPushButton("Preview on slice")
        preview_sharpen.clicked.connect(lambda: self.previewRequested.emit("sharpen"))
        btn_row2.addWidget(preview_sharpen)
        apply_sharpen = QPushButton("Apply to volume")
        apply_sharpen.clicked.connect(lambda: self.applyToVolume.emit("sharpen"))
        btn_row2.addWidget(apply_sharpen)
        shg.addLayout(btn_row2)
        layout.addWidget(sharpen_group)

        # -- live preview -----------------------------------------------------
        self.live_box = QCheckBox("Live preview")
        self.live_box.setToolTip("Update the comparison view as sliders move")
        self.live_box.setChecked(True)
        layout.addWidget(self.live_box)

        layout.addStretch(1)

        # -- wiring -----------------------------------------------------------
        for spin in (self.sigma_trace, self.sigma_time, self.sharpen_sigma, self.amount):
            spin.valueChanged.connect(lambda _: self.paramsChanged.emit())
        self.method_combo.currentTextChanged.connect(lambda _: self.paramsChanged.emit())

    # ----------------------------------------------------------- accessors

    @property
    def live_preview(self) -> bool:
        return self.live_box.isChecked()

    def smooth_params(self) -> SmoothParams:
        return SmoothParams(
            sigma_trace=self.sigma_trace.value(),
            sigma_time=self.sigma_time.value(),
        )

    def sharpen_params(self) -> SharpenParams:
        return SharpenParams(
            sigma=self.sharpen_sigma.value(),
            amount=self.amount.value(),
            method=self.method_combo.currentText(),
        )


# ===================================================================
#  Volume Panel
# ===================================================================


class VolumePanel(QWidget):
    """Lists loaded volumes and shows the summary of the active one."""

    activeChanged = pyqtSignal(int)
    removeRequested = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self.volume_list = QListWidget()
        self.volume_list.setMaximumHeight(100)
        layout.addWidget(self.volume_list)

        remove = QPushButton("Remove selected")
        remove.clicked.connect(self._remove)
        layout.addWidget(remove)

        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet("color: #aaa; font-size: 11px;")
        layout.addWidget(self.summary)

        layout.addStretch(1)

        self.volume_list.currentRowChanged.connect(self._selection_changed)

    def refresh(self, volumes: list[SeismicVolume], active: int) -> None:
        self.volume_list.blockSignals(True)
        self.volume_list.clear()
        for vol in volumes:
            self.volume_list.addItem(vol.name)
        if 0 <= active < len(volumes):
            self.volume_list.setCurrentRow(active)
            self.summary.setText(volumes[active].summary())
        else:
            self.summary.setText("")
        self.volume_list.blockSignals(False)

    def _selection_changed(self, row: int) -> None:
        if row >= 0:
            self.activeChanged.emit(row)

    def _remove(self) -> None:
        row = self.volume_list.currentRow()
        if row >= 0:
            self.removeRequested.emit(row)