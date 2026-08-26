"""Application shell: menus, docks, tabs and the wiring between them."""

from __future__ import annotations

import os
import traceback

import numpy as np
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import (
    QApplication,
    QDockWidget,
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QTabWidget,
    QWidget,
)

from ..core import filters
from ..core.spectrum_client import SpectrumClient, SpectrumError, build_module
from ..core.volume import AXIS_ILINE, AXIS_NAMES, AXIS_TIME, AXIS_XLINE, SeismicVolume
from .arbitrary_view import ArbitraryLineView
from .compare_view import CompareView
from .display import DisplaySettings
from .panels import DisplayControls, FilterControls, SliceNavigator, VolumePanel
from .slice_view import AxisMap, SliceView
from .spectrum_panel import SpectrumPanel
from .view3d import Volume3DView

PREVIEW_SMOOTH = "Gaussian smoothing (preview of this slice)"
PREVIEW_SHARPEN = "Image sharpening (preview of this slice)"


def axis_map_for(volume: SeismicVolume, axis: int) -> AxisMap:
    """Axis calibration for a section taken along ``axis``."""
    geometry = volume.geometry
    if axis == AXIS_ILINE:                        # section is (crossline, time)
        return AxisMap(
            x0=geometry.xline_label(0),
            dx=geometry.xl_step,
            y0=geometry.time_label(0),
            dy=geometry.dt * 1000.0,
            x_label="Crossline",
            y_label="Time (ms)",
        )
    if axis == AXIS_XLINE:                        # section is (inline, time)
        return AxisMap(
            x0=geometry.iline_label(0),
            dx=geometry.il_step,
            y0=geometry.time_label(0),
            dy=geometry.dt * 1000.0,
            x_label="Inline",
            y_label="Time (ms)",
        )
    return AxisMap(                               # time slice is (inline, crossline)
        x0=geometry.iline_label(0),
        dx=geometry.il_step,
        y0=geometry.xline_label(0),
        dy=geometry.xl_step,
        x_label="Inline",
        y_label="Crossline",
    )


class FilterWorker(QThread):
    """Runs a whole-cube filter off the GUI thread, with progress and cancel."""

    progressed = pyqtSignal(int)
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, volume: SeismicVolume, kind: str, params, parent=None) -> None:
        super().__init__(parent)
        self._volume = volume
        self._kind = kind
        self._params = params
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:  # noqa: D102 - QThread entry point
        def report(percent: int) -> bool:
            self.progressed.emit(percent)
            return not self._cancelled

        try:
            source = np.asarray(self._volume.data)
            if self._kind == "smooth":
                result = filters.gaussian_smooth_3d(source, self._params, report)
            else:
                result = filters.sharpen_3d(source, self._params, report)
        except Exception as exc:
            self.failed.emit("%s: %s" % (type(exc).__name__, exc))
            return
        self.completed.emit(result)


class MainWindow(QMainWindow):
    """The application's single top-level window."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Seismic Volume Explorer")
        self.resize(1500, 950)
        self.setAcceptDrops(True)

        self.volumes: list[SeismicVolume] = []
        self.active_index: int = -1
        self._syncing_3d = False
        self._filter_worker: FilterWorker | None = None

        self.settings = DisplaySettings(self)
        self.settings_b = DisplaySettings(self)
        self.settings_b.copy_from(self.settings)
        self.settings.changed.connect(self._mirror_settings)

        self.spectrum_client = SpectrumClient()

        self._build_tabs()
        self._build_docks()
        self._build_menus()
        self._connect_signals()

        self.statusBar().showMessage("Open a .npy seismic volume to begin (Ctrl+O).")
        self._update_enabled_state()

    # ------------------------------------------------------------------ layout

    def _build_tabs(self) -> None:
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self.slice_view = SliceView(self.settings, "No volume loaded")
        self.tabs.addTab(self.slice_view, "2D Section")

        self.view3d = Volume3DView(self.settings)
        self.tabs.addTab(self.view3d, "3D View")

        self.compare_view = CompareView(self.settings, self.settings_b)
        self.tabs.addTab(self.compare_view, "Compare / Sync")

        self.arbitrary_view = ArbitraryLineView(self.settings)
        self.tabs.addTab(self.arbitrary_view, "Arbitrary Line")

        self.tabs.currentChanged.connect(self._tab_changed)

    def _build_docks(self) -> None:
        self.navigator = SliceNavigator()
        self.volume_panel = VolumePanel()
        self.display_controls = DisplayControls(self.settings)
        self.filter_controls = FilterControls()
        self.spectrum_panel = SpectrumPanel(self.spectrum_client)

        self.dock_volumes = self._add_dock(
            "Volumes", self.volume_panel, Qt.DockWidgetArea.LeftDockWidgetArea
        )
        self.dock_navigation = self._add_dock(
            "Slice navigation", self.navigator, Qt.DockWidgetArea.LeftDockWidgetArea
        )
        self.dock_display = self._add_dock(
            "Display", self.display_controls, Qt.DockWidgetArea.LeftDockWidgetArea
        )
        self.dock_filters = self._add_dock(
            "Filters", self.filter_controls, Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.dock_spectrum = self._add_dock(
            "Spectrum", self.spectrum_panel, Qt.DockWidgetArea.BottomDockWidgetArea
        )
        self.dock_spectrum.setMinimumHeight(300)

    def _add_dock(self, title: str, widget: QWidget, area) -> QDockWidget:
        dock = QDockWidget(title, self)
        dock.setObjectName("dock_" + title.lower().replace(" ", "_"))
        dock.setWidget(widget)
        self.addDockWidget(area, dock)
        return dock

    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("&File")

        open_action = QAction("&Open volume...", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self.open_volume_dialog)
        file_menu.addAction(open_action)

        sample_action = QAction("Open &sample volume", self)
        sample_action.triggered.connect(self.open_sample_volume)
        file_menu.addAction(sample_action)

        file_menu.addSeparator()

        self.save_action = QAction("&Save active volume as...", self)
        self.save_action.triggered.connect(self.save_active_volume)
        file_menu.addAction(self.save_action)

        self.export_action = QAction("&Export current view as image...", self)
        self.export_action.triggered.connect(self.export_current_view)
        file_menu.addAction(self.export_action)

        file_menu.addSeparator()
        quit_action = QAction("E&xit", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        view_menu = self.menuBar().addMenu("&View")
        for dock in (
            self.dock_volumes,
            self.dock_navigation,
            self.dock_display,
            self.dock_filters,
            self.dock_spectrum,
        ):
            view_menu.addAction(dock.toggleViewAction())

        view_menu.addSeparator()
        self.sync3d_action = QAction("Sync 3D slices with the 2D navigator", self)
        self.sync3d_action.setCheckable(True)
        self.sync3d_action.setChecked(True)
        view_menu.addAction(self.sync3d_action)

        tools_menu = self.menuBar().addMenu("&Tools")

        restart_action = QAction("Restart spectrum module", self)
        restart_action.triggered.connect(self.restart_spectrum_module)
        tools_menu.addAction(restart_action)

        rebuild_action = QAction("Rebuild C# spectrum module", self)
        rebuild_action.triggered.connect(self.rebuild_spectrum_module)
        tools_menu.addAction(rebuild_action)

        help_menu = self.menuBar().addMenu("&Help")
        manual_action = QAction("Quick manual", self)
        manual_action.triggered.connect(self.show_manual)
        help_menu.addAction(manual_action)

        about_action = QAction("About", self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)

    def _connect_signals(self) -> None:
        self.navigator.sliceChanged.connect(self._on_slice_changed)
        self.volume_panel.activeChanged.connect(self.set_active_volume)
        self.volume_panel.removeRequested.connect(self.remove_volume)

        self.filter_controls.previewRequested.connect(self._preview_filter)
        self.filter_controls.applyToVolume.connect(self._apply_filter_to_volume)
        self.filter_controls.paramsChanged.connect(self._on_filter_params_changed)

        self.compare_view.sourceChanged.connect(lambda _: self._update_compare())
        self.compare_view.roiChanged.connect(self._on_roi_changed)
        self.slice_view.roiChanged.connect(self._on_roi_changed)
        self.arbitrary_view.section_view.roiChanged.connect(self._on_roi_changed)

        self.spectrum_panel.requestSections.connect(self._send_to_spectrum_module)
        self.view3d.sliceChanged.connect(self._on_3d_slice_changed)

    # ----------------------------------------------------------------- volumes

    @property
    def active_volume(self) -> SeismicVolume | None:
        if 0 <= self.active_index < len(self.volumes):
            return self.volumes[self.active_index]
        return None

    def open_volume_dialog(self) -> None:
        start = os.path.join(_project_root(), "data")
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Open seismic volume", start if os.path.isdir(start) else "",
            "NumPy volumes (*.npy);;All files (*)",
        )
        for path in paths:
            self.open_volume(path)

    def open_sample_volume(self) -> None:
        path = os.path.join(_project_root(), "data", "seismic_synthetic.npy")
        if not os.path.isfile(path):
            QMessageBox.information(
                self,
                "Sample not found",
                "No sample volume yet. Generate one with:\n\n"
                "    python tools/make_synthetic.py",
            )
            return
        self.open_volume(path)

    def open_volume(self, path: str) -> None:
        try:
            volume = SeismicVolume.load(path)
        except Exception as exc:
            QMessageBox.critical(self, "Cannot open volume", "%s\n\n%s" % (path, exc))
            return
        self.add_volume(volume)
        self.statusBar().showMessage(
            "Loaded %s  -  %d x %d x %d  (%.1f MB)"
            % (volume.name, volume.n_iline, volume.n_xline, volume.n_time, volume.nbytes_mb())
        )

    def add_volume(self, volume: SeismicVolume) -> None:
        self.volumes.append(volume)
        self.set_active_volume(len(self.volumes) - 1, force=True)

    def remove_volume(self, index: int) -> None:
        if not (0 <= index < len(self.volumes)):
            return
        self.volumes.pop(index)
        if not self.volumes:
            self.active_index = -1
            self.navigator.set_volume(None)
            self.view3d.set_volume(None)
            self.arbitrary_view.set_volume(None)
            self.slice_view.clear()
            self.compare_view.left.clear()
            self.compare_view.right.clear()
            self.volume_panel.refresh([], -1)
            self._update_enabled_state()
            return
        self.set_active_volume(min(index, len(self.volumes) - 1), force=True)

    def set_active_volume(self, index: int, force: bool = False) -> None:
        if not (0 <= index < len(self.volumes)):
            return
        if index == self.active_index and not force:
            return

        self.active_index = index
        volume = self.volumes[index]

        self.volume_panel.refresh(self.volumes, index)
        self.navigator.set_volume(volume)
        self.arbitrary_view.set_volume(volume)
        if self.view3d.available:
            self.view3d.set_volume(volume)

        self._refresh_compare_sources()
        self._update_enabled_state()
        self._on_slice_changed(self.navigator.axis, self.navigator.index)

    def _update_enabled_state(self) -> None:
        has_volume = self.active_volume is not None
        for widget in (self.save_action, self.export_action):
            widget.setEnabled(has_volume)
        self.filter_controls.setEnabled(has_volume)
        self.spectrum_panel.compute_button.setEnabled(has_volume)

    # ------------------------------------------------------------------ slices

    def _on_slice_changed(self, axis: int, index: int) -> None:
        volume = self.active_volume
        if volume is None:
            return

        section = volume.slice(axis, index)
        axes = axis_map_for(volume, axis)
        title = "%s  -  %s  %s" % (
            volume.name,
            AXIS_NAMES[axis],
            volume.geometry.axis_label(axis, index),
        )
        self.slice_view.set_section(section, axes, title)

        self._update_compare()

        if self.sync3d_action.isChecked() and self.view3d.available and not self._syncing_3d:
            self._syncing_3d = True
            try:
                self.view3d.set_slice_for_axis(axis, index)
            finally:
                self._syncing_3d = False

        self.spectrum_panel.request_update()

    def _on_3d_slice_changed(self, axis: int, index: int) -> None:
        if not self.sync3d_action.isChecked() or self._syncing_3d:
            return
        self._syncing_3d = True
        try:
            self.navigator.set_slice(axis, index)
        finally:
            self._syncing_3d = False

    def _tab_changed(self, _index: int) -> None:
        self.spectrum_panel.request_update()

    # ----------------------------------------------------------------- compare

    def _refresh_compare_sources(self) -> None:
        names = [PREVIEW_SMOOTH, PREVIEW_SHARPEN]
        names += [
            volume.name
            for position, volume in enumerate(self.volumes)
            if position != self.active_index
        ]
        self.compare_view.set_sources(names)

    def _update_compare(self) -> None:
        volume = self.active_volume
        if volume is None:
            return

        axis, index = self.navigator.axis, self.navigator.index
        left = volume.slice(axis, index)
        axes = axis_map_for(volume, axis)
        left_title = "%s  -  %s %s" % (
            volume.name,
            AXIS_NAMES[axis],
            volume.geometry.axis_label(axis, index),
        )

        right, right_title = self._compute_comparison(left, axis, index)

        if right is not None and self.compare_view.show_difference:
            right = right - left
            right_title = "Difference:  %s  minus reference" % right_title
            self.settings_b.set_auto_levels(True)
            self.settings_b.autoscale_to(right)
        else:
            self._mirror_settings()

        self.compare_view.set_sections(left, right, axes, left_title, right_title)

    def _compute_comparison(
        self, left: np.ndarray, axis: int, index: int
    ) -> tuple[np.ndarray | None, str]:
        """The right-hand section for the current comparison source."""
        source = self.compare_view.source

        if source == PREVIEW_SMOOTH:
            params = self.filter_controls.smooth_params()
            return filters.gaussian_smooth_2d(left, params), "Gaussian smoothed  (%s)" % (
                params.describe()
            )

        if source == PREVIEW_SHARPEN:
            params = self.filter_controls.sharpen_params()
            return filters.sharpen_2d(left, params), "Sharpened  (%s)" % params.describe()

        for volume in self.volumes:
            if volume.name != source:
                continue
            if volume.axis_size(axis) <= index:
                return None, "%s: slice %d is out of range" % (volume.name, index)

            section = volume.slice(axis, index)
            if section.shape != left.shape:
                return None, "%s: incompatible geometry" % volume.name
            return section, "%s  -  %s %s" % (
                volume.name,
                AXIS_NAMES[axis],
                volume.geometry.axis_label(axis, index),
            )

        return None, ""

    def _mirror_settings(self) -> None:
        """Keep the comparison panel on the reference panel's scale."""
        if self.compare_view.show_difference:
            return
        self.settings_b.copy_from(self.settings)

    def _on_filter_params_changed(self) -> None:
        if not self.filter_controls.live_preview:
            return
        if self.compare_view.source in (PREVIEW_SMOOTH, PREVIEW_SHARPEN):
            self._update_compare()

    def _preview_filter(self, kind: str) -> None:
        self.compare_view.source_combo.setCurrentText(
            PREVIEW_SMOOTH if kind == "smooth" else PREVIEW_SHARPEN
        )
        self.tabs.setCurrentWidget(self.compare_view)
        self._update_compare()

    # ------------------------------------------------------------ whole volume

    def _apply_filter_to_volume(self, kind: str) -> None:
        volume = self.active_volume
        if volume is None or (self._filter_worker and self._filter_worker.isRunning()):
            return

        if kind == "smooth":
            params = self.filter_controls.smooth_params()
            suffix = params.describe()
        else:
            params = self.filter_controls.sharpen_params()
            suffix = params.describe()

        dialog = QProgressDialog(
            "Filtering %s (%s)..." % (volume.name, suffix), "Cancel", 0, 100, self
        )
        dialog.setWindowTitle("Applying filter")
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setMinimumDuration(0)
        dialog.setValue(0)

        worker = FilterWorker(volume, kind, params, self)
        self._filter_worker = worker

        worker.progressed.connect(dialog.setValue)
        dialog.canceled.connect(worker.cancel)

        def on_completed(result: object) -> None:
            dialog.close()
            if result is None:
                self.statusBar().showMessage("Filtering cancelled.")
                return
            new_volume = volume.derived(np.asarray(result), suffix)
            self.volumes.append(new_volume)
            self.volume_panel.refresh(self.volumes, self.active_index)
            self._refresh_compare_sources()
            self.compare_view.source_combo.setCurrentText(new_volume.name)
            self.tabs.setCurrentWidget(self.compare_view)
            self._update_compare()
            self.statusBar().showMessage("Created %s" % new_volume.name)

        def on_failed(message: str) -> None:
            dialog.close()
            QMessageBox.critical(self, "Filter failed", message)

        worker.completed.connect(on_completed)
        worker.failed.connect(on_failed)
        worker.finished.connect(lambda: setattr(self, "_filter_worker", None))
        worker.start()

    # ---------------------------------------------------------------- spectrum

    def _on_roi_changed(self) -> None:
        self.spectrum_panel.request_update()

    def _send_to_spectrum_module(self) -> None:
        volume = self.active_volume
        if volume is None:
            return

        widget = self.tabs.currentWidget()
        dt = volume.geometry.dt

        if widget is self.arbitrary_view:
            section = self.arbitrary_view.roi_section()
            if section is None:
                self.spectrum_panel.status.setText("Extract an arbitrary line first.")
                return
            jobs = [("Composite line", section)]
            note = self.arbitrary_view.roi_description()

        elif widget is self.compare_view:
            if self.navigator.axis == AXIS_TIME:
                self._warn_time_slice()
                return
            jobs = []
            for view, label in (
                (self.compare_view.left, "Reference"),
                (self.compare_view.right, "Comparison"),
            ):
                section = view.roi_section()
                if section is not None:
                    jobs.append((label, section))
            note = self.compare_view.left.roi_description()

        else:
            if self.navigator.axis == AXIS_TIME:
                self._warn_time_slice()
                return
            section = self.slice_view.roi_section()
            if section is None:
                return
            jobs = [
                (
                    "%s %s"
                    % (
                        AXIS_NAMES[self.navigator.axis],
                        volume.geometry.axis_label(self.navigator.axis, self.navigator.index),
                    ),
                    section,
                )
            ]
            note = self.slice_view.roi_description()

        self.spectrum_panel.compute(jobs, dt, note)

    def _warn_time_slice(self) -> None:
        self.spectrum_panel.status.setText(
            "A time slice has no time axis, so a frequency spectrum is not defined for it. "
            "Switch the navigator to Inline or Crossline."
        )

    def restart_spectrum_module(self) -> None:
        self.spectrum_client.shutdown()
        try:
            self.spectrum_client.ensure_started()
        except SpectrumError as exc:
            QMessageBox.warning(self, "Spectrum module", str(exc))
        self.spectrum_panel.module_label.setText(self.spectrum_client.status_text())
        self.statusBar().showMessage(self.spectrum_client.status_text())

    def rebuild_spectrum_module(self) -> None:
        self.spectrum_client.shutdown()
        try:
            path = build_module()
        except SpectrumError as exc:
            QMessageBox.critical(self, "Build failed", str(exc))
            return
        QMessageBox.information(self, "Spectrum module", "Rebuilt:\n%s" % path)
        self.restart_spectrum_module()

    # ------------------------------------------------------------------ export

    def save_active_volume(self) -> None:
        volume = self.active_volume
        if volume is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save volume", volume.name + ".npy", "NumPy volumes (*.npy)"
        )
        if not path:
            return
        try:
            volume.save(path)
        except Exception as exc:
            QMessageBox.critical(self, "Save failed", str(exc))
            return
        self.statusBar().showMessage("Saved %s" % path)

    def export_current_view(self) -> None:
        widget = self.tabs.currentWidget()
        path, _ = QFileDialog.getSaveFileName(
            self, "Export view", "view.png", "PNG image (*.png)"
        )
        if not path:
            return

        try:
            if widget is self.view3d and self.view3d.available:
                self.view3d.screenshot(path)
            else:
                widget.grab().save(path)
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        self.statusBar().showMessage("Exported %s" % path)

    # -------------------------------------------------------------------- help

    def show_manual(self) -> None:
        QMessageBox.information(
            self,
            "Quick manual",
            "1.  File > Open volume, or drag a .npy file onto the window.\n"
            "2.  Slice navigation picks Inline / Crossline / Time and the index.\n"
            "3.  2D Section: wheel to zoom, drag to pan, drag the colour bar ends\n"
            "     to change the amplitude window.\n"
            "4.  3D View: add IL / XL / Time slices and move them with the slider\n"
            "     or the drag handle.\n"
            "5.  Filters: preview Gaussian smoothing or sharpening on the current\n"
            "     slice, or apply either to the whole cube.\n"
            "6.  Compare / Sync: two panels share zoom, pan and crosshair.\n"
            "7.  Spectrum: tick ROI, drag the rectangle, and the selection is sent\n"
            "     to the external C# module for the average amplitude spectrum.",
        )

    def show_about(self) -> None:
        QMessageBox.about(
            self,
            "About",
            "<b>Seismic Volume Explorer</b><br><br>"
            "3D seismic visualisation and analysis tool.<br>"
            "Python / PyQt6 / pyqtgraph / PyVista, with an independent C#"
            " spectrum-analysis module reached over a loopback socket.<br><br>"
            "%s" % self.spectrum_client.status_text(),
        )

    # ------------------------------------------------------------- drag & drop

    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.mimeData().hasUrls():
            if any(url.toLocalFile().lower().endswith(".npy") for url in event.mimeData().urls()):
                event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt naming
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith(".npy"):
                self.open_volume(path)

    # ---------------------------------------------------------------- shutdown

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if self._filter_worker is not None and self._filter_worker.isRunning():
            self._filter_worker.cancel()
            self._filter_worker.wait(3000)
        self.spectrum_panel.shutdown()
        self.spectrum_client.shutdown()
        self.view3d.close_view()
        super().closeEvent(event)


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def excepthook(exc_type, exc_value, exc_tb) -> None:
    """Show unexpected errors instead of dying silently behind the GUI."""
    text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    if QApplication.instance() is not None:
        QMessageBox.critical(None, "Unexpected error", text)
    print(text)
