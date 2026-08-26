"""Interactive 3D volume view built on PyVista / VTK.

Slices are not cut out of the full cube with a VTK cutter - that would mean
re-cutting nine million points every time a slider moves. Each slice is instead
its own flat ``ImageData`` whose scalars are the corresponding numpy section,
so moving a slice is a cheap origin update plus one array swap.

Time runs downwards on screen: the world z axis carries positive time and the
camera is set up with ``up = (0, 0, -1)``, which keeps the depth ticks positive
while preserving the usual seismic look.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
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

from ..core.volume import AXIS_ILINE, AXIS_NAMES, AXIS_TIME, AXIS_XLINE, SeismicVolume
from .display import DisplaySettings, vtk_colormap_name

try:  # VTK needs a working OpenGL context; fail soft so the 2D tools survive.
    import pyvista as pv
    from pyvistaqt import QtInteractor

    PYVISTA_AVAILABLE = True
    PYVISTA_ERROR = ""
except Exception as exc:  # pragma: no cover - depends on the local GL stack
    pv = None
    QtInteractor = None
    PYVISTA_AVAILABLE = False
    PYVISTA_ERROR = str(exc)


@dataclass
class SlicePlane:
    """One movable plane in the 3D scene."""

    axis: int
    index: int
    mesh: object              # pv.ImageData
    actor: object             # vtk actor
    visible: bool = True

    def label(self, volume: SeismicVolume) -> str:
        return "%s  %s" % (
            AXIS_NAMES[self.axis],
            volume.geometry.axis_label(self.axis, self.index),
        )


class Volume3DView(QWidget):
    """3D scene with a set of user-managed, movable slice planes."""

    sliceChanged = pyqtSignal(int, int)   # axis, index

    def __init__(self, settings: DisplaySettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.volume: SeismicVolume | None = None
        self.planes: list[SlicePlane] = []
        self._outline_actor = None
        self._widget_enabled = False
        self._updating = False

        self._build_ui()
        if PYVISTA_AVAILABLE:
            self.settings.changed.connect(self._apply_settings)

    # ------------------------------------------------------------------ setup

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)

        if not PYVISTA_AVAILABLE:
            message = QLabel(
                "3D view unavailable - PyVista/VTK could not be initialised.\n\n%s\n\n"
                "The 2D, filtering and spectrum tools are unaffected." % PYVISTA_ERROR
            )
            message.setWordWrap(True)
            message.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(message)
            self.plotter = None
            return

        self.plotter = QtInteractor(self)
        self.plotter.set_background("#1b1d21", top="#2c3038")
        layout.addWidget(self.plotter.interactor, 1)
        layout.addWidget(self._build_panel(), 0)

    def _build_panel(self) -> QWidget:
        panel = QWidget()
        panel.setMaximumWidth(260)
        box = QVBoxLayout(panel)
        box.setContentsMargins(4, 4, 4, 4)

        # -- slice management ------------------------------------------------
        group = QGroupBox("Slices in the 3D view")
        inner = QVBoxLayout(group)

        row = QHBoxLayout()
        for axis, text in ((AXIS_ILINE, "+ IL"), (AXIS_XLINE, "+ XL"), (AXIS_TIME, "+ Time")):
            button = QPushButton(text)
            button.setToolTip("Add a %s slice" % AXIS_NAMES[axis].lower())
            button.clicked.connect(lambda _=False, a=axis: self.add_slice(a))
            row.addWidget(button)
        inner.addLayout(row)

        self.slice_list = QListWidget()
        self.slice_list.setMaximumHeight(130)
        self.slice_list.currentRowChanged.connect(self._selection_changed)
        self.slice_list.itemChanged.connect(self._item_toggled)
        inner.addWidget(self.slice_list)

        remove = QPushButton("Remove selected")
        remove.clicked.connect(self.remove_selected)
        inner.addWidget(remove)
        box.addWidget(group)

        # -- position --------------------------------------------------------
        move_group = QGroupBox("Move selected slice")
        move = QVBoxLayout(move_group)

        self.position_slider = QSlider(Qt.Orientation.Horizontal)
        self.position_slider.setEnabled(False)
        self.position_slider.valueChanged.connect(self._slider_moved)
        move.addWidget(self.position_slider)

        spin_row = QHBoxLayout()
        spin_row.addWidget(QLabel("Index"))
        self.position_spin = QSpinBox()
        self.position_spin.setEnabled(False)
        self.position_spin.valueChanged.connect(self._slider_moved)
        spin_row.addWidget(self.position_spin, 1)
        move.addLayout(spin_row)

        self.drag_box = QCheckBox("Drag handle in 3D")
        self.drag_box.setToolTip(
            "Attach a VTK plane widget to the selected slice so it can be\n"
            "dragged directly in the 3D scene."
        )
        self.drag_box.toggled.connect(self._toggle_plane_widget)
        move.addWidget(self.drag_box)
        box.addWidget(move_group)

        # -- scene ------------------------------------------------------------
        scene_group = QGroupBox("Scene")
        scene = QVBoxLayout(scene_group)

        self.outline_box = QCheckBox("Survey outline")
        self.outline_box.setChecked(True)
        self.outline_box.toggled.connect(self._toggle_outline)
        scene.addWidget(self.outline_box)

        self.axes_box = QCheckBox("Axes and bounds")
        self.axes_box.setChecked(True)
        self.axes_box.toggled.connect(self._toggle_bounds)
        scene.addWidget(self.axes_box)

        view_row = QHBoxLayout()
        view_row.addWidget(QLabel("View"))
        self.view_combo = QComboBox()
        self.view_combo.addItems(["Isometric", "Inline", "Crossline", "Map (time)"])
        self.view_combo.currentTextChanged.connect(self.set_camera_preset)
        view_row.addWidget(self.view_combo, 1)
        scene.addLayout(view_row)

        reset = QPushButton("Reset camera")
        reset.clicked.connect(lambda: self.set_camera_preset(self.view_combo.currentText()))
        scene.addWidget(reset)

        self.hint = QLabel(
            "Left drag rotates, wheel zooms,\nmiddle drag pans, right drag dollies."
        )
        self.hint.setStyleSheet("color:#888;")
        self.hint.setWordWrap(True)
        scene.addWidget(self.hint)

        box.addWidget(scene_group)
        box.addStretch(1)
        return panel

    # ----------------------------------------------------------------- volume

    @property
    def available(self) -> bool:
        return PYVISTA_AVAILABLE and self.plotter is not None

    def set_volume(self, volume: SeismicVolume | None) -> None:
        if not self.available:
            return

        self.clear()
        self.volume = volume
        if volume is None:
            self.plotter.render()
            return

        self._spacing = self._compute_spacing(volume)
        self._add_outline()

        # A starting arrangement that immediately shows the cube's interior.
        self.add_slice(AXIS_ILINE, volume.n_iline // 2, render=False)
        self.add_slice(AXIS_XLINE, volume.n_xline // 2, render=False)
        self.add_slice(AXIS_TIME, volume.n_time // 2, render=False)

        self._toggle_bounds(self.axes_box.isChecked())
        self.set_camera_preset(self.view_combo.currentText())
        self.plotter.render()

    def _compute_spacing(self, volume: SeismicVolume) -> tuple[float, float, float]:
        """World units per index.

        The bin spacing is normalised so the cube keeps a sensible shape
        whatever the survey dimensions, and time is exaggerated a little
        because a flat pancake is hard to interpret.
        """
        longest = max(volume.n_iline, volume.n_xline)
        return (
            100.0 / longest,
            100.0 / longest,
            70.0 / max(volume.n_time, 1),
        )

    def clear(self) -> None:
        if not self.available:
            return
        self._disable_plane_widget()
        for plane in self.planes:
            try:
                self.plotter.remove_actor(plane.actor, render=False)
            except Exception:
                pass
        self.planes.clear()
        self.slice_list.clear()

        if self._outline_actor is not None:
            try:
                self.plotter.remove_actor(self._outline_actor, render=False)
            except Exception:
                pass
            self._outline_actor = None

        try:
            self.plotter.remove_scalar_bar()
        except Exception:
            pass
        self.volume = None

    # ----------------------------------------------------------------- slices

    def add_slice(self, axis: int, index: int | None = None, render: bool = True) -> None:
        if not self.available or self.volume is None:
            return

        n = self.volume.axis_size(axis)
        if index is None:
            index = n // 2
        index = int(np.clip(index, 0, n - 1))

        mesh = self._make_plane_mesh(axis, index)
        actor = self.plotter.add_mesh(
            mesh,
            scalars="amplitude",
            cmap=vtk_colormap_name(self.settings.cmap, self.settings.reverse),
            clim=self.settings.levels,
            lighting=False,
            show_scalar_bar=False,
            interpolate_before_map=True,
            name="slice_%d_%d" % (axis, len(self.planes)),
        )

        plane = SlicePlane(axis=axis, index=index, mesh=mesh, actor=actor)
        self.planes.append(plane)

        item = QListWidgetItem(plane.label(self.volume))
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked)
        self.slice_list.addItem(item)
        self.slice_list.setCurrentRow(self.slice_list.count() - 1)

        self._refresh_scalar_bar()
        if self.axes_box.isChecked():
            self._toggle_bounds(True)
        if render:
            self.plotter.render()

    def _make_plane_mesh(self, axis: int, index: int):
        """A flat ImageData carrying the section's amplitudes as point scalars."""
        assert self.volume is not None
        sx, sy, sz = self._spacing
        n_il, n_xl, n_t = self.volume.shape
        section = self.volume.slice(axis, index)

        if axis == AXIS_ILINE:                      # (n_xl, n_t)
            dims = (1, n_xl, n_t)
            origin = (index * sx, 0.0, 0.0)
        elif axis == AXIS_XLINE:                    # (n_il, n_t)
            dims = (n_il, 1, n_t)
            origin = (0.0, index * sy, 0.0)
        else:                                       # (n_il, n_xl)
            dims = (n_il, n_xl, 1)
            origin = (0.0, 0.0, index * sz)

        mesh = pv.ImageData(dimensions=dims, spacing=(sx, sy, sz), origin=origin)
        # ImageData expects x to vary fastest; the sections above are already
        # ordered so that Fortran flattening produces exactly that.
        mesh.point_data["amplitude"] = np.ascontiguousarray(
            section.flatten(order="F"), dtype=np.float32
        )
        return mesh

    def move_slice(self, position: int, index: int, render: bool = True) -> None:
        """Slide an existing plane to a new index without rebuilding the actor."""
        if not self.available or self.volume is None:
            return
        if not (0 <= position < len(self.planes)):
            return

        plane = self.planes[position]
        n = self.volume.axis_size(plane.axis)
        index = int(np.clip(index, 0, n - 1))
        if index == plane.index:
            return

        sx, sy, sz = self._spacing
        plane.index = index
        if plane.axis == AXIS_ILINE:
            plane.mesh.origin = (index * sx, 0.0, 0.0)
        elif plane.axis == AXIS_XLINE:
            plane.mesh.origin = (0.0, index * sy, 0.0)
        else:
            plane.mesh.origin = (0.0, 0.0, index * sz)

        section = self.volume.slice(plane.axis, index)
        plane.mesh.point_data["amplitude"] = np.ascontiguousarray(
            section.flatten(order="F"), dtype=np.float32
        )

        item = self.slice_list.item(position)
        if item is not None:
            item.setText(plane.label(self.volume))

        if render:
            self.plotter.render()
        self.sliceChanged.emit(plane.axis, index)

    def remove_selected(self) -> None:
        row = self.slice_list.currentRow()
        if not self.available or not (0 <= row < len(self.planes)):
            return

        self._disable_plane_widget()
        plane = self.planes.pop(row)
        try:
            self.plotter.remove_actor(plane.actor, render=False)
        except Exception:
            pass
        self.slice_list.takeItem(row)
        self._refresh_scalar_bar()
        if self.axes_box.isChecked():
            self._toggle_bounds(True)
        self.plotter.render()

    def set_slice_for_axis(self, axis: int, index: int) -> None:
        """Sync helper: move the first plane on ``axis``, or create one."""
        for position, plane in enumerate(self.planes):
            if plane.axis == axis:
                self.move_slice(position, index)
                if self.slice_list.currentRow() == position:
                    self._sync_position_controls(plane)
                return
        self.add_slice(axis, index)

    # -------------------------------------------------------------- selection

    def _selection_changed(self, row: int) -> None:
        if not (0 <= row < len(self.planes)):
            self.position_slider.setEnabled(False)
            self.position_spin.setEnabled(False)
            self._disable_plane_widget()
            return

        plane = self.planes[row]
        self._sync_position_controls(plane)
        if self.drag_box.isChecked():
            self._enable_plane_widget(plane)

    def _sync_position_controls(self, plane: SlicePlane) -> None:
        if self.volume is None:
            return
        n = self.volume.axis_size(plane.axis)

        self._updating = True
        try:
            for control in (self.position_slider, self.position_spin):
                control.setEnabled(True)
                control.setRange(0, n - 1)
                control.setValue(plane.index)
        finally:
            self._updating = False

    def _slider_moved(self, value: int) -> None:
        if self._updating:
            return
        row = self.slice_list.currentRow()
        if not (0 <= row < len(self.planes)):
            return

        self._updating = True
        try:
            self.position_slider.setValue(value)
            self.position_spin.setValue(value)
        finally:
            self._updating = False

        self.move_slice(row, value)
        if self._widget_enabled:
            self._enable_plane_widget(self.planes[row])

    def _item_toggled(self, item: QListWidgetItem) -> None:
        row = self.slice_list.row(item)
        if not (0 <= row < len(self.planes)):
            return
        visible = item.checkState() == Qt.CheckState.Checked
        plane = self.planes[row]
        plane.visible = visible
        try:
            plane.actor.SetVisibility(visible)
        except Exception:
            pass
        self.plotter.render()

    # ---------------------------------------------------------- plane widget

    def _toggle_plane_widget(self, on: bool) -> None:
        row = self.slice_list.currentRow()
        if on and 0 <= row < len(self.planes):
            self._enable_plane_widget(self.planes[row])
        else:
            self._disable_plane_widget()

    def _enable_plane_widget(self, plane: SlicePlane) -> None:
        """Attach a draggable VTK plane to the selected slice."""
        if not self.available or self.volume is None:
            return
        self._disable_plane_widget()

        sx, sy, sz = self._spacing
        normal = [(1, 0, 0), (0, 1, 0), (0, 0, 1)][plane.axis]
        origin = [
            plane.index * sx if plane.axis == AXIS_ILINE else self.volume.n_iline * sx / 2,
            plane.index * sy if plane.axis == AXIS_XLINE else self.volume.n_xline * sy / 2,
            plane.index * sz if plane.axis == AXIS_TIME else self.volume.n_time * sz / 2,
        ]
        step = (sx, sy, sz)[plane.axis]

        def moved(widget_normal, widget_origin) -> None:
            index = int(round(widget_origin[plane.axis] / step)) if step else 0
            row = self.slice_list.currentRow()
            if 0 <= row < len(self.planes):
                self._updating = True
                try:
                    clamped = int(
                        np.clip(index, 0, self.volume.axis_size(plane.axis) - 1)
                    )
                    self.position_slider.setValue(clamped)
                    self.position_spin.setValue(clamped)
                finally:
                    self._updating = False
                self.move_slice(row, index)

        try:
            self.plotter.add_plane_widget(
                callback=moved,
                normal=normal,
                origin=origin,
                normal_rotation=False,
                outline_translation=False,
                implicit=True,
                factor=1.05,
                test_callback=False,
            )
            self._widget_enabled = True
        except Exception:
            # Older or headless VTK builds may refuse; the slider still works.
            self._widget_enabled = False
            self.drag_box.blockSignals(True)
            self.drag_box.setChecked(False)
            self.drag_box.blockSignals(False)

    def _disable_plane_widget(self) -> None:
        if not self.available:
            return
        try:
            self.plotter.clear_plane_widgets()
        except Exception:
            pass
        self._widget_enabled = False

    # ------------------------------------------------------------------ scene

    def _add_outline(self) -> None:
        if self.volume is None:
            return
        sx, sy, sz = self._spacing
        n_il, n_xl, n_t = self.volume.shape
        box = pv.Box(
            bounds=(0.0, (n_il - 1) * sx, 0.0, (n_xl - 1) * sy, 0.0, (n_t - 1) * sz)
        )
        self._outline_actor = self.plotter.add_mesh(
            box.outline(), color="#9aa4b2", line_width=1.5, name="outline"
        )
        self._outline_actor.SetVisibility(self.outline_box.isChecked())

    def _toggle_outline(self, on: bool) -> None:
        if self._outline_actor is not None:
            self._outline_actor.SetVisibility(on)
            self.plotter.render()

    def _toggle_bounds(self, on: bool) -> None:
        if not self.available:
            return
        try:
            if on and self.volume is not None:
                # The scene uses normalised world units so the cube keeps a
                # sensible shape; axes_ranges relabels the ticks with the real
                # survey numbers and two-way time.
                geometry = self.volume.geometry
                n_il, n_xl, n_t = self.volume.shape
                self.plotter.show_bounds(
                    grid="back",
                    location="outer",
                    xtitle="Inline",
                    ytitle="Crossline",
                    ztitle="Time (ms)",
                    axes_ranges=[
                        geometry.iline_label(0),
                        geometry.iline_label(n_il - 1),
                        geometry.xline_label(0),
                        geometry.xline_label(n_xl - 1),
                        geometry.time_label(0),
                        geometry.time_label(n_t - 1),
                    ],
                    color="#c8cdd4",
                    font_size=12,
                    n_xlabels=4,
                    n_ylabels=4,
                    n_zlabels=5,
                    fmt="%.0f",
                    use_3d_text=False,
                )
            else:
                self.plotter.remove_bounds_axes()
        except Exception:
            pass
        self.plotter.render()

    def _refresh_scalar_bar(self) -> None:
        try:
            self.plotter.remove_scalar_bar()
        except Exception:
            pass
        if not self.planes:
            return
        try:
            self.plotter.add_scalar_bar(
                title="Amplitude",
                mapper=self.planes[0].actor.mapper,
                color="#e6e9ee",
                title_font_size=13,
                label_font_size=10,
                width=0.06,
                height=0.35,
                position_x=0.90,
                position_y=0.06,
                vertical=True,
            )
        except Exception:
            pass

    def _apply_settings(self) -> None:
        if not self.available or not self.planes:
            return
        cmap = vtk_colormap_name(self.settings.cmap, self.settings.reverse)
        lo, hi = self.settings.levels
        for plane in self.planes:
            try:
                plane.actor.mapper.lookup_table.cmap = cmap
                plane.actor.mapper.scalar_range = (lo, hi)
            except Exception:
                pass
        self.plotter.render()

    # ----------------------------------------------------------------- camera

    def set_camera_preset(self, preset: str) -> None:
        """Point the camera at the cube, always with time going down."""
        if not self.available or self.volume is None:
            return

        sx, sy, sz = self._spacing
        n_il, n_xl, n_t = self.volume.shape
        cx, cy, cz = (n_il - 1) * sx / 2, (n_xl - 1) * sy / 2, (n_t - 1) * sz / 2
        span = max((n_il - 1) * sx, (n_xl - 1) * sy, (n_t - 1) * sz) or 1.0

        if preset == "Inline":
            position = (cx - 2.4 * span, cy, cz)
            up = (0.0, 0.0, -1.0)
        elif preset == "Crossline":
            position = (cx, cy - 2.4 * span, cz)
            up = (0.0, 0.0, -1.0)
        elif preset.startswith("Map"):
            position = (cx, cy, cz - 2.4 * span)
            up = (0.0, 1.0, 0.0)
        else:
            position = (cx - 1.7 * span, cy - 2.0 * span, cz - 1.5 * span)
            up = (0.0, 0.0, -1.0)

        self.plotter.camera_position = [position, (cx, cy, cz), up]
        self.plotter.reset_camera()
        self.plotter.render()

    def screenshot(self, path: str) -> None:
        if self.available:
            self.plotter.screenshot(path)

    def close_view(self) -> None:
        """Release the VTK render window; call before the app exits."""
        if self.available:
            try:
                self.plotter.close()
            except Exception:
                pass
