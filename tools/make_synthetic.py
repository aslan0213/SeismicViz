"""Generate a synthetic post-stack seismic cube for testing.

The assignment ships a real ``(il, xl, t)`` ``.npy`` volume; this script builds
a stand-in with the same layout and the same visual character - dipping and
folded reflectors, a normal fault, a channel, a bandlimited wavelet and noise -
so the application can be exercised without the original data.

    python tools/make_synthetic.py --out data/seismic_synthetic.npy
"""

from __future__ import annotations

import argparse
import os

import numpy as np
from scipy import ndimage


def ricker(freq: float, dt: float, length: float = 0.256) -> np.ndarray:
    """Zero-phase Ricker wavelet, the usual stand-in for a post-stack pulse."""
    n = int(length / dt)
    if n % 2 == 0:
        n += 1
    t = (np.arange(n) - n // 2) * dt
    a = (np.pi * freq * t) ** 2
    return ((1.0 - 2.0 * a) * np.exp(-a)).astype(np.float32)


def smooth_noise(shape: tuple[int, int], sigma: float, rng: np.random.Generator) -> np.ndarray:
    """Correlated random field, used to give horizons a natural wobble."""
    field = rng.normal(size=shape)
    field = ndimage.gaussian_filter(field, sigma=sigma, mode="wrap")
    peak = np.abs(field).max()
    return (field / peak) if peak > 0 else field


def build_structure(n_il: int, n_xl: int, rng: np.random.Generator) -> np.ndarray:
    """Vertical shift (in samples) applied to every horizon.

    Combines a regional dip, a broad anticline and a fault so the cube has
    something worth panning a slice through.
    """
    il = np.arange(n_il)[:, None].astype(np.float64)
    xl = np.arange(n_xl)[None, :].astype(np.float64)

    dip = 0.06 * il + 0.02 * xl

    # Anticline centred just off the middle of the survey.
    ci, cx = n_il * 0.45, n_xl * 0.55
    radius2 = ((il - ci) / (n_il * 0.30)) ** 2 + ((xl - cx) / (n_xl * 0.34)) ** 2
    anticline = -26.0 * np.exp(-radius2)

    # Normal fault: a smooth but rapid throw across an oblique plane.
    plane = il * 0.75 + xl - n_xl * 0.62
    fault = 11.0 * np.tanh(plane / 3.0)

    wobble = 5.0 * smooth_noise((n_il, n_xl), min(n_il, n_xl) * 0.10, rng)

    return dip + anticline + fault + wobble


def build_reflectivity(
    n_il: int, n_xl: int, n_t: int, rng: np.random.Generator
) -> np.ndarray:
    """Layer-cake reflectivity warped by the structural model."""
    structure = build_structure(n_il, n_xl, rng)
    refl = np.zeros((n_il, n_xl, n_t), dtype=np.float32)

    # Horizon depths in samples, with a mix of strong and weak contrasts.
    base_depths = np.sort(rng.uniform(0.06, 0.94, size=26) * n_t)
    strengths = rng.normal(0.0, 0.35, size=base_depths.size)
    strengths[np.abs(strengths) < 0.08] = 0.12          # avoid invisible layers
    strengths[::7] *= 2.4                                # a few marker horizons

    il_idx, xl_idx = np.meshgrid(np.arange(n_il), np.arange(n_xl), indexing="ij")

    for depth, strength in zip(base_depths, strengths):
        # Deeper horizons follow the structure more closely than shallow ones.
        follow = np.clip(depth / n_t * 1.6, 0.15, 1.0)
        surface = depth + structure * follow
        surface += 1.5 * smooth_noise((n_il, n_xl), n_il * 0.05, rng)

        amp = strength * (1.0 + 0.25 * smooth_noise((n_il, n_xl), n_il * 0.12, rng))
        _stamp_horizon(refl, il_idx, xl_idx, surface, amp)

    _add_channel(refl, structure, rng)
    return refl


def _stamp_horizon(
    refl: np.ndarray,
    il_idx: np.ndarray,
    xl_idx: np.ndarray,
    surface: np.ndarray,
    amplitude: np.ndarray,
) -> None:
    """Write a reflection coefficient at a fractional depth.

    Rounding each horizon to the nearest sample would leave visible staircase
    steps wherever the surface crosses a sample boundary, so the coefficient is
    split linearly between the two samples that straddle it.
    """
    n_t = refl.shape[2]
    lower = np.floor(surface).astype(np.int64)
    frac = (surface - lower).astype(np.float32)

    for offset, weight in ((0, 1.0 - frac), (1, frac)):
        sample = lower + offset
        valid = (sample >= 0) & (sample < n_t)
        if not valid.any():
            continue
        refl[il_idx[valid], xl_idx[valid], sample[valid]] += (
            amplitude[valid] * weight[valid]
        ).astype(np.float32)


def _add_channel(refl: np.ndarray, structure: np.ndarray, rng: np.random.Generator) -> None:
    """Carve a meandering channel into one horizon - a recognisable time-slice feature."""
    n_il, n_xl, n_t = refl.shape
    depth = 0.42 * n_t

    il = np.arange(n_il)
    centre = n_xl * 0.5 + n_xl * 0.18 * np.sin(2 * np.pi * il / (n_il * 0.8))
    centre += n_xl * 0.06 * np.sin(2 * np.pi * il / (n_il * 0.23))
    half_width = n_xl * 0.035

    for i in range(n_il):
        lo = int(max(0, centre[i] - half_width))
        hi = int(min(n_xl, centre[i] + half_width))
        if hi <= lo:
            continue
        position = depth + structure[i, lo:hi].mean() * 0.7
        lower = int(np.floor(position))
        frac = float(position - lower)
        amp = -0.9 + rng.normal(0, 0.05, hi - lo).astype(np.float32)
        for offset, weight in ((0, 1.0 - frac), (1, frac)):
            if 0 <= lower + offset < n_t:
                refl[i, lo:hi, lower + offset] += (amp * weight).astype(np.float32)


def make_volume(
    n_il: int, n_xl: int, n_t: int, dt: float, freq: float, noise: float, seed: int
) -> np.ndarray:
    rng = np.random.default_rng(seed)

    refl = build_reflectivity(n_il, n_xl, n_t, rng)
    wavelet = ricker(freq, dt)

    # Convolve every trace with the wavelet, keeping the original length.
    seismic = ndimage.convolve1d(refl, wavelet, axis=2, mode="constant", cval=0.0, origin=0)
    del refl

    # Bandlimited random noise plus a faint acquisition footprint.
    if noise > 0:
        grain = rng.normal(0, 1, seismic.shape).astype(np.float32)
        grain = ndimage.gaussian_filter1d(grain, sigma=1.2, axis=2, mode="nearest")
        seismic += (noise * grain.std(ddof=0) ** -1 * seismic.std() * grain).astype(np.float32)

        footprint = 0.03 * seismic.std() * np.cos(np.arange(n_xl) * np.pi / 3.0)
        seismic += footprint.astype(np.float32)[None, :, None]

    # Gentle amplitude decay with time, as in a real processed cube.
    gain = np.exp(-np.arange(n_t) / (n_t * 1.9)).astype(np.float32)
    seismic *= gain[None, None, :]

    peak = float(np.percentile(np.abs(seismic), 99.5))
    if peak > 0:
        seismic /= peak
    return seismic.astype(np.float32)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="data/seismic_synthetic.npy")
    parser.add_argument("--iline", type=int, default=180, help="number of inlines")
    parser.add_argument("--xline", type=int, default=160, help="number of crosslines")
    parser.add_argument("--samples", type=int, default=320, help="samples per trace")
    parser.add_argument("--dt", type=float, default=0.004, help="sample interval, seconds")
    parser.add_argument("--freq", type=float, default=26.0, help="Ricker peak frequency, Hz")
    parser.add_argument("--noise", type=float, default=0.12, help="noise level, 0-1")
    parser.add_argument("--seed", type=int, default=2024)
    args = parser.parse_args()

    volume = make_volume(
        args.iline, args.xline, args.samples, args.dt, args.freq, args.noise, args.seed
    )

    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    np.save(out, volume)

    print("wrote %s" % out)
    print("  shape   : %d IL x %d XL x %d samples" % volume.shape)
    print("  dtype   : %s   size: %.1f MB" % (volume.dtype, volume.nbytes / 1024 / 1024))
    print("  dt      : %.1f ms  (Nyquist %.0f Hz)" % (args.dt * 1000, 0.5 / args.dt))
    print("  range   : %+.3f .. %+.3f" % (volume.min(), volume.max()))


if __name__ == "__main__":
    main()