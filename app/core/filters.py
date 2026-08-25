from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
from scipy import ndimage

ProgressFn = Callable[[int], bool]


# ---------------------------------------------------------------------------
#  Parameter objects
# ---------------------------------------------------------------------------


@dataclass
class SmoothParams:
    """Gaussian blur settings."""

    sigma_trace: float = 1.5
    sigma_time: float = 1.5
    truncate: float = 4.0

    def sigmas_2d(self) -> tuple[float, float]:
        return (self.sigma_trace, self.sigma_time)

    def describe(self) -> str:
        return "gauss %.1f/%.1f" % (self.sigma_trace, self.sigma_time)


@dataclass
class SharpenParams:
    """Unsharp-mask settings."""

    sigma: float = 1.0
    amount: float = 1.0
    method: str = "unsharp"  # "unsharp" | "laplacian"
    clip_to_input: bool = False

    def describe(self) -> str:
        if self.method == "laplacian":
            return "laplace x%.2f" % self.amount
        return "unsharp %.1f x%.2f" % (self.sigma, self.amount)


# ---------------------------------------------------------------------------
#  2D operations
# ---------------------------------------------------------------------------


def gaussian_smooth_2d(image: np.ndarray, params: SmoothParams) -> np.ndarray:
    """Blur a ``(n_traces, n_samples)`` section."""
    src = np.asarray(image, dtype=np.float32)
    sigma = params.sigmas_2d()
    if max(sigma) <= 0.0:
        return src.copy()
    return ndimage.gaussian_filter(
        src, sigma=sigma, truncate=params.truncate, mode="nearest"
    ).astype(np.float32)


def sharpen_2d(image: np.ndarray, params: SharpenParams) -> np.ndarray:
    """Boost high frequencies in a ``(n_traces, n_samples)`` section."""
    src = np.asarray(image, dtype=np.float32)
    if params.amount == 0.0:
        return src.copy()

    if params.method == "laplacian":
        # A discrete Laplacian is already a high-pass residual.
        detail = -ndimage.laplace(src, mode="nearest")
    else:
        blurred = ndimage.gaussian_filter(src, sigma=max(params.sigma, 1e-3), mode="nearest")
        detail = src - blurred

    out = src + params.amount * detail
    if params.clip_to_input:
        out = np.clip(out, src.min(), src.max())
    return out.astype(np.float32)


# ---------------------------------------------------------------------------
#  3D operations
# ---------------------------------------------------------------------------


def gaussian_smooth_3d(
    volume: np.ndarray, params: SmoothParams, progress: ProgressFn | None = None
) -> np.ndarray | None:
    """Blur a whole cube, section by section."""
    src = np.asarray(volume, dtype=np.float32)
    out = np.empty_like(src)
    n = src.shape[0]

    for i in range(n):
        out[i] = gaussian_smooth_2d(src[i], params)
        if progress is not None and not progress(int((i + 1) * 100 / n)):
            return None
    return out


def sharpen_3d(
    volume: np.ndarray, params: SharpenParams, progress: ProgressFn | None = None
) -> np.ndarray | None:
    """Sharpen a whole cube, section by section."""
    src = np.asarray(volume, dtype=np.float32)
    out = np.empty_like(src)
    n = src.shape[0]

    for i in range(n):
        out[i] = sharpen_2d(src[i], params)
        if progress is not None and not progress(int((i + 1) * 100 / n)):
            return None
    return out


# ---------------------------------------------------------------------------
#  Quality metrics used by the comparison view
# ---------------------------------------------------------------------------


def difference_stats(a: np.ndarray, b: np.ndarray) -> dict[str, float]:
    """Summarise how far two sections have drifted apart."""
    x = np.asarray(a, dtype=np.float64).ravel()
    y = np.asarray(b, dtype=np.float64).ravel()
    if x.size != y.size or x.size == 0:
        return {}

    diff = y - x
    rms_signal = float(np.sqrt(np.mean(x**2)))
    rms_diff = float(np.sqrt(np.mean(diff**2)))

    stats = {
        "rms_original": rms_signal,
        "rms_difference": rms_diff,
        "max_abs_difference": float(np.max(np.abs(diff))),
    }

    if rms_signal > 0.0:
        stats["relative_rms_pct"] = 100.0 * rms_diff / rms_signal
    if x.std() > 0.0 and y.std() > 0.0:
        stats["correlation"] = float(np.corrcoef(x, y)[0, 1])
    return stats