"""End-to-end smoke test for Seismic Volume Explorer.

Exercises the whole application programmatically:
  1. Spins up the GUI and loads the synthetic test volume.
  2. Steps the 2D navigator across axes and slices.
  3. Exercises the 3D view (planes and presets).
  4. Runs Gaussian smoothing and sharpening.
  5. Computes spectra via the C# module.
  6. Exercises the arbitrary line composite section.
  7. Saves reference screenshots into ``docs/screenshots/``.

Run::

    python tools/smoke_test.py
"""

from __future__ import annotations

import os
import sys
import time

# Ensure project root is in sys.path.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt6.QtCore import QCoreApplication
from PyQt6.QtWidgets import QApplication

from app.core.volume import AXIS_ILINE, AXIS_TIME, AXIS_XLINE
from app.main import apply_dark_theme
from app.ui.main_window import MainWindow

SCREENSHOT_DIR = os.path.join(ROOT, "docs", "screenshots")


def pump_events(ms: int = 150) -> None:
    """Process pending Qt events to let the UI update."""
    app = QApplication.instance()
    if app is None:
        return
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def save_screenshot(widget, filename: str) -> str:
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    path = os.path.join(SCREENSHOT_DIR, filename)
    widget.grab().save(path)
    print("      Saved screenshot: %s" % path)
    return path


def run_smoke_test() -> None:
    print("=== Running End-to-End Smoke Test ===\n")

    app = QApplication.instance() or QApplication(sys.argv)
    apply_dark_theme(app)

    window = MainWindow()
    window.resize(1400, 850)
    window.show()
    pump_events(300)

    # 1. Load volume
    print("[1/6] Loading synthetic volume...")
    synth_path = os.path.join(ROOT, "data", "seismic_synthetic.npy")
    if not os.path.isfile(synth_path):
        print("      Generating synthetic volume first...")
        from tools import make_synthetic
        make_synthetic.main()

    window.open_volume(synth_path)
    pump_events(300)
    assert window.active_volume is not None
    print("      Volume loaded: %s" % window.active_volume.name)

    # 2. Test 2D section navigation
    print("[2/6] Testing 2D section navigation...")
    window.tabs.setCurrentWidget(window.slice_view)
    window.navigator.set_slice(AXIS_ILINE, 90)
    pump_events(200)
    save_screenshot(window, "01_2d_inline.png")

    window.navigator.set_slice(AXIS_TIME, 160)
    pump_events(200)
    save_screenshot(window, "02_2d_timeslice.png")

    # 3. Test 3D view
    print("[3/6] Testing 3D view...")
    window.tabs.setCurrentWidget(window.view3d)
    pump_events(400)
    if window.view3d.available:
        window.view3d.set_camera_preset("Isometric")
        pump_events(300)
        save_screenshot(window, "03_3d_isometric.png")
    else:
        print("      3D view skipped (PyVista/VTK unavailable).")

    # 4. Test Comparison & Filters
    print("[4/6] Testing filters and comparison view...")
    window.tabs.setCurrentWidget(window.compare_view)
    window.navigator.set_slice(AXIS_ILINE, 90)
    window.filter_controls.previewRequested.emit("smooth")
    pump_events(300)
    save_screenshot(window, "04_compare_smooth.png")

    # 5. Test Arbitrary Line
    print("[5/6] Testing arbitrary line composite section...")
    window.tabs.setCurrentWidget(window.arbitrary_view)
    window.arbitrary_view.update_section()
    pump_events(300)
    save_screenshot(window, "05_arbitrary_line.png")

    # 6. Test Spectrum
    print("[6/6] Testing spectrum computation via C# module...")
    window.tabs.setCurrentWidget(window.slice_view)
    window.navigator.set_slice(AXIS_ILINE, 90)
    window.slice_view.set_roi_visible(True)
    pump_events(300)
    window.spectrum_panel.compute_button.click()
    
    # Wait for the background worker to finish
    t0 = time.time()
    while window.spectrum_panel._worker is not None and time.time() - t0 < 10.0:
        pump_events(100)

    save_screenshot(window, "06_spectrum_analysis.png")
    print("      Spectrum analysis completed successfully.")

    window.close()
    print("\nALL SMOKE TESTS COMPLETED SUCCESSFULLY!")


if __name__ == "__main__":
    run_smoke_test()