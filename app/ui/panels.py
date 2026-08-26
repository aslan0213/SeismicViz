"""Dockable control panels: slice navigation, display settings and filters."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..core.filters import SharpenParams, SmoothParams
from ..core.volume import AXIS_ILINE, AXIS_NAMES, AXIS_TIME, AXIS_XLINE, SeismicVolume
from .display import COLORMAPS, DisplaySettings


class SliceNavigator(QWidget):
    """Pick the section orientation and step through the cube."""

    sliceChanged = pyqtSignal(int, int)   # axis, index

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.volume: SeismicVolume | None = None
        self._indices: dict[int, int] = {}
        self._axis = AXIS_ILINE
        self._updating = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        form = QFormLayout()
        self.axis_combo = QComboBox()
        for axis in (AXIS_ILINE, AXIS_XLINE, AXIS_TIME):
            self.axis_combo.addItem(AXIS_NAMES[axis], axis)
        self.axis_combo.currentIndexChanged.connect(self._axis_changed)
        form.addRow("Direction", self.axis_combo)
        layout.addLayout(form)

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.valueChanged.connect(self._index_changed)
        layout.addWidget(self.slider)

        row = QHBoxLayout()
        self.prev_button = QPushButton("<")
        self.prev_button.setFixedWidth(30)
        self.prev_button.clicked.connect(lambda: self.step(-1))
        row.addWidget(self.prev_button)

        self.spin = QSpinBox()
        self.spin.valueChanged.connect(self._index_changed)
        row.addWidget(self.spin, 1)

        self.next_button = QPushButton(">")
        self.next_button.setFixedWidth(30)
        self.next_button.clicked.connect(lambda: self.step(1))
        row.addWidget(self.next_button)
        layout.addLayout(row)

        self.label = QLabel("-")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setStyleSheet("color:#9aa4b2;")
        layout.addWidget(self.label)
        layout.addStretch(1)

        self.setEnabled(False)

    # ------------------------------------------------------------------ state

    @property
    def axis(self) -> int:
        return self._axis

    @property
    def index(self) -> int:
        return self._indices[self._axis]

    def set_volume(self, volume: SeismicVolume | None) -> None:
        self.volume = volume
        self.setEnabled(volume is not None)
        if volume is None:
            return

        # Keep the current position where possible; a volume opened for the
        # first time starts in the middle of the cube, where there is data.
        for axis in (AXIS_ILINE, AXIS_XLINE, AXIS_TIME):
            size = volume.axis_size(axis)
            if axis in self._indices:
                self._indices[axis] = min(self._indices[axis], size - 1)
            else:
                self._indices[axis] = size // 2
        self._refresh_range(emit=True)

    def set_slice(self, axis: int, index: int) -> None:
        """Programmatic move, e.g. from the 3D view."""
        if self.volume is None:
            return
        index = max(0, min(index, self.volume.axis_size(axis) - 1))
        if axis != self._axis:
            self._axis = axis
            self._updating = True
            try:
                self.axis_combo.setCurrentIndex(self.axis_combo.findData(axis))
            finally:
                self._updating = False
        self._indices[axis] = index
        self._refresh_range(emit=True)

    def step(self, delta: int) -> None:
        if self.volume is None:
            return
        self.spin.setValue(self.index + delta)

    # --------------------------------------------------------------- internals

    def _axis_changed(self, _row: int) -> None:
        if self._updating:
            return
        self._axis = self.axis_combo.currentData()
        self._refresh_range(emit=True)

    def _refresh_range(self, emit: bool) -> None:
        if self.volume is None:
            return
        size = self.volume.axis_size(self._axis)
        index = min(self._indices[self._axis], size - 1)

        self._updating = True
        try:
            for control in (self.slider, self.spin):
                control.setRange(0, size - 1)
                control.setValue(index)
        finally:
            self._updating = False

        self._indices[self._axis] = index
        self._update_label()
        if emit:
            self.sliceChanged.emit(self._axis, index)

    def _index_changed(self, value: int) -> None:
        if self._updating or self.volume is None:
            return
        value = max(0, min(value, self.volume.axis_size(self._axis) - 1))

        self._updating = True
        try:
            self.slider.setValue(value)
            self.spin.setValue(value)
        finally:
            self._updating = False

        self._indices[self._axis] = value
        self._update_label()
        self.sliceChanged.emit(self._axis, value)

    def _update_label(self) -> None:
        if self.volume is None:
            self.label.setText("-")
            return
        geometry = self.volume.geometry
        index = self._indices[self._axis]
        size = self.volume.axis_size(self._axis)
        self.label.setText(
            "%s   (index %d of %d)" % (geometry.axis_label(self._axis, index), index, size - 1)
        )


class DisplayControls(QWidget):
    """Colormap and amplitude window, shared by every view."""

    def __init__(self, settings: DisplaySettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._updating = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        form = QFormLayout()

        self.cmap_combo = QComboBox()
        self.cmap_combo.addItems(COLORMAPS)
        self.cmap_combo.setCurrentText(settings.cmap)
        self.cmap_combo.currentTextChanged.connect(
            lambda name: self.settings.set_cmap(name)
        )
        form.addRow("Colormap", self.cmap_combo)

        self.reverse_box = QCheckBox("Reverse")
        self.reverse_box.toggled.connect(self.settings.set_reverse)
        form.addRow("", self.reverse_box)

        self.auto_box = QCheckBox("Auto clip per slice")
        self.auto_box.setChecked(settings.auto_levels)
        self.auto_box.toggled.connect(self.settings.set_auto_levels)
        form.addRow("", self.auto_box)

        self.percentile_spin = QDoubleSpinBox()
        self.percentile_spin.setRange(50.0, 100.0)
        self.percentile_spin.setSingleStep(0.5)
        self.percentile_spin.setDecimals(1)
        self.percentile_spin.setValue(settings.percentile)
        self.percentile_spin.setToolTip(
            "Amplitude percentile used when auto clipping; lower values give a "
            "harder, higher-contrast display."
        )
        self.percentile_spin.valueChanged.connect(self.settings.set_percentile)
        form.addRow("Clip percentile", self.percentile_spin)

        self.min_spin = QDoubleSpinBox()
        self.max_spin = QDoubleSpinBox()
        for spin in (self.min_spin, self.max_spin):
            spin.setRange(-1e9, 1e9)
            spin.setDecimals(4)
            spin.setSingleStep(0.05)
            spin.valueChanged.connect(self._levels_edited)
        form.addRow("Min amplitude", self.min_spin)
        form.addRow("Max amplitude", self.max_spin)

        layout.addLayout(form)

        buttons = QHBoxLayout()
        symmetric = QPushButton("Make symmetric")
        symmetric.setToolTip("Centre the amplitude window on zero")
        symmetric.clicked.connect(self._make_symmetric)
        buttons.addWidget(symmetric)

        rescale = QPushButton("Rescale now")
        rescale.setToolTip("Re-apply auto clipping to the slice on screen")
        rescale.clicked.connect(lambda: self.settings.set_auto_levels(True))
        buttons.addWidget(rescale)
        layout.addLayout(buttons)

        hint = QLabel(
            "The colour bar beside each section is draggable: pull its ends to "
            "stretch the amplitude window, or drag its middle to shift it."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#9aa4b2;")
        layout.addWidget(hint)
        layout.addStretch(1)

        settings.changed.connect(self._pull_from_settings)
        self._pull_from_settings()

    def _pull_from_settings(self) -> None:
        self._updating = True
        try:
            low, high = self.settings.levels
            self.min_spin.setValue(low)
            self.max_spin.setValue(high)
            self.auto_box.setChecked(self.settings.auto_levels)
            self.cmap_combo.setCurrentText(self.settings.cmap)
            self.reverse_box.setChecked(self.settings.reverse)
        finally:
            self._updating = False

    def _levels_edited(self, _value: float) -> None:
        if self._updating:
            return
        self.settings.set_levels(self.min_spin.value(), self.max_spin.value(), from_user=True)

    def _make_symmetric(self) -> None:
        low, high = self.settings.levels
        level = max(abs(low), abs(high)) or 1.0
        self.settings.set_levels(-level, level, from_user=True)


class FilterControls(QWidget):
    """Gaussian smoothing and sharpening parameters plus the apply actions."""

    paramsChanged = pyqtSignal()
    previewRequested = pyqtSignal(str)      # "smooth" | "sharpen" | "none"
    applyToVolume = pyqtSignal(str)         # "smooth" | "sharpen"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        # -- Gaussian --------------------------------------------------------
        smooth_group = QGroupBox("Gaussian smoothing")
        smooth_form = QFormLayout(smooth_group)

        self.sigma_trace = QDoubleSpinBox()
        self.sigma_trace.setRange(0.0, 20.0)
        self.sigma_trace.setSingleStep(0.25)
        self.sigma_trace.setValue(1.5)
        self.sigma_trace.setToolTip("Standard deviation across traces, in bins")
        self.sigma_trace.valueChanged.connect(lambda _: self.paramsChanged.emit())
        smooth_form.addRow("Sigma (traces)", self.sigma_trace)

        self.sigma_time = QDoubleSpinBox()
        self.sigma_time.setRange(0.0, 20.0)
        self.sigma_time.setSingleStep(0.25)
        self.sigma_time.setValue(1.5)
        self.sigma_time.setToolTip("Standard deviation along time, in samples")
        self.sigma_time.valueChanged.connect(lambda _: self.paramsChanged.emit())
        smooth_form.addRow("Sigma (time)", self.sigma_time)

        smooth_buttons = QHBoxLayout()
        preview_smooth = QPushButton("Preview on slice")
        preview_smooth.clicked.connect(lambda: self.previewRequested.emit("smooth"))
        smooth_buttons.addWidget(preview_smooth)

        apply_smooth = QPushButton("Apply to volume")
        apply_smooth.setToolTip("Create a new smoothed volume for side-by-side comparison")
        apply_smooth.clicked.connect(lambda: self.applyToVolume.emit("smooth"))
        smooth_buttons.addWidget(apply_smooth)
        smooth_form.addRow(smooth_buttons)
        layout.addWidget(smooth_group)

        # -- Sharpening ------------------------------------------------------
        sharpen_group = QGroupBox("Image sharpening")
        sharpen_form = QFormLayout(sharpen_group)

        self.method_combo = QComboBox()
        self.method_combo.addItems(["unsharp", "laplacian"])
        self.method_combo.setToolTip(
            "unsharp: add back the difference from a blurred copy\n"
            "laplacian: add back a discrete second derivative"
        )
        self.method_combo.currentTextChanged.connect(lambda _: self.paramsChanged.emit())
        sharpen_form.addRow("Method", self.method_combo)

        self.sharpen_sigma = QDoubleSpinBox()
        self.sharpen_sigma.setRange(0.1, 20.0)
        self.sharpen_sigma.setSingleStep(0.25)
        self.sharpen_sigma.setValue(1.0)
        self.sharpen_sigma.setToolTip("Blur radius used to build the detail image")
        self.sharpen_sigma.valueChanged.connect(lambda _: self.paramsChanged.emit())
        sharpen_form.addRow("Sigma", self.sharpen_sigma)

        self.amount = QDoubleSpinBox()
        self.amount.setRange(0.0, 10.0)
        self.amount.setSingleStep(0.1)
        self.amount.setValue(1.0)
        self.amount.setToolTip("How much of the detail image is added back")
        self.amount.valueChanged.connect(lambda _: self.paramsChanged.emit())
        sharpen_form.addRow("Amount", self.amount)

        sharpen_buttons = QHBoxLayout()
        preview_sharpen = QPushButton("Preview on slice")
        preview_sharpen.clicked.connect(lambda: self.previewRequested.emit("sharpen"))
        sharpen_buttons.addWidget(preview_sharpen)

        apply_sharpen = QPushButton("Apply to volume")
        apply_sharpen.clicked.connect(lambda: self.applyToVolume.emit("sharpen"))
        sharpen_buttons.addWidget(apply_sharpen)
        sharpen_form.addRow(sharpen_buttons)
        layout.addWidget(sharpen_group)

        self.live_box = QCheckBox("Live preview while editing")
        self.live_box.setChecked(True)
        self.live_box.setToolTip(
            "Recompute the comparison panel as soon as a parameter changes"
        )
        layout.addWidget(self.live_box)

        note = QLabel(
            "'Preview on slice' filters only the section on screen and shows it "
            "in the Compare tab. 'Apply to volume' filters the whole cube and "
            "adds it to the volume list."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color:#9aa4b2;")
        layout.addWidget(note)
        layout.addStretch(1)

    # ------------------------------------------------------------------ values

    def smooth_params(self) -> SmoothParams:
        return SmoothParams(
            sigma_trace=self.sigma_trace.value(), sigma_time=self.sigma_time.value()
        )

    def sharpen_params(self) -> SharpenParams:
        return SharpenParams(
            sigma=self.sharpen_sigma.value(),
            amount=self.amount.value(),
            method=self.method_combo.currentText(),
        )

    @property
    def live_preview(self) -> bool:
        return self.live_box.isChecked()


class VolumePanel(QWidget):
    """List of loaded cubes; the highlighted one drives every view."""

    activeChanged = pyqtSignal(int)
    removeRequested = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._row_changed)
        layout.addWidget(self.list, 1)

        row = QHBoxLayout()
        remove = QPushButton("Remove")
        remove.clicked.connect(lambda: self.removeRequested.emit(self.list.currentRow()))
        row.addWidget(remove)
        layout.addLayout(row)

        self.info = QLabel("No volume loaded.")
        self.info.setWordWrap(True)
        self.info.setStyleSheet("font-family: Consolas, monospace; color:#9aa4b2;")
        layout.addWidget(self.info)

    def refresh(self, volumes: list[SeismicVolume], active: int) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        for volume in volumes:
            item = QListWidgetItem(
                "%s   [%d x %d x %d]"
                % (volume.name, volume.n_iline, volume.n_xline, volume.n_time)
            )
            item.setToolTip(volume.path or "in memory")
            self.list.addItem(item)
        if 0 <= active < len(volumes):
            self.list.setCurrentRow(active)
        self.list.blockSignals(False)

        self.info.setText(volumes[active].summary() if 0 <= active < len(volumes) else "")

    def _row_changed(self, row: int) -> None:
        if row >= 0:
            self.activeChanged.emit(row)