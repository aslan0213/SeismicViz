"""Shared display configuration: colormaps, amplitude windows, sync."""

from __future__ import annotations

from typing import TYPE_CHECKING

import matplotlib
import matplotlib.cm as cm
import matplotlib.colors as mcolors
import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QObject, pyqtSignal

if TYPE_CHECKING:
    from PyQt6.QtWidgets import QWidget

COLORMAPS = ["seismic", "petrel", "gray", "bwr", "coolwarm", "viridis", "inferno"]

_PETREL_SEGMENTS = {
    "red": [(0.0, 0.0, 0.0), (0.35, 0.15, 0.15), (0.5, 1.0, 1.0), (0.65, 0.95, 0.95), (1.0, 0.8, 0.8)],
    "green": [(0.0, 0.2, 0.2), (0.35, 0.8, 0.8), (0.5, 1.0, 1.0), (0.65, 0.75, 0.75), (1.0, 0.1, 0.1)],
    "blue": [(0.0, 0.7, 0.7), (0.35, 0.95, 0.95), (0.5, 1.0, 1.0), (0.65, 0.15, 0.15), (1.0, 0.1, 0.1)],
}

try:
    matplotlib.colormaps.register(
        name="petrel",
        cmap=mcolors.LinearSegmentedColormap("petrel", _PETREL_SEGMENTS, N=256),
    )
except ValueError:
    pass  # Already registered if imported multiple times.

# Cache built lookup tables so toggling between colormaps is instant.
_LUT_CACHE: dict[tuple[str, bool], np.ndarray] = {}


def lookup_table(name: str, reverse: bool = False, n: int = 512) -> np.ndarray:
    """A 512-entry RGBA LUT for pyqtgraph's ImageItem."""
    key = (name, reverse)
    lut = _LUT_CACHE.get(key)
    if lut is not None:
        return lut

    cmap_name = name + "_r" if reverse and not name.endswith("_r") else name
    try:
        cmap = matplotlib.colormaps.get_cmap(cmap_name)
    except ValueError:
        cmap = matplotlib.colormaps.get_cmap("seismic")

    indices = np.linspace(0.0, 1.0, n)
    rgba = (cmap(indices) * 255.0).astype(np.uint8)
    _LUT_CACHE[key] = rgba
    return rgba


def color_map(name: str, reverse: bool = False) -> pg.ColorMap:
    """Build a :class:`pg.ColorMap` backed by the matplotlib definition."""
    lut = lookup_table(name, reverse=reverse, n=256)
    pos = np.linspace(0.0, 1.0, len(lut))
    return pg.ColorMap(pos, lut)


def vtk_colormap_name(name: str) -> str:
    """Map a UI colormap name to a preset that PyVista/VTK understands."""
    table = {
        "seismic": "bwr",
        "petrel": "coolwarm",
        "gray": "gray",
        "bwr": "bwr",
        "coolwarm": "coolwarm",
        "viridis": "viridis",
        "inferno": "inferno",
    }
    return table.get(name, "bwr")


class DisplaySettings(QObject):
    """Observable display parameters shared across all views."""

    #: Fired whenever any visual parameter changes.
    changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cmap = "seismic"
        self._reverse_cmap = False
        self._vmin: float = -1.0
        self._vmax: float = 1.0
        self._percentile: float = 99.0
        self._auto_levels: bool = True
        self._muted: bool = False

    # -------------------------------------------------------------- properties

    @property
    def cmap(self) -> str:
        return self._cmap

    @property
    def reverse_cmap(self) -> bool:
        return self._reverse_cmap

    @property
    def levels(self) -> tuple[float, float]:
        return (self._vmin, self._vmax)

    @property
    def percentile(self) -> float:
        return self._percentile

    @property
    def auto_levels(self) -> bool:
        return self._auto_levels

    # --------------------------------------------------------------- mutators

    def set_cmap(self, name: str, reverse: bool | None = None) -> None:
        dirty = False
        if name in COLORMAPS and name != self._cmap:
            self._cmap = name
            dirty = True
        if reverse is not None and reverse != self._reverse_cmap:
            self._reverse_cmap = reverse
            dirty = True
        if dirty:
            self._notify()

    def set_levels(self, vmin: float, vmax: float, from_user: bool = False) -> None:
        if vmin >= vmax:
            vmax = vmin + 1e-4
        if (vmin, vmax) == (self._vmin, self._vmax):
            return
        self._vmin = float(vmin)
        self._vmax = float(vmax)
        if from_user:
            self._auto_levels = False
        self._notify()

    def set_percentile(self, p: float) -> None:
        p = float(np.clip(p, 50.0, 100.0))
        if p != self._percentile:
            self._percentile = p
            self._notify()

    def set_auto_levels(self, auto: bool) -> None:
        if auto != self._auto_levels:
            self._auto_levels = auto
            self._notify()

    def autoscale_to(self, data: np.ndarray | None) -> None:
        """Update levels from data if auto_levels is enabled."""
        if not self._auto_levels or data is None or data.size == 0:
            return
        finite = data[np.isfinite(data)]
        if finite.size == 0:
            return
        lim = float(np.percentile(np.abs(finite), self._percentile))
        if lim <= 0.0:
            lim = float(np.abs(finite).max()) or 1.0
        self.set_levels(-lim, lim)

    def lut(self) -> np.ndarray:
        """The 512-entry RGBA LUT for current colormap settings."""
        return lookup_table(self._cmap, self._reverse_cmap)

    def pg_colormap(self) -> pg.ColorMap:
        return color_map(self._cmap, self._reverse_cmap)

    def copy_from(self, other: DisplaySettings) -> None:
        """Mirror another settings object without rebinding signals."""
        self._cmap = other._cmap
        self._reverse_cmap = other._reverse_cmap
        self._vmin = other._vmin
        self._vmax = other._vmax
        self._percentile = other._percentile
        self._auto_levels = other._auto_levels
        self._notify()

    def _notify(self) -> None:
        if not self._muted:
            self.changed.emit()