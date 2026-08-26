"""Render the architecture and protocol diagrams used in the presentation."""

from __future__ import annotations

import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "diagrams")

BG = "#0e1420"
PANEL = "#1b2436"
PANEL2 = "#232f46"
GROUP = "#141b29"
TEXT = "#e8ecf4"
MUTED = "#93a0b8"
ACCENT = "#4f9dff"
ACCENT2 = "#ff8a3d"
GREEN = "#40c463"


def new_axes(width: float, height: float):
    """Canvas 10 units wide, with y scaled to keep the aspect square."""
    fig = plt.figure(figsize=(width, height), facecolor=BG)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_facecolor(BG)
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 10 * height / width)
    ax.axis("off")
    return fig, ax, 10 * height / width


def group(ax, x, y, w, h, title, colour):
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h,
        boxstyle="round,pad=0.03,rounding_size=0.15",
        facecolor=GROUP, edgecolor=colour, linewidth=1.4, alpha=0.95))
    ax.text(x + 0.28, y + h - 0.32, title, color=colour,
            fontsize=10.5, fontweight="bold", va="center")


def box(ax, x, y, w, h, label, sub=None, face=PANEL, edge=ACCENT, fontsize=10.5):
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h,
        boxstyle="round,pad=0.02,rounding_size=0.10",
        facecolor=face, edgecolor=edge, linewidth=1.5))
    if sub:
        ax.text(x + w / 2, y + h * 0.63, label, ha="center", va="center",
                color=TEXT, fontsize=fontsize, fontweight="bold")
        ax.text(x + w / 2, y + h * 0.28, sub, ha="center", va="center",
                color=MUTED, fontsize=fontsize - 2.5)
    else:
        ax.text(x + w / 2, y + h / 2, label, ha="center", va="center",
                color=TEXT, fontsize=fontsize, fontweight="bold")


def arrow(ax, start, end, colour=ACCENT):
    ax.add_patch(FancyArrowPatch(
        start, end, arrowstyle="-|>", mutation_scale=15,
        color=colour, linewidth=1.8, shrinkA=0, shrinkB=0))


# ---------------------------------------------------------------------------


def architecture() -> str:
    fig, ax, top = new_axes(12.0, 6.4)          # top = 5.333

    ax.text(0.28, top - 0.38, "Application architecture",
            color=TEXT, fontsize=16, fontweight="bold", va="center")

    py_x, py_w = 0.25, 6.25
    cs_x, cs_w = 7.55, 2.20
    body_y, body_h = 0.30, top - 1.10           # 0.30 .. 4.53

    group(ax, py_x, body_y, py_w, body_h, "Python process   (PyQt6)", ACCENT)
    group(ax, cs_x, body_y, cs_w, body_h, "C# process", GREEN)

    # --- Python side -------------------------------------------------------
    box(ax, py_x + 0.30, 3.45, py_w - 0.60, 0.62, "MainWindow",
        "menus  ·  docks  ·  tabs  ·  wiring", face=PANEL2)

    col_w, gap = 1.75, 0.20
    col_x = [py_x + 0.30 + i * (col_w + gap) for i in range(3)]

    for x, (name, sub) in zip(col_x, [
        ("SliceView", "2D · zoom · ROI"),
        ("Volume3DView", "PyVista / VTK"),
        ("CompareView", "sync 2 panels"),
    ]):
        box(ax, x, 2.35, col_w, 0.78, name, sub, fontsize=9.5)

    for i, (x, (name, sub)) in enumerate(zip(col_x, [
        ("SeismicVolume", "load · slice"),
        ("filters", "smooth · sharpen"),
        ("SpectrumClient", "IPC driver"),
    ])):
        box(ax, x, 1.25, col_w, 0.78, name, sub,
            edge=GREEN if i == 2 else ACCENT, fontsize=9.5)

    for x in col_x[:2]:
        arrow(ax, (x + col_w / 2, 2.35), (x + col_w / 2, 2.03), colour="#5a6d8c")
    arrow(ax, (col_x[2] + col_w / 2, 2.35), (col_x[2] + col_w / 2, 2.03), colour="#5a6d8c")

    ax.text(py_x + 0.30, 0.72, "numpy  ·  scipy.ndimage  ·  pyqtgraph  ·  PyVista / VTK",
            color=MUTED, fontsize=9, va="center")

    # --- C# side -----------------------------------------------------------
    box(ax, cs_x + 0.20, 3.30, cs_w - 0.40, 0.80, "SpectrumService", ".exe",
        face=PANEL2, edge=GREEN, fontsize=10)
    box(ax, cs_x + 0.20, 2.15, cs_w - 0.40, 0.78, "TcpListener", "127.0.0.1 : auto",
        edge=GREEN, fontsize=9.5)
    box(ax, cs_x + 0.20, 1.00, cs_w - 0.40, 0.78, "radix-2 FFT", "no dependencies",
        edge=GREEN, fontsize=9.5)

    arrow(ax, (cs_x + cs_w / 2, 3.30), (cs_x + cs_w / 2, 2.98), colour="#4a7a4a")
    arrow(ax, (cs_x + cs_w / 2, 2.15), (cs_x + cs_w / 2, 1.83), colour="#4a7a4a")

    # --- the link ----------------------------------------------------------
    left, right = py_x + py_w + 0.05, cs_x - 0.05          # 6.55 .. 7.50
    arrow(ax, (left, 1.85), (right, 1.85), colour=ACCENT2)
    arrow(ax, (right, 1.45), (left, 1.45), colour=ACCENT2)
    ax.text((left + right) / 2, 2.06, "ROI, float32", ha="center",
            color=ACCENT2, fontsize=8.5)
    ax.text((left + right) / 2, 1.24, "spectrum", ha="center",
            color=ACCENT2, fontsize=8.5)
    ax.text((left + right) / 2, 0.72, "TCP loopback\nbinary, little-endian",
            ha="center", va="center", color=MUTED, fontsize=8.5)

    path = os.path.join(OUT, "architecture.png")
    fig.savefig(path, dpi=190, facecolor=BG, metadata={"Software": None})
    plt.close(fig)
    return path


def protocol() -> str:
    fig, ax, top = new_axes(12.0, 6.6)          # top = 5.5

    ax.text(0.28, top - 0.38, "Request / response format",
            color=TEXT, fontsize=16, fontweight="bold", va="center")

    row_h = 0.42

    def column(x, w, y_top, rows, title, colour):
        ax.text(x, y_top + 0.26, title, color=colour, fontsize=10.5, fontweight="bold")
        for i, (kind, name) in enumerate(rows):
            y = y_top - (i + 1) * row_h
            ax.add_patch(FancyBboxPatch(
                (x, y), w, row_h * 0.84,
                boxstyle="round,pad=0.01,rounding_size=0.04",
                facecolor=PANEL if i % 2 == 0 else PANEL2,
                edgecolor=colour, linewidth=1.0))
            ax.text(x + 0.14, y + row_h * 0.40, kind, color=MUTED, fontsize=8.5,
                    va="center", family="monospace")
            ax.text(x + w - 0.14, y + row_h * 0.40, name, color=TEXT, fontsize=8.5,
                    va="center", ha="right")

    y_top = top - 1.05
    column(0.35, 3.85, y_top, [
        ('char[4]', '"SPEC"'),
        ("int32", "version = 1"),
        ("int32", "nTraces"),
        ("int32", "nSamples"),
        ("float64", "dt  (seconds)"),
        ("int32", "window  0 / 1 / 2"),
        ("float32[]", "nTraces x nSamples"),
    ], "REQUEST      Python → C#", ACCENT)

    column(5.80, 3.85, y_top, [
        ('char[4]', '"SPCR"'),
        ("int32", "status = 0"),
        ("int32", "nFreq = nfft/2 + 1"),
        ("float64", "df  (Hz per bin)"),
        ("float32[]", "average amplitude"),
    ], "RESPONSE      C# → Python", GREEN)

    mid = (0.35 + 3.85 + 5.80) / 2
    arrow(ax, (4.35, y_top - 2.1), (5.72, y_top - 2.1), colour=ACCENT2)
    arrow(ax, (5.72, y_top - 2.7), (4.35, y_top - 2.7), colour=ACCENT2)
    ax.text(mid, y_top - 1.80, "28 B header\n+ payload", ha="center", va="center",
            color=MUTED, fontsize=8.5)
    ax.text(mid, y_top - 3.05, "~21 ms for\n1.5 MB", ha="center", va="center",
            color=MUTED, fontsize=8.5)

    ax.text(0.35, 0.52,
            'Little-endian, no padding.  Handshake: the module prints "PORT <n>" '
            'then "READY" on stdout; the client parses\nthe port and connects to '
            "127.0.0.1.  On failure the response carries status ≠ 0 and a "
            "UTF-8 message.",
            color=MUTED, fontsize=9, va="center")

    path = os.path.join(OUT, "protocol.png")
    fig.savefig(path, dpi=190, facecolor=BG, metadata={"Software": None})
    plt.close(fig)
    return path


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    for render in (architecture, protocol):
        print("wrote", os.path.relpath(render(), ROOT))


if __name__ == "__main__":
    main()