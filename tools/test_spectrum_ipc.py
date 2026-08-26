"""Integration test for the C# spectrum-analysis module.

Exercises:
  1. Auto-compilation (if the .exe is missing).
  2. Protocol exchange against a two-sine synthetic test section.
  3. Numerical agreement with numpy.fft.rfft.
  4. Large payload handling (400 traces x 1000 samples = 1.6 MB).
  5. Error response on degenerate geometry.

Run::

    python tools/test_spectrum_ipc.py
"""

from __future__ import annotations

import os
import sys

# Ensure 'app' is on sys.path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from app.core.spectrum_client import (
    WINDOW_HANN,
    WINDOW_NONE,
    SpectrumClient,
    SpectrumError,
)


def _numpy_reference(section: np.ndarray, dt: float, window_kind: int) -> tuple[np.ndarray, np.ndarray]:
    """Compute the same average single-sided spectrum using NumPy."""
    n_traces, n_samples = section.shape
    nfft = 2
    while nfft < n_samples:
        nfft <<= 1

    if window_kind == WINDOW_HANN:
        taper = 0.5 * (1.0 - np.cos(2.0 * np.pi * np.arange(n_samples) / (n_samples - 1)))
    else:
        taper = np.ones(n_samples, dtype=np.float64)

    taper_gain = float(taper.sum()) or float(n_samples)

    centered = section.astype(np.float64) - section.mean(axis=1, keepdims=True)
    windowed = centered * taper[None, :]

    rfft = np.fft.rfft(windowed, n=nfft, axis=1)
    mag = np.abs(rfft)
    if nfft > 2:
        mag[:, 1:-1] *= 2.0  # single-sided doubling

    avg_amp = (mag / taper_gain).mean(axis=0).astype(np.float32)
    df = 1.0 / (nfft * dt)
    freqs = np.arange(len(avg_amp), dtype=np.float32) * float(df)
    return freqs, avg_amp


def test_numerical_agreement() -> None:
    print("[1/3] Testing numerical agreement against NumPy FFT...")
    dt = 0.004
    n_traces = 32
    n_samples = 400
    t = np.arange(n_samples) * dt

    # 18 Hz + 42 Hz sine waves.
    signal = (np.sin(2 * np.pi * 18.0 * t) + 0.6 * np.sin(2 * np.pi * 42.0 * t)).astype(np.float32)
    section = np.tile(signal, (n_traces, 1))

    with SpectrumClient() as client:
        freqs_cs, amps_cs = client.average_spectrum(section, dt, WINDOW_HANN)

    freqs_np, amps_np = _numpy_reference(section, dt, WINDOW_HANN)

    assert len(freqs_cs) == len(freqs_np), "frequency bins mismatch"
    assert np.allclose(freqs_cs, freqs_np, atol=1e-5), "frequencies do not match"

    max_rel_err = np.max(np.abs(amps_cs - amps_np)) / np.max(amps_np)
    print("      Max relative error vs NumPy: %.2e" % max_rel_err)
    assert max_rel_err < 2e-3, "relative error too large (%.2e)" % max_rel_err
    print("      PASS: C# FFT matches NumPy FFT within tolerance.")


def test_large_payload() -> None:
    print("[2/3] Testing large payload (400 traces x 1000 samples = 1.6 MB)...")
    rng = np.random.default_rng(123)
    big = rng.normal(0, 1, (400, 1000)).astype(np.float32)

    with SpectrumClient() as client:
        freqs, amps = client.average_spectrum(big, dt=0.002, window=WINDOW_NONE)

    assert len(freqs) == 1024 // 2 + 1, "unexpected FFT size for 1000 samples"
    assert amps.shape == freqs.shape
    assert np.all(np.isfinite(amps))
    print("      PASS: Received %d frequency bins (%.1f MB round-trip OK)." % (len(freqs), big.nbytes / 1e6))


def test_error_handling() -> None:
    print("[3/3] Testing error handling for invalid/degenerate geometry...")
    degenerate = np.zeros((1, 1), dtype=np.float32)

    with SpectrumClient() as client:
        try:
            client.average_spectrum(degenerate, dt=0.004)
            assert False, "should have raised SpectrumError"
        except SpectrumError as exc:
            print("      PASS: Module correctly rejected degenerate input (%s)." % exc)


def main() -> None:
    print("=== Running Spectrum IPC Tests ===\n")
    test_numerical_agreement()
    test_large_payload()
    test_error_handling()
    print("\nALL SPECTRUM IPC TESTS PASSED!")


if __name__ == "__main__":
    main()