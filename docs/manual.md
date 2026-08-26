# User manual — Seismic Volume Explorer

---

## 1. Starting the application

Double-click **`run.bat`**, or from a command prompt in the project folder:

```bat
run.bat
```

The first run creates a virtual environment, installs the Python packages and
builds the C# spectrum module. Subsequent runs start immediately.

To open a volume straight away:

```bat
run.bat data\seismic_synthetic.npy
```

If you do not have a volume yet:

```bat
.venv\Scripts\python.exe tools\make_synthetic.py
```

writes a 180 × 160 × 320 test cube (35 MB) containing dipping and folded
reflectors, a normal fault and a meandering channel.

---

## 2. The window

```
┌───────────┬────────────────────────────────────────────┬───────────┐
│ Volumes   │  2D Section │ 3D View │ Compare │ Arbitrary │  Filters  │
│           │                                            │           │
│ Slice     │            (the active view)               │           │
│ navigation│                                            │           │
│           │                                            │           │
│ Display   │                                            │           │
├───────────┴────────────────────────────────────────────┴───────────┤
│  Spectrum  (external C# module)                                    │
└────────────────────────────────────────────────────────────────────┘
```

Every panel is a dock: drag its title bar to move or float it, close it with
the ×, bring it back from the **View** menu.

---

## 3. Loading data

| Action | How |
|---|---|
| Open a volume | **File ▸ Open volume…** (Ctrl+O), or drag a `.npy` file onto the window |
| Open the bundled test cube | **File ▸ Open sample volume** |
| Load a second volume for comparison | Open it the same way; both appear in **Volumes** |
| Switch the active volume | Click it in the **Volumes** list |
| Remove one | Select it, then **Remove** |
| Save the active volume | **File ▸ Save active volume as…** |

The **Volumes** panel shows the shape, size, dtype, sample interval and the
p99 amplitude range of whichever cube is selected.

The array must be 3D and indexed `(iline, xline, time)`.

---

## 4. 2D sections

**Slice navigation** dock:

* **Direction** — Inline, Crossline or Time.
* **Slider / spin box** — the slice index; `<` and `>` step by one.
* The label underneath shows the survey number (`IL 91`) and the index.

In the **2D Section** tab:

| Interaction | Result |
|---|---|
| Mouse wheel | zoom about the cursor |
| Left drag | pan |
| **Fit** | zoom back to the whole section |
| Right-click | pyqtgraph menu: export image/CSV, axis options |
| Move the cursor | the line under the plot reads out position and amplitude |
| Left click | pins the crosshair at that point |

---

## 5. Colours and amplitude

The **Display** dock drives every 2D and 3D view at once:

* **Colormap** — `seismic`, `gray`, `RdBu_r`, `bwr`, `PuOr`, `viridis`,
  `magma`, `petrel`; **Reverse** flips any of them.
* **Auto clip per slice** — recompute the amplitude window each time the slice
  changes (on by default).
* **Clip percentile** — how hard that auto clip is. 99 is a normal display;
  lower values (95–97) make weak events stand out.
* **Min / Max amplitude** — type exact limits; this turns auto clip off.
* **Make symmetric** — centre the window on zero, so a divergent colormap has
  white exactly at zero amplitude.
* **Rescale now** — re-enable auto clip and apply it immediately.

**The colour bar beside each section is interactive**: drag either end to
stretch the window, drag the middle to shift it. The Display dock follows.

---

## 6. 3D view

The scene opens with one inline, one crossline and one time slice through the
middle of the cube.

| Control | Effect |
|---|---|
| **+ IL / + XL / + Time** | add another slice plane |
| The list | every plane; untick to hide, select to move |
| **Remove selected** | delete the highlighted plane |
| **Move selected slice** slider / **Index** | slide the plane through the cube |
| **Drag handle in 3D** | attach a VTK plane widget so the plane can be dragged directly in the scene |
| **Survey outline**, **Axes and bounds** | show or hide the box and the labelled axes |
| **View** | Isometric, Inline, Crossline or Map, always with time downwards |
| **Reset camera** | back to the chosen preset |

Navigation: left drag rotates, wheel zooms, middle drag pans, right drag
dollies.

**View ▸ Sync 3D slices with the 2D navigator** (on by default) keeps the 3D
planes and the 2D section on the same slice — move one and the other follows.

---

## 7. Smoothing and sharpening

The **Filters** dock has two independent groups.

**Gaussian smoothing** — separate sigmas across traces and along time, both in
samples. Setting one to 0 smooths in the other direction only.

**Image sharpening**
* `unsharp` — subtract a blurred copy and add the difference back;
  **Sigma** sets the blur radius, **Amount** how much detail returns.
* `laplacian` — add back a discrete second derivative; **Amount** scales it,
  Sigma is unused.

Each group offers:

* **Preview on slice** — filter only the section on screen and show it beside
  the original in the **Compare / Sync** tab. Nothing is modified.
* **Apply to volume** — filter the whole cube, with a progress bar and a
  Cancel button, and add the result to **Volumes** as a new entry
  (`name [gauss 2.0/2.0]`). The original stays untouched.

**Live preview while editing** recomputes the preview as soon as a parameter
changes.

---

## 8. Comparing and synchronising two views

The **Compare / Sync** tab shows the same slice twice.

* **Compare against** — a filter preview of the current slice, or any other
  loaded volume.
* **Link zoom and pan** — the two panels share their view; zooming one zooms
  the other.
* **Show difference** — the right panel becomes *comparison − reference*,
  auto-scaled to its own (much smaller) amplitudes.
* Moving the cursor over one panel **mirrors the crosshair into the other**.
  Clicking pins it in both and reads out the value in each plus the difference.
* The line under the panels reports RMS of the reference, RMS of the
  difference (absolute and as a percentage), the largest absolute difference
  and the correlation coefficient.

The typical workflow: load a cube, set the smoothing sigmas, press
**Preview on slice**, and compare. Then **Apply to volume** and pick the new
cube in **Compare against** to check the whole survey.

---

## 9. Spectrum analysis (external C# module)

The spectrum is computed by `csharp/bin/SpectrumService.exe`, a separate
process. The Python application only sends data and plots the answer; see
[protocol.md](protocol.md) for the wire format.

**Selecting the region of interest**

1. Tick **ROI** in the 2D Section toolbar (or in the Compare toolbar, where it
   applies to both panels at once).
2. A yellow rectangle appears over the middle of the section. Drag it to move,
   drag a corner handle to resize.
3. With **Auto update on ROI change** ticked, every adjustment recomputes the
   spectrum; otherwise press **Compute spectrum**.

If ROI is off, the whole section is used.

**Options**

* **Taper** — None, Hann (default) or Hamming, applied to each trace before
  the FFT.
* **dB scale** — plot 20·log₁₀ relative to each curve's peak.
* **Normalise** — scale each curve so its peak is 1, which makes shape
  differences obvious when amplitudes differ.

**Reading the result**

The dashed orange line marks the dominant frequency of the first curve. Under
the plot, each curve is summarised as *peak frequency*, *spectral centroid*
and *−6 dB bandwidth*.

On the **Compare / Sync** tab both panels are sent, so the original and the
filtered version appear as two curves on one plot — smoothing pulls the high
end down, sharpening lifts it.

> A **time slice has no time axis**, so a frequency spectrum is not defined for
> it. The panel says so instead of drawing a meaningless curve; switch the
> navigator to Inline or Crossline.

**Tools menu**
* **Restart spectrum module** — stop and relaunch the child process.
* **Rebuild C# spectrum module** — recompile `SpectrumService.cs`.

The status line under the controls always shows whether the module is running,
with its process id and port.

---

## 10. Arbitrary line (optional feature)

The **Arbitrary Line** tab extracts a composite section along a traverse.

1. The left panel is a map — one time slice through the cube. Use **Map time
   slice** to pick a level where the geology is clear.
2. A yellow polyline crosses it. Drag a handle to move a bend; click on a
   segment to add one.
3. The right panel shows the resulting section, interpolated bilinearly
   between bins.

**Live update** recomputes while dragging; turn it off on very large cubes and
use **Extract now**. **Reset line** restores the default diagonal traverse.

The composite section supports ROI and spectrum analysis exactly like a normal
section.

---

## 11. Exporting

* **File ▸ Export current view as image…** — PNG of the active tab. The 3D tab
  is captured through VTK, so the render is included.
* Right-click any 2D plot ▸ **Export…** — pyqtgraph's own exporter (PNG, SVG,
  CSV of the plotted data).
* **File ▸ Save active volume as…** — writes the active cube, including one
  produced by **Apply to volume**, as `.npy`.

---

## 12. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| 3D tab shows a message instead of a scene | VTK could not get an OpenGL context, usually a remote desktop or a stale graphics driver. The rest of the application is unaffected. |
| "Spectrum module not found" | Run `csharp\build.bat`, or **Tools ▸ Rebuild C# spectrum module**. |
| "The spectrum module did not report a port" | A firewall is blocking loopback connections, or the exe is blocked by SmartScreen. Unblock `csharp\bin\SpectrumService.exe` in its file properties. |
| Spectrum panel says a time slice has no time axis | Expected — switch to Inline or Crossline. |
| Opening a volume fails with "a 3D cube is required" | The `.npy` file is not 3D. This tool reads post-stack `(il, xl, t)` cubes only. |
| Filtering a large cube is slow | It runs on a background thread with a Cancel button; use **Preview on slice** to choose parameters first, then apply once. |
