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

        box.addWidget(scene_group)
        box.addStretch(1)
        return panel

    # ----------------------------------------------------------------- state

    @property
    def available(self) -> bool:
        return PYVISTA_AVAILABLE and self.plotter is not None

    # ---------------------------------------------------------------- volume

    def set_volume(self, volume: SeismicVolume | None) -> None:
        if not self.available:
            return
        self.volume = volume
        self._clear_scene()
        if volume is None:
            return

        self._spacing = self._compute_spacing(volume)
        self._add_outline(volume)

        # Default slices: one of each axis through the middle.
        for axis in (AXIS_ILINE, AXIS_XLINE, AXIS_TIME):
            self.add_slice(axis, volume.axis_size(axis) // 2)

        self._apply_bounds()
        self.set_camera_preset("Isometric")

    def _compute_spacing(self, volume: SeismicVolume) -> tuple[float, float, float]:
        longest = max(volume.n_iline, volume.n_xline)
        return (
            100.0 / max(longest, 1),
            100.0 / max(longest, 1),
            70.0 / max(volume.n_time, 1),
        )

    # --------------------------------------------------------------- outline

    def _add_outline(self, volume: SeismicVolume) -> None:
        if not self.available:
            return
        sx, sy, sz = self._spacing
        bounds = [
            0, volume.n_iline * sx,
            0, volume.n_xline * sy,
            0, volume.n_time * sz,
        ]
        mesh = pv.Box(bounds=bounds)
        self._outline_actor = self.plotter.add_mesh(
            mesh, style="wireframe", color="#555555", line_width=1.5, name="outline"
        )

    def _toggle_outline(self, on: bool) -> None:
        if self._outline_actor is not None:
            self._outline_actor.SetVisibility(on)
            self.plotter.render()

    def _apply_bounds(self) -> None:
        if not self.available or self.volume is None:
            return
        vol = self.volume
        sx, sy, sz = self._spacing
        if self.axes_box.isChecked():
            self.plotter.show_bounds(
                bounds=[0, vol.n_iline * sx, 0, vol.n_xline * sy, 0, vol.n_time * sz],
                xlabel="Inline", ylabel="Crossline", zlabel="Time",
                xtitle="", ytitle="", ztitle="",
                show_xlabels=True, show_ylabels=True, show_zlabels=True,
                color="#888888", font_size=9,
            )
        else:
            try:
                self.plotter.remove_bounds_axes()
            except Exception:
                pass

    def _toggle_bounds(self, _on: bool) -> None:
        self._apply_bounds()
        self.plotter.render()

    # ----------------------------------------------------------- slice mgmt

    def add_slice(self, axis: int, index: int | None = None) -> None:
        if not self.available or self.volume is None:
            return
        vol = self.volume
        if index is None:
            index = vol.axis_size(axis) // 2
        index = int(np.clip(index, 0, vol.axis_size(axis) - 1))

        mesh = self._make_plane_mesh(vol, axis, index)
        cmap = vtk_colormap_name(self.settings.cmap)
        clim = self.settings.levels
        actor = self.plotter.add_mesh(
            mesh, scalars="amplitude", cmap=cmap, clim=clim,
            show_scalar_bar=False, name="plane_%d_%d" % (len(self.planes), axis),
        )

        plane = SlicePlane(axis=axis, index=index, mesh=mesh, actor=actor)
        self.planes.append(plane)

        item = QListWidgetItem(plane.label(vol))
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked)
        self.slice_list.addItem(item)
        self.slice_list.setCurrentRow(self.slice_list.count() - 1)

        self.plotter.render()

    def _make_plane_mesh(self, volume: SeismicVolume, axis: int, index: int):
        section = volume.slice(axis, index)
        sx, sy, sz = self._spacing

        if axis == AXIS_ILINE:
            dims = (1, section.shape[0], section.shape[1])
            spacing = (sx, sy, sz)
            origin = (index * sx, 0, 0)
        elif axis == AXIS_XLINE:
            dims = (section.shape[0], 1, section.shape[1])
            spacing = (sx, sy, sz)
            origin = (0, index * sy, 0)
        else:  # AXIS_TIME
            dims = (section.shape[0], section.shape[1], 1)
            spacing = (sx, sy, sz)
            origin = (0, 0, index * sz)

        mesh = pv.ImageData(dimensions=dims, spacing=spacing, origin=origin)
        mesh.point_data["amplitude"] = section.flatten(order="F").astype(np.float32)
        return mesh

    def move_slice(self, row: int, new_index: int) -> None:
        if not self.available or self.volume is None:
            return
        if not (0 <= row < len(self.planes)):
            return
        plane = self.planes[row]
        vol = self.volume
        new_index = int(np.clip(new_index, 0, vol.axis_size(plane.axis) - 1))
        if new_index == plane.index:
            return

        plane.index = new_index
        new_mesh = self._make_plane_mesh(vol, plane.axis, new_index)
        plane.mesh = new_mesh

        cmap = vtk_colormap_name(self.settings.cmap)
        clim = self.settings.levels
        self.plotter.add_mesh(
            new_mesh, scalars="amplitude", cmap=cmap, clim=clim,
            show_scalar_bar=False, name="plane_%d_%d" % (row, plane.axis),
        )

        item = self.slice_list.item(row)
        if item is not None:
            item.setText(plane.label(vol))

        self.plotter.render()
        self.sliceChanged.emit(plane.axis, new_index)

    def remove_selected(self) -> None:
        row = self.slice_list.currentRow()
        if row < 0 or row >= len(self.planes):
            return
        self._remove_plane_widget()
        plane = self.planes.pop(row)
        try:
            self.plotter.remove_actor("plane_%d_%d" % (row, plane.axis))
        except Exception:
            pass
        self.slice_list.takeItem(row)
        # Rename remaining actors for consistency.
        for i, p in enumerate(self.planes):
            pass  # actors already have unique names from creation
        self.plotter.render()

    def set_slice_for_axis(self, axis: int, index: int) -> None:
        """Move the first plane on *axis* to *index*, used by 2D↔3D sync."""
        for row, plane in enumerate(self.planes):
            if plane.axis == axis:
                self.move_slice(row, index)
                return

    # -------------------------------------------------------- selection / UI

    def _selection_changed(self, row: int) -> None:
        self._remove_plane_widget()
        if not self.available or self.volume is None or not (0 <= row < len(self.planes)):
            self.position_slider.setEnabled(False)
            self.position_spin.setEnabled(False)
            return

        plane = self.planes[row]
        vol = self.volume
        maximum = vol.axis_size(plane.axis) - 1

        self._updating = True
        self.position_slider.setEnabled(True)
        self.position_spin.setEnabled(True)
        self.position_slider.setMaximum(maximum)
        self.position_spin.setMaximum(maximum)
        self.position_slider.setValue(plane.index)
        self.position_spin.setValue(plane.index)
        self._updating = False

    def _slider_moved(self, value: int) -> None:
        if self._updating:
            return
        row = self.slice_list.currentRow()
        if row < 0:
            return
        self._updating = True
        self.position_slider.setValue(value)
        self.position_spin.setValue(value)
        self._updating = False
        self.move_slice(row, value)

    def _item_toggled(self, item: QListWidgetItem) -> None:
        row = self.slice_list.row(item)
        if 0 <= row < len(self.planes):
            visible = item.checkState() == Qt.CheckState.Checked
            self.planes[row].visible = visible
            if self.planes[row].actor is not None:
                self.planes[row].actor.SetVisibility(visible)
            self.plotter.render()

    # -------------------------------------------------------- plane widget

    def _toggle_plane_widget(self, on: bool) -> None:
        if on:
            self._add_plane_widget()
        else:
            self._remove_plane_widget()

    def _add_plane_widget(self) -> None:
        if not self.available or self.volume is None:
            return
        row = self.slice_list.currentRow()
        if not (0 <= row < len(self.planes)):
            return
        plane = self.planes[row]
        vol = self.volume
        sx, sy, sz = self._spacing

        if plane.axis == AXIS_ILINE:
            normal, origin_pt = (1, 0, 0), (plane.index * sx, vol.n_xline * sy / 2, vol.n_time * sz / 2)
        elif plane.axis == AXIS_XLINE:
            normal, origin_pt = (0, 1, 0), (vol.n_iline * sx / 2, plane.index * sy, vol.n_time * sz / 2)
        else:
            normal, origin_pt = (0, 0, 1), (vol.n_iline * sx / 2, vol.n_xline * sy / 2, plane.index * sz)

        bounds = [0, vol.n_iline * sx, 0, vol.n_xline * sy, 0, vol.n_time * sz]

        def moved(normal_vec, origin_vec):
            if plane.axis == AXIS_ILINE:
                idx = int(round(origin_vec[0] / sx))
            elif plane.axis == AXIS_XLINE:
                idx = int(round(origin_vec[1] / sy))
            else:
                idx = int(round(origin_vec[2] / sz))
            idx = int(np.clip(idx, 0, vol.axis_size(plane.axis) - 1))
            if idx != plane.index:
                self._updating = True
                self.position_slider.setValue(idx)
                self.position_spin.setValue(idx)
                self._updating = False
                self.move_slice(row, idx)

        self.plotter.add_plane_widget(
            callback=moved,
            normal=normal,
            origin=origin_pt,
            bounds=bounds,
            factor=1.0,
            color="#ffab00",
            assign_to_axis=None,
            tubing=False,
            outline_translation=False,
        )
        self._widget_enabled = True

    def _remove_plane_widget(self) -> None:
        if self._widget_enabled and self.available:
            try:
                self.plotter.clear_plane_widgets()
            except Exception:
                pass
            self._widget_enabled = False
        if hasattr(self, "drag_box"):
            self.drag_box.blockSignals(True)
            self.drag_box.setChecked(False)
            self.drag_box.blockSignals(False)

    # --------------------------------------------------------------- display

    def _apply_settings(self) -> None:
        if not self.available or self.volume is None:
            return
        cmap = vtk_colormap_name(self.settings.cmap)
        clim = self.settings.levels
        for row, plane in enumerate(self.planes):
            try:
                self.plotter.update_scalars(
                    plane.mesh.point_data["amplitude"],
                    mesh=plane.mesh,
                    render=False,
                )
                mapper = plane.actor.GetMapper()
                mapper.SetScalarRange(clim[0], clim[1])
                lut = mapper.GetLookupTable()
                if lut is not None:
                    lut.SetRange(clim[0], clim[1])
            except Exception:
                pass
        self.plotter.render()

    # --------------------------------------------------------------- camera

    def set_camera_preset(self, name: str) -> None:
        if not self.available or self.volume is None:
            return
        vol = self.volume
        sx, sy, sz = self._spacing
        cx = vol.n_iline * sx / 2
        cy = vol.n_xline * sy / 2
        cz = vol.n_time * sz / 2
        dist = max(vol.n_iline * sx, vol.n_xline * sy, vol.n_time * sz) * 2.5

        if name == "Inline":
            self.plotter.camera_position = [
                (cx - dist, cy, cz), (cx, cy, cz), (0, 0, -1)
            ]
        elif name == "Crossline":
            self.plotter.camera_position = [
                (cx, cy - dist, cz), (cx, cy, cz), (0, 0, -1)
            ]
        elif name == "Map (time)":
            self.plotter.camera_position = [
                (cx, cy, cz - dist), (cx, cy, cz), (0, -1, 0)
            ]
        else:  # Isometric
            self.plotter.camera_position = [
                (cx + dist * 0.6, cy - dist * 0.6, cz - dist * 0.5),
                (cx, cy, cz),
                (0, 0, -1),
            ]
        self.plotter.render()

    # --------------------------------------------------------------- export

    def screenshot(self, path: str) -> None:
        if self.available:
            self.plotter.screenshot(path)

    # --------------------------------------------------------------- cleanup

    def _clear_scene(self) -> None:
        if not self.available:
            return
        self._remove_plane_widget()
        self.planes.clear()
        self.slice_list.clear()
        self.plotter.clear()
        self._outline_actor = None

    def close_view(self) -> None:
        if self.available:
            try:
                self.plotter.close()
            except Exception:
                pass