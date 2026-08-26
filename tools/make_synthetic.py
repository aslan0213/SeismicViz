"""Generate a synthetic post-stack seismic volume for testing.

The cube is built from a structural model convolved with a Ricker wavelet and
has enough variety (regional dip, anticline, normal fault, meandering channel,
band-limited noise, acquisition footprint) to exercise every feature of the
viewer.

Run::

    python tools/make_synthetic.py
"""

from __future__ import annotations

import os
import sys
import time

import numpy as np

# ---------------------------------------------------------------------------
#  Parameters
# ---------------------------------------------------------------------------

N_IL, N_XL, N_T = 180, 160, 320
DT = 0.004                  # seconds  (4 ms)
PEAK_FREQ = 28.0             # Hz      (Ricker wavelet)
N_HORIZONS = 26
NOISE_LEVEL = 0.12
SEED = 42

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "seismic_synthetic.npy")


# ---------------------------------------------------------------------------
#  Ricker wavelet
# ---------------------------------------------------------------------------

def ricker(freq: float, dt: float, length: float = 0.08) -> np.ndarray:
    """Ricker (Mexican hat) wavelet centred at t = 0."""
    t = np.arange(-length / 2, length / 2, dt)
    a = (np.pi * freq * t) ** 2
    return ((1.0 - 2.0 * a) * np.exp(-a)).astype(np.float32)


# ---------------------------------------------------------------------------
#  Structural model
# ---------------------------------------------------------------------------

def make_structure(rng: np.random.Generator) -> np.ndarray:
    """Horizon depth map ``(n_il, n_xl)`` for the first (shallowest) horizon."""
    il = np.arange(N_IL, dtype=np.float64)
    xl = np.arange(N_XL, dtype=np.float64)
    II, XX = np.meshgrid(il, xl, indexing="ij")

    # Regional dip.
    surface = 40.0 + 0.15 * II + 0.10 * XX

    # Anticline dome.
    cx, cy = N_IL * 0.45, N_XL * 0.55
    r2 = ((II - cx) / (N_IL * 0.28)) ** 2 + ((XX - cy) / (N_XL * 0.28)) ** 2
    surface -= 20.0 * np.exp(-r2 * 3.0)

    # Random wobble for realism.
    surface += rng.normal(0, 1.2, surface.shape)

    return surface


def make_fault_throw(rng: np.random.Generator) -> np.ndarray:
    """Normal fault that displaces the eastern half downwards."""
    il = np.arange(N_IL, dtype=np.float64)
    xl = np.arange(N_XL, dtype=np.float64)
    II, XX = np.meshgrid(il, xl, indexing="ij")

    fault_xl = N_XL * 0.62 + 0.12 * (II - N_IL * 0.5)
    throw = 14.0 / (1.0 + np.exp(-(XX - fault_xl) * 0.6))
    return throw.astype(np.float64)


# ---------------------------------------------------------------------------
#  Channel
# ---------------------------------------------------------------------------

def make_channel(rng: np.random.Generator) -> np.ndarray:
    """A meandering channel that will be visible on time slices."""
    il = np.arange(N_IL, dtype=np.float64)
    centre = N_XL * 0.35 + 12.0 * np.sin(2 * np.pi * il / (N_IL * 0.45))
    centre += 6.0 * np.sin(2 * np.pi * il / (N_IL * 0.18) + 1.2)

    xl = np.arange(N_XL, dtype=np.float64)
    II, XX = np.meshgrid(il, xl, indexing="ij")
    dist = np.abs(XX - centre[:, None])
    width = 4.5
    channel = np.exp(-(dist / width) ** 2)
    return channel.astype(np.float64)


# ---------------------------------------------------------------------------
#  Build the cube
# ---------------------------------------------------------------------------

def build_cube() -> np.ndarray:
    rng = np.random.default_rng(SEED)

    print("Building structural model...")
    top_surface = make_structure(rng)
    fault = make_fault_throw(rng)
    channel = make_channel(rng)

    reflectivity = np.zeros((N_IL, N_XL, N_T), dtype=np.float32)

    # Place horizons.
    spacing = (N_T - 80) / N_HORIZONS
    strengths = rng.uniform(0.3, 1.0, N_HORIZONS).astype(np.float32)
    # Make a few horizons brighter (marker beds).
    for idx in (3, 9, 17, 22):
        if idx < N_HORIZONS:
            strengths[idx] *= 2.4

    print("Placing %d horizons..." % N_HORIZONS)
    for h in range(N_HORIZONS):
        depth = top_surface + h * spacing + fault * (0.3 + 0.7 * h / N_HORIZONS)
        depth = np.clip(depth, 0, N_T - 1)
        # Smooth polarity reversal near the channel.
        polarity = 1.0 - 0.8 * channel if h in (7, 8, 9) else np.ones_like(channel)
        for i in range(N_IL):
            for j in range(N_XL):
                t_idx = int(round(depth[i, j]))
                if 0 <= t_idx < N_T:
                    reflectivity[i, j, t_idx] += strengths[h] * polarity[i, j]

    # Channel amplitude anomaly.
    channel_horizon = int(8 * spacing + 40)
    if channel_horizon < N_T:
        reflectivity[:, :, channel_horizon] += 0.6 * channel.astype(np.float32)

    # Convolve with wavelet.
    print("Convolving with Ricker wavelet (%.0f Hz)..." % PEAK_FREQ)
    w = ricker(PEAK_FREQ, DT)
    cube = np.zeros_like(reflectivity)
    for i in range(N_IL):
        for j in range(N_XL):
            cube[i, j, :] = np.convolve(reflectivity[i, j, :], w, mode="same")

    # Amplitude decay with depth.
    decay = np.exp(-np.arange(N_T, dtype=np.float32) * DT / 0.8)
    cube *= decay[None, None, :]

    # Band-limited noise.
    print("Adding noise (level %.0f%%)..." % (NOISE_LEVEL * 100))
    noise = rng.normal(0, 1, cube.shape).astype(np.float32)
    from scipy.ndimage import gaussian_filter1d
    noise = gaussian_filter1d(noise, sigma=1.5, axis=2)
    noise *= NOISE_LEVEL * cube.std() / max(noise.std(), 1e-9)
    cube += noise

    # Weak acquisition footprint (every 6th crossline slightly louder).
    footprint = np.ones(N_XL, dtype=np.float32)
    footprint[::6] = 1.04
    cube *= footprint[None, :, None]

    return cube


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------

def main() -> None:
    t0 = time.time()
    cube = build_cube()

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    np.save(OUTPUT_FILE, cube)
    size_mb = os.path.getsize(OUTPUT_FILE) / (1024 * 1024)

    print(
        "\nSaved %s\n  shape : %s\n  dtype : %s\n  size  : %.1f MB\n  time  : %.1f s"
        % (OUTPUT_FILE, cube.shape, cube.dtype, size_mb, time.time() - t0)
    )


if __name__ == "__main__":
    main()