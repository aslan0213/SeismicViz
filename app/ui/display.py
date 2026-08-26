"""Shared colour / amplitude settings for every 2D and 3D view.

A single :class:`DisplaySettings` instance is handed to all the views that
should stay visually consistent, so changing a colormap or a clip level in one
place updates the whole window - and the side-by-side comparison keeps both
panels on identical scales, which is the only way a difference is meaningful.
"""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from matplotlib import colormaps as mpl_colormaps
from matplotlib.colors import LinearSegmentedColormap
from PyQt6.QtCore import QObject, pyqtSignal

# Colormaps offered in the UI. The first two are the conventional choices for
# post-stack amplitude displays; the rest are useful for attribute-style looks.
COLORMAPS: list[str] = [
    "seismic",
    "gray",
    "RdBu_r",
    "bwr",
    "PuOr",
    "viridis",
    "magma",
    "petrel",
]

# "petrel": the blue-white-red ramp used by most interpretation packages, with
# a slightly compressed white band so weak amplitudes stay visible.
_PETREL = LinearSegmentedColormap.from_list(
    "petrel",
    [
        (0.00, "#00204a"),
        (0.25, "#1f6fd0"),
        (0.46, "#eef3fa"),
        (0.50, "#ffffff"),
        (0.54, "#faeeee"),
        (0.75, "#d3341f"),
        (1.00, "#4a0a00"),
    ],
)

_LUT_CACHE: dict[tuple[str, bool], np.ndarray] = {}


def _matplotlib_cmap(name: str):
    if name == "petrel":
        return _PETREL
    try:
        return mpl_colormaps[name]
    except KeyError:
        return mpl_colormaps["seismic"]


def lookup_table(name: str, reverse: bool = False, size: int = 512) -> np.ndarray:
    """RGBA lookup table for pyqtgraph's ImageItem."""
    key = (name, reverse)
    cached = _LUT_CACHE.get(key)
    if cached is not None and cached.shape[0] == size:
        return cached

    cmap = _matplotlib_cmap(name)
    positions = np.linspace(1.0, 0.0, size) if reverse else np.linspace(0.0, 1.0, size)
    lut = (np.asarray(cmap(positions)) * 255.0).round().astype(np.ubyte)
    _LUT_CACHE[key] = lut
    return lut


def color_map(name: str, reverse: bool = False) -> pg.ColorMap:
    """pyqtgraph ColorMap, used by the interactive colour bars."""
    lut = lookup_table(name, reverse, size=256)
    return pg.ColorMap(np.linspace(0.0, 1.0, lut.shape[0]), lut)


def vtk_colormap_name(name: str, reverse: bool) -> str:
    """Name PyVista understands. It accepts matplotlib names directly."""
    base = "seismic" if name == "petrel" else name
    return base + "_r" if reverse else base


class DisplaySettings(QObject):
    """Colormap and amplitude window shared by a group of views."""

    changed = pyqtSignal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._cmap = "seismic"
        self._reverse = False
        self._vmin = -1.0
        self._vmax = 1.0
        self._percentile = 99.0
        self._auto = True
        self._muted = False

    # ---------------------------------------------------------------- getters

    @property
    def cmap(self) -> str:
        return self._cmap

    @property
    def reverse(self) -> bool:
        return self._reverse

    @property
    def levels(self) -> tuple[float, float]:
        return (self._vmin, self._vmax)

    @property
    def percentile(self) -> float:
        return self._percentile

    @property
    def auto_levels(self) -> bool:
        return self._auto

    def lut(self) -> np.ndarray:
        return lookup_table(self._cmap, self._reverse)

    def pg_colormap(self) -> pg.ColorMap:
        return color_map(self._cmap, self._reverse)

    # ---------------------------------------------------------------- setters

    def set_cmap(self, name: str, reverse: bool | None = None) -> None:
        changed = name != self._cmap
        if reverse is not None and reverse != self._reverse:
            self._reverse = reverse
            changed = True
        self._cmap = name
        if changed:
            self._emit()

    def set_reverse(self, reverse: bool) -> None:
        if reverse != self._reverse:
            self._reverse = reverse
            self._emit()

    def set_levels(self, vmin: float, vmax: float, from_user: bool = True) -> None:
        """Set the amplitude window. A user edit turns auto-scaling off."""
        if vmax <= vmin:
            vmax = vmin + 1e-6
        if (vmin, vmax) == (self._vmin, self._vmax):
            return
        self._vmin, self._vmax = float(vmin), float(vmax)
        if from_user:
            self._auto = False
        self._emit()

    def set_percentile(self, value: float) -> None:
        value = float(np.clip(value, 50.0, 100.0))
        if value != self._percentile:
            self._percentile = value
            self._emit()

    def set_auto_levels(self, auto: bool) -> None:
        if auto != self._auto:
            self._auto = auto
            self._emit()

    def autoscale_to(self, data: np.ndarray) -> None:
        """Recompute a symmetric window from the data, if auto-scaling is on."""
        if not self._auto:
            return
        finite = np.asarray(data, dtype=np.float32).ravel()
        finite = finite[np.isfinite(finite)]
        if finite.size == 0:
            return
        level = float(np.percentile(np.abs(finite), self._percentile))
        if level <= 0.0:
            level = float(np.abs(finite).max()) or 1.0
        self.set_levels(-level, level, from_user=False)

    def copy_from(self, other: "DisplaySettings") -> None:
        """Adopt another group's look, e.g. to keep two panels comparable."""
        self._muted = True
        try:
            self.set_cmap(other.cmap, other.reverse)
            self.set_percentile(other.percentile)
            self.set_auto_levels(other.auto_levels)
            self.set_levels(*other.levels, from_user=False)
        finally:
            self._muted = False
        self.changed.emit()

    # ---------------------------------------------------------------- helpers

    def mute(self, muted: bool) -> None:
        """Suppress ``changed`` while several properties are being set."""
        self._muted = muted
        if not muted:
            self.changed.emit()

    def _emit(self) -> None:
        if not self._muted:
            self.changed.emit()
