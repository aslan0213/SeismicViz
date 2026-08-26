"""Round-trip check of the Python <-> C# spectrum link.

Sends a synthetic gather whose spectrum is known analytically and compares the
module's answer against numpy's own FFT.
"""
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.core.spectrum_client import SpectrumClient, WINDOW_HANN, WINDOW_NONE


def reference_spectrum(section, dt, window):
    n_samples = section.shape[1]
    nfft = 1 << (n_samples - 1).bit_length()
    nfft = max(nfft, 2)
    if window == WINDOW_HANN:
        taper = 0.5 * (1.0 - np.cos(2 * np.pi * np.arange(n_samples) / (n_samples - 1)))
    else:
        taper = np.ones(n_samples)
    gain = taper.sum()

    x = section.astype(np.float64)
    x = (x - x.mean(axis=1, keepdims=True)) * taper
    spec = np.abs(np.fft.rfft(x, n=nfft, axis=1))
    spec[:, 1:nfft // 2] *= 2.0
    amp = (spec / gain).mean(axis=0)
    freqs = np.fft.rfftfreq(nfft, d=dt)
    return freqs, amp


def main():
    dt = 0.004
    n_traces, n_samples = 64, 500
    rng = np.random.default_rng(7)

    t = np.arange(n_samples) * dt
    section = np.zeros((n_traces, n_samples), dtype=np.float32)
    for f0, a in ((18.0, 1.0), (42.0, 0.4)):
        section += (a * np.sin(2 * np.pi * f0 * t)).astype(np.float32)
    section += rng.normal(0, 0.02, section.shape).astype(np.float32)

    with SpectrumClient() as client:
        print(client.status_text())

        for window, label in ((WINDOW_HANN, "Hann"), (WINDOW_NONE, "None")):
            t0 = time.perf_counter()
            freqs, amp = client.average_spectrum(section, dt, window)
            elapsed = (time.perf_counter() - t0) * 1000

            rf, ra = reference_spectrum(section, dt, window)
            assert freqs.shape == rf.shape, (freqs.shape, rf.shape)
            assert np.allclose(freqs, rf, atol=1e-3), "frequency axis mismatch"

            err = np.max(np.abs(amp - ra)) / max(ra.max(), 1e-12)
            peaks = freqs[np.argsort(amp)[-2:]]
            print("  window=%-5s  %4d bins  df=%.4f Hz  %6.1f ms  "
                  "max rel.err=%.2e  peaks=%s"
                  % (label, amp.size, freqs[1], elapsed, err, np.sort(peaks)))
            assert err < 2e-3, "C# spectrum disagrees with numpy (%.3e)" % err

        # A tall payload exercises the streaming reader on both sides.
        big = rng.normal(0, 1, (400, 1000)).astype(np.float32)
        t0 = time.perf_counter()
        freqs, amp = client.average_spectrum(big, dt, WINDOW_HANN)
        print("  400x1000 payload (1.5 MB): %.0f ms, %d bins"
              % ((time.perf_counter() - t0) * 1000, amp.size))

        # Error path: a degenerate ROI must come back as a clean exception.
        try:
            client.average_spectrum(np.zeros((1, 1), np.float32), dt)
        except Exception as exc:
            print("  degenerate ROI rejected as expected: %s" % type(exc).__name__)
        else:
            raise AssertionError("expected the module to reject a 1x1 ROI")

    print("IPC ROUND TRIP OK")


if __name__ == "__main__":
    main()