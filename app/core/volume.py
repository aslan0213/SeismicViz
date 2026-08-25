from __future__ import annotations

import os
from dataclasses import dataclass, field, replace

import numpy as np
from scipy import ndimage

# Axis identifiers used throughout the application.
AXIS_ILINE = 0
AXIS_XLINE = 1
AXIS_TIME = 2

AXIS_NAMES = {AXIS_ILINE: "Inline", AXIS_XLINE: "Crossline", AXIS_TIME: "Time"}


@dataclass(frozen=True)
class Geometry:
    """Maps array indices onto survey coordinates.

    Only affine mappings are supported, which is all a post-stack cube on a
    regular grid needs.
    """

    il_start: int = 1
    il_step: int = 1
    xl_start: int = 1
    xl_step: int = 1
    t_start: float = 0.0
    dt: float = 0.004  # seconds

    def iline_label(self, index: int) -> int:
        return self.il_start + index * self.il_step

    def xline_label(self, index: int) -> int:
        return self.xl_start + index * self.xl_step

    def time_label(self, index: int) -> float:
        """Time of a sample in milliseconds."""
        return (self.t_start + index * self.dt) * 1000.0

    def axis_label(self, axis: int, index: int) -> str:
        if axis == AXIS_ILINE:
            return "IL %d" % self.iline_label(index)
        if axis == AXIS_XLINE:
            return "XL %d" % self.xline_label(index)
        return "%.0f ms" % self.time_label(index)


@dataclass
class SeismicVolume:
    """A named 3D cube plus its display statistics."""

    data: np.ndarray
    name: str = "volume"
    path: str = ""
    geometry: Geometry = field(default_factory=Geometry)

    # Cached robust amplitude range, filled lazily by :meth:`clip_range`.
    _clip: tuple[float, float] | None = field(default=None, repr=False, compare=False)

    # ------------------------------------------------------------------ shape

    def __post_init__(self) -> None:
        if self.data.ndim != 3:
            raise ValueError(
                "expected a 3D array (iline, xline, time), got shape %r" % (self.data.shape,)
            )
        if self.data.dtype.kind not in "fiu":
            raise ValueError("unsupported dtype %s" % self.data.dtype)

    @property
    def shape(self) -> tuple[int, int, int]:
        return self.data.shape  # type: ignore[return-value]

    @property
    def n_iline(self) -> int:
        return self.data.shape[0]

    @property
    def n_xline(self) -> int:
        return self.data.shape[1]

    @property
    def n_time(self) -> int:
        return self.data.shape[2]

    def axis_size(self, axis: int) -> int:
        return self.data.shape[axis]

    def nbytes_mb(self) -> float:
        return self.data.nbytes / (1024.0 * 1024.0)

    # ------------------------------------------------------------------ I/O

    @classmethod
    def load(cls, path: str, name: str | None = None, mmap: bool | None = None) -> "SeismicVolume":
  
        size_mb = os.path.getsize(path) / (1024.0 * 1024.0)
        if mmap is None:
            mmap = size_mb > 512.0

        array = np.load(path, mmap_mode="r" if mmap else None, allow_pickle=False)
        if array.ndim != 3:
            raise ValueError(
                "%s holds a %dD array; a 3D (iline, xline, time) cube is required"
                % (os.path.basename(path), array.ndim)
            )

        return cls(
            data=array,
            name=name or os.path.splitext(os.path.basename(path))[0],
            path=path,
        )

    def save(self, path: str) -> None:
        np.save(path, np.asarray(self.data))
        self.path = path

    def derived(self, data: np.ndarray, suffix: str) -> "SeismicVolume":
        """A sibling cube with the same geometry, e.g. a filtered copy."""
        return SeismicVolume(
            data=data,
            name="%s [%s]" % (self.name, suffix),
            path="",
            geometry=replace(self.geometry),
        )

    # ------------------------------------------------------------------ slices

    def slice(self, axis: int, index: int) -> np.ndarray:

        index = int(np.clip(index, 0, self.axis_size(axis) - 1))
        if axis == AXIS_ILINE:
            section = self.data[index, :, :]
        elif axis == AXIS_XLINE:
            section = self.data[:, index, :]
        elif axis == AXIS_TIME:
            section = self.data[:, :, index]
        else:
            raise ValueError("unknown axis %r" % axis)
        return np.asarray(section, dtype=np.float32)

    def slice_axis_names(self, axis: int) -> tuple[str, str]:

        if axis == AXIS_ILINE:
            return "Crossline", "Time (ms)"
        if axis == AXIS_XLINE:
            return "Inline", "Time (ms)"
        return "Inline", "Crossline"

    def trace(self, il: int, xl: int) -> np.ndarray:
        il = int(np.clip(il, 0, self.n_iline - 1))
        xl = int(np.clip(xl, 0, self.n_xline - 1))
        return np.asarray(self.data[il, xl, :], dtype=np.float32)

    # --------------------------------------------------------- arbitrary line

    def arbitrary_slice(
        self, waypoints: list[tuple[float, float]], spacing: float = 1.0
    ) -> tuple[np.ndarray, np.ndarray]:

        if len(waypoints) < 2:
            raise ValueError("an arbitrary line needs at least two waypoints")

        pts = np.asarray(waypoints, dtype=np.float64)
        segments = np.diff(pts, axis=0)
        lengths = np.hypot(segments[:, 0], segments[:, 1])
        total = float(lengths.sum())
        if total <= 0.0:
            raise ValueError("the waypoints describe a zero-length line")

        n_samples = max(2, int(round(total / max(spacing, 1e-6))) + 1)
        cumulative = np.concatenate([[0.0], np.cumsum(lengths)])
        wanted = np.linspace(0.0, total, n_samples)

        il_path = np.interp(wanted, cumulative, pts[:, 0])
        xl_path = np.interp(wanted, cumulative, pts[:, 1])

        t_index = np.arange(self.n_time, dtype=np.float64)
        coords = np.empty((3, n_samples, self.n_time), dtype=np.float64)
        coords[0] = il_path[:, None]
        coords[1] = xl_path[:, None]
        coords[2] = t_index[None, :]

        section = ndimage.map_coordinates(
            np.asarray(self.data, dtype=np.float32),
            coords,
            order=1,
            mode="nearest",
        )
        path = np.stack([il_path, xl_path], axis=1)
        return section.astype(np.float32), path

    # ------------------------------------------------------------ statistics

    def clip_range(self, percentile: float = 99.0) -> tuple[float, float]:
  
        if self._clip is not None:
            return self._clip

        sample = self._subsample()
        finite = sample[np.isfinite(sample)]
        if finite.size == 0:
            self._clip = (-1.0, 1.0)
            return self._clip

        level = float(np.percentile(np.abs(finite), percentile))
        if level <= 0.0:
            level = float(np.abs(finite).max()) or 1.0
        self._clip = (-level, level)
        return self._clip

    def _subsample(self, budget: int = 2_000_000) -> np.ndarray:
        """A decimated copy small enough for percentile work."""
        total = int(np.prod(self.shape))
        if total <= budget:
            return np.asarray(self.data, dtype=np.float32).ravel()

        step = max(1, int(round((total / budget) ** (1.0 / 3.0))))
        return np.asarray(self.data[::step, ::step, ::step], dtype=np.float32).ravel()

    def summary(self) -> str:
        lo, hi = self.clip_range()
        return (
            "%s\n"
            "  shape    : %d IL x %d XL x %d samples\n"
            "  size     : %.1f MB (%s)\n"
            "  sampling : %.1f ms\n"
            "  clip     : %+.4g .. %+.4g (p99)"
            % (
                self.name,
                self.n_iline,
                self.n_xline,
                self.n_time,
                self.nbytes_mb(),
                self.data.dtype,
                self.geometry.dt * 1000.0,
                lo,
                hi,
            )
        )