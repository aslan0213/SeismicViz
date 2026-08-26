"""Headless-ish exercise of the whole application.

Builds the real window, loads the sample cube, walks through every tab and
saves a screenshot of each, so the UI can be checked without clicking through
it by hand. Run from the project root:

    python tools/smoke_test.py
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import QApplication  # noqa: E402

from app.main import apply_dark_theme  # noqa: E402
from app.ui.main_window import PREVIEW_SHARPEN, PREVIEW_SMOOTH, MainWindow  # noqa: E402
from app.core.volume import AXIS_ILINE, AXIS_TIME, AXIS_XLINE  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOTS = os.path.join(ROOT, "docs", "screenshots")


def pump(app: QApplication, seconds: float = 0.35) -> None:
    """Let Qt process events for a while."""
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.01)


def shot(window: MainWindow, app: QApplication, name: str) -> None:
    pump(app, 0.6)
    os.makedirs(SHOTS, exist_ok=True)
    path = os.path.join(SHOTS, name + ".png")
    window.grab().save(path)
    print("  screenshot -> docs/screenshots/%s.png" % name)


def main() -> int:
    app = QApplication(sys.argv)
    apply_dark_theme(app)

    window = MainWindow()
    window.resize(1600, 1000)
    window.show()
    pump(app, 0.8)

    sample = os.path.join(ROOT, "data", "seismic_synthetic.npy")
    if not os.path.isfile(sample):
        print("FAIL: sample volume missing; run tools/make_synthetic.py first")
        return 1

    print("1. loading volume")
    window.open_volume(sample)
    pump(app, 1.0)
    volume = window.active_volume
    assert volume is not None, "volume did not become active"
    print("   active: %s %s" % (volume.name, volume.shape))
    assert window.slice_view.section is not None, "2D view has no section"
    shot(window, app, "01_2d_inline")

    print("2. stepping through slices and orientations")
    window.navigator.set_slice(AXIS_ILINE, volume.n_iline // 3)
    pump(app)
    window.navigator.set_slice(AXIS_XLINE, volume.n_xline // 2)
    pump(app)
    assert window.slice_view.section.shape == (volume.n_iline, volume.n_time)
    shot(window, app, "02_2d_crossline")

    window.navigator.set_slice(AXIS_TIME, volume.n_time // 3)
    pump(app)
    assert window.slice_view.section.shape == (volume.n_iline, volume.n_xline)
    window.navigator.set_slice(AXIS_ILINE, volume.n_iline // 2)
    pump(app)

    print("3. display settings")
    window.settings.set_cmap("petrel")
    window.settings.set_percentile(97.0)
    pump(app)
    window.settings.set_levels(-0.5, 0.5)
    pump(app)
    assert window.slice_view.image.levels is not None
    window.settings.set_cmap("seismic")
    window.settings.set_auto_levels(True)
    pump(app)

    print("4. 3D view")
    window.tabs.setCurrentWidget(window.view3d)
    pump(app, 1.2)
    if window.view3d.available:
        print("   planes: %d" % len(window.view3d.planes))
        assert len(window.view3d.planes) == 3, "expected IL/XL/Time planes"
        window.view3d.slice_list.setCurrentRow(0)
        pump(app)
        window.view3d.move_slice(0, volume.n_iline // 4)
        pump(app, 0.5)
        assert window.view3d.planes[0].index == volume.n_iline // 4
        window.view3d.add_slice(AXIS_XLINE, volume.n_xline // 4)
        pump(app, 0.5)
        assert len(window.view3d.planes) == 4
        window.view3d.set_camera_preset("Isometric")
        pump(app, 0.8)
        # QWidget.grab() cannot capture a native OpenGL surface, so the 3D
        # viewport is captured through VTK instead of the Qt screenshot path.
        os.makedirs(SHOTS, exist_ok=True)
        window.view3d.screenshot(os.path.join(SHOTS, "03b_3d_vtk.png"))
        print("  screenshot -> docs/screenshots/03b_3d_vtk.png (via VTK)")

        window.view3d.slice_list.setCurrentRow(1)
        window.view3d.move_slice(1, volume.n_xline // 5)
        window.view3d.set_camera_preset("Inline")
        pump(app, 0.9)
        window.view3d.screenshot(os.path.join(SHOTS, "03c_3d_inline_view.png"))
        print("  screenshot -> docs/screenshots/03c_3d_inline_view.png (via VTK)")

        window.view3d.set_camera_preset("Isometric")
        window.view3d.slice_list.setCurrentRow(window.view3d.slice_list.count() - 1)
        window.view3d.remove_selected()
        pump(app, 0.4)
    else:
        print("   SKIPPED - PyVista unavailable")

    print("5. compare / synchronisation")
    window.tabs.setCurrentWidget(window.compare_view)
    window.compare_view.source_combo.setCurrentText(PREVIEW_SMOOTH)
    window.filter_controls.sigma_trace.setValue(2.0)
    window.filter_controls.sigma_time.setValue(2.0)
    pump(app, 0.6)
    left = window.compare_view.left.section
    right = window.compare_view.right.section
    assert left is not None and right is not None, "compare panels are empty"
    assert left.shape == right.shape
    assert float(abs(right).std()) < float(abs(left).std()), "smoothing did not reduce variance"
    print("   smoothed std %.4f vs original %.4f" % (right.std(), left.std()))

    # Crosshair mirroring.
    window.compare_view.left.cursorClicked.emit(80.0, 500.0)
    pump(app, 0.3)
    assert window.compare_view.right.marker.isVisible(), "crosshair was not mirrored"
    shot(window, app, "04_compare_smoothed")

    window.compare_view.source_combo.setCurrentText(PREVIEW_SHARPEN)
    window.filter_controls.amount.setValue(1.5)
    pump(app, 0.6)
    sharpened = window.compare_view.right.section
    assert sharpened is not None
    assert float(abs(sharpened).std()) > float(abs(left).std()), "sharpening did not add detail"
    print("   sharpened std %.4f vs original %.4f" % (sharpened.std(), left.std()))
    shot(window, app, "05_compare_sharpened")

    print("6. spectrum via the C# module")
    window.compare_view.roi_box.setChecked(True)
    pump(app, 0.4)
    window.spectrum_panel.auto_box.setChecked(False)
    window._send_to_spectrum_module()

    deadline = time.perf_counter() + 30.0
    while time.perf_counter() < deadline and not window.spectrum_panel._results:
        pump(app, 0.2)
    results = window.spectrum_panel._results
    assert results, "no spectrum came back: %s" % window.spectrum_panel.status.text()
    print("   %d curves, %d bins" % (len(results), len(results[0][1])))
    print("   " + window.spectrum_panel.stats.text().replace("\n", "\n   "))
    shot(window, app, "06_spectrum")

    print("7. arbitrary line")
    window.tabs.setCurrentWidget(window.arbitrary_view)
    pump(app, 0.8)
    window.arbitrary_view.update_section()
    pump(app, 0.6)
    composite = window.arbitrary_view.section
    assert composite is not None and composite.shape[1] == volume.n_time
    print("   composite %s" % (composite.shape,))
    shot(window, app, "07_arbitrary_line")

    print("8. whole-volume filter")
    window.tabs.setCurrentWidget(window.compare_view)
    before = len(window.volumes)
    window._apply_filter_to_volume("smooth")
    deadline = time.perf_counter() + 60.0
    while time.perf_counter() < deadline and len(window.volumes) == before:
        pump(app, 0.2)
    assert len(window.volumes) == before + 1, "filtered volume was not created"
    print("   created: %s" % window.volumes[-1].name)
    pump(app, 0.8)
    shot(window, app, "08_compare_volumes")

    print("9. shutdown")
    window.close()
    pump(app, 0.5)
    print("\nSMOKE TEST OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())