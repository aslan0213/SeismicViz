# Seismic Volume Explorer

A desktop application for loading, visualising and analysing 3D post-stack
seismic volumes, with the spectrum analysis implemented as an **independent C#
module** that the main application talks to over a loopback socket.

Python 3.10+ / PyQt6 / pyqtgraph / PyVista (VTK) / SciPy · C# 5 (.NET Framework)

---

## Quick start

```bat
:: 1. create the environment
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt

:: 2. build the C# spectrum module (optional - the app builds it on demand)
csharp\build.bat

:: 3. make a test cube if you do not have one
.venv\Scripts\python.exe tools\make_synthetic.py

:: 4. run
run.bat
```

or, in one step, `run.bat` — on first start it creates the virtual environment,
installs the requirements, builds the C# module and generates a synthetic test
volume, then launches the application.

**No seismic volume ships with this package.** Point the application at your own
`(iline, xline, time)` `.npy` cube, or use the synthetic one the first run
produces; see `data/README.txt`.

Open a volume with **File ▸ Open volume…**, drag a `.npy` file onto the window,
or pass it on the command line:

```bat
.venv\Scripts\python.exe -m app.main data\seismic_synthetic.npy
```

---

## What it does

| # | Requirement | Where |
|---|---|---|
| 1 | Load a 3D seismic `.npy` volume | `app/core/volume.py`, File ▸ Open |
| 2 | 2D slices along inline / crossline | **2D Section** tab |
| 2b | *(optional)* arbitrary / composite line | **Arbitrary Line** tab |
| 3 | Interactive 3D visualisation, add and move slices | **3D View** tab |
| 4 | Zoom, pan and colour-bar control in 2D and 3D | every view; **Display** dock |
| 5 | Gaussian smoothing and image sharpening of a slice | **Filters** dock |
| 6 | Compare two volumes, synchronised 2D views | **Compare / Sync** tab |
| 7 | Spectrum analysis in a separate C# module, ROI selectable | **Spectrum** dock, `csharp/` |

Time slices (`il × xl`) are supported as a third orientation, which makes the
channel and the fault in the sample cube easy to see.

---

## Layout

```
SeismicViz/
├── app/
│   ├── main.py                  entry point, dark theme
│   ├── core/
│   │   ├── volume.py            SeismicVolume: load, slice, arbitrary line
│   │   ├── filters.py           Gaussian smoothing, sharpening, difference stats
│   │   └── spectrum_client.py   launches and drives the C# module
│   └── ui/
│       ├── main_window.py       menus, docks, tabs, all the wiring
│       ├── slice_view.py        2D section: zoom, pan, colour bar, crosshair, ROI
│       ├── view3d.py            PyVista scene with movable slice planes
│       ├── compare_view.py      two synchronised sections
│       ├── arbitrary_view.py    map + composite section
│       ├── spectrum_panel.py    spectrum plot, worker thread
│       ├── panels.py            navigation / display / filter / volume docks
│       └── display.py           shared colormap and amplitude window
├── csharp/
│   ├── SpectrumService.cs       TCP server, hand-written radix-2 FFT
│   └── build.bat                builds with the in-box csc.exe
├── docs/
│   ├── protocol.md              IPC method and data exchange format
│   ├── manual.md                user manual
│   └── screenshots/
├── tools/
│   ├── make_synthetic.py        generates a realistic test cube
│   ├── test_spectrum_ipc.py     C# module vs numpy.fft
│   └── smoke_test.py            drives the whole GUI and saves screenshots
├── data/                        .npy volumes
├── requirements.txt
└── run.bat
```

---

## Data format

A volume is a 3D `.npy` array indexed `(iline, xline, time)` — the first axis
is the inline (x) direction, the second the crossline (y), the third time.
Integer and floating-point dtypes are both accepted; sections are converted to
`float32` on extraction. Files over 512 MB are memory mapped automatically.

The sample interval defaults to 4 ms (`Geometry.dt`), which sets the frequency
axis of the spectrum and the time labels on every view.

---

## Verifying it works

```bat
:: C# module in isolation: 25 Hz sine must peak in the 25 Hz bin
csharp\bin\SpectrumService.exe --selftest

:: Python <-> C# round trip, checked against numpy.fft
.venv\Scripts\python.exe tools\test_spectrum_ipc.py

:: the whole GUI, end to end, with screenshots into docs/screenshots
.venv\Scripts\python.exe tools\smoke_test.py
```

---

## Notes and limitations

* A **time slice has no time axis**, so the spectrum is undefined for it; the
  panel says so rather than returning a meaningless curve. Switch the navigator
  to Inline or Crossline.
* "Apply to volume" filters section by section along the inline axis (2D
  filtering per inline), not with a 3D kernel — that is the behaviour wanted
  when the goal is to clean up displayed sections rather than smear structure
  across the survey.
* The 3D scene uses normalised world units so that a cube of any proportions
  looks sensible; the axis ticks are relabelled with the real survey numbers
  and two-way time.
* If VTK cannot get an OpenGL context, the 3D tab shows the reason and the rest
  of the application keeps working.
