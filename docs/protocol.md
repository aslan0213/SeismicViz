# Python ↔ C# spectrum module — communication and data exchange

The spectrum analysis is **not** part of the Python application. It lives in a
separate executable, `csharp/bin/SpectrumService.exe`, built from a single C#
source file. The Python side only packages data and draws the answer.

---

## 1. Why a loopback socket

| Option considered | Verdict |
|---|---|
| Temporary files on disk | Simple, but writes tens of MB per interaction and leaves artefacts behind. Slow for a slider that the user drags. |
| stdin/stdout pipe | Works, but couples the child's lifetime to one stream, and any stray `Console.WriteLine` in the module corrupts the data stream. |
| **TCP on 127.0.0.1** | **Chosen.** Binary-clean, no disk traffic, strictly request/response, survives a chatty child (stdout stays free for logging), and the same protocol would work unchanged if the module were moved to another machine. |
| COM / .NET embedding (pythonnet) | Ties the two runtimes into one process — the opposite of the "independent module" the task asks for. |

Measured cost on the sample cube: a 400 × 1000 float32 payload (1.5 MB)
completes a full round trip in **≈ 21 ms**, including process-side FFT.

---

## 2. Process lifecycle

```
Python (SpectrumClient)                     C# (SpectrumService.exe)
──────────────────────────────────────────────────────────────────────
 ensure_started()
   ├─ if the exe is missing → build it with the in-box csc.exe
   ├─ Popen([exe, "--port", "0"])  ─────────►  TcpListener on 127.0.0.1:0
   │                                           OS assigns a free port
   │                               ◄─────────  stdout: "PORT 52451"
   │                               ◄─────────  stdout: "READY"
   ├─ parse the port
   └─ socket.create_connection(("127.0.0.1", 52451))
                                   ─────────►  AcceptTcpClient()
                                               one thread per connection
```

* Port `0` means "let the OS choose", so several instances of the application
  can run at once without colliding.
* The connection is kept open; every request reuses it.
* `stderr` is drained by a background thread so a chatty module can never fill
  its pipe and stall.
* If a call fails, the client tears the child down and retries **once** with a
  fresh process — this recovers transparently from a crashed module.
* On exit, `SpectrumClient.shutdown()` terminates the child (`terminate`, then
  `kill` after 3 s).

---

## 3. Wire format

All integers and floats are **little-endian**; there is no padding and no
alignment. `float32` = IEEE 754 single, `float64` = IEEE 754 double.

### 3.1 Request — Python → C#

| Offset | Type | Field | Meaning |
|---:|---|---|---|
| 0 | `char[4]` | magic | ASCII `"SPEC"` |
| 4 | `int32` | version | protocol version, currently `1` |
| 8 | `int32` | nTraces | rows in the payload |
| 12 | `int32` | nSamples | samples per trace |
| 16 | `float64` | dt | sample interval in **seconds** (e.g. `0.004`) |
| 24 | `int32` | window | `0` none, `1` Hann, `2` Hamming |
| 28 | `float32[nTraces × nSamples]` | data | row-major: one trace per row, time along the row |

Header size is 28 bytes; the payload follows immediately.

The array Python sends is exactly the ROI cut out of the displayed section:

```python
section = volume.slice(axis, index)      # (n_traces, n_samples)
roi     = section[i0:i1, j0:j1]          # rectangle picked in the UI
client.average_spectrum(roi, dt=0.004, window=WINDOW_HANN)
```

Because every section is stored as *(traces, samples)* whatever its
orientation, the module never needs to know whether it received an inline, a
crossline or a composite line.

### 3.2 Response — C# → Python, success

| Offset | Type | Field | Meaning |
|---:|---|---|---|
| 0 | `char[4]` | magic | ASCII `"SPCR"` |
| 4 | `int32` | status | `0` |
| 8 | `int32` | nFreq | number of bins = `nfft/2 + 1` |
| 12 | `float64` | df | bin spacing in Hz = `1 / (nfft · dt)` |
| 20 | `float32[nFreq]` | amplitude | average amplitude spectrum |

The frequency axis is reconstructed on the Python side as
`freqs = arange(nFreq) * df`, so it never has to travel over the wire.

### 3.3 Response — C# → Python, failure

| Offset | Type | Field |
|---:|---|---|
| 0 | `char[4]` | `"SPCR"` |
| 4 | `int32` | status ≠ 0 |
| 8 | `int32` | msgLen |
| 12 | `byte[msgLen]` | UTF-8 message |

| status | Meaning |
|---:|---|
| 1 | bad magic |
| 2 | unsupported protocol version |
| 3 | invalid geometry (`nTraces ≤ 0`, `nSamples ≤ 1`, or absurdly large) |
| 4 | payload larger than the 512 MB guard |
| 5 | computation error |

The client raises `SpectrumError` carrying that message, and the panel shows it
instead of a stale curve.

---

## 4. What the module computes

For each trace in the received block:

1. subtract the trace mean (removes any DC step),
2. multiply by the selected taper,
3. zero-pad to the next power of two,
4. radix-2 Cooley–Tukey FFT (hand-written; no external library),
5. take the magnitude of bins `0 … nfft/2`, doubling the interior bins so the
   single-sided amplitude matches the true signal amplitude,
6. divide by the sum of the taper, so the result does not depend on the window
   or on the trace length.

The magnitudes are then averaged over all traces. Traces containing `NaN` or
`Inf` are skipped rather than poisoning the average.

**Validation.** `tools/test_spectrum_ipc.py` compares the module against
`numpy.fft.rfft` on the same data with the same conventions; the maximum
relative error is **≈ 7 × 10⁻⁹**. `SpectrumService.exe --selftest` independently
checks that a 25 Hz sine sampled at 4 ms peaks in the 25 Hz bin with unit
amplitude.

---

## 5. Building the module

No .NET SDK and no NuGet packages are required — the source targets C# 5 so
that the compiler shipped with the .NET Framework can build it:

```bat
csharp\build.bat
```

which runs

```
C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe ^
    -nologo -optimize+ -target:exe -out:bin\SpectrumService.exe SpectrumService.cs
```

If the executable is missing when the application first needs a spectrum,
`SpectrumClient` runs that same command itself, so a fresh checkout works
without a manual build step. **Tools ▸ Rebuild C# spectrum module** does it on
demand.
