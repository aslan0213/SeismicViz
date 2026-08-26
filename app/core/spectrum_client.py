"""Client for the independent C# spectrum-analysis module.

The module (``csharp/SpectrumService.exe``) is a separate process. This class
starts it on demand, learns the loopback port it printed on stdout, and then
exchanges fixed-layout binary messages with it over TCP.
"""

from __future__ import annotations

import os
import queue
import socket
import struct
import subprocess
import sys
import threading

import numpy as np

PROTOCOL_VERSION = 1

WINDOW_NONE = 0
WINDOW_HANN = 1
WINDOW_HAMMING = 2

WINDOW_NAMES = {"None": WINDOW_NONE, "Hann": WINDOW_HANN, "Hamming": WINDOW_HAMMING}

_REQUEST_HEADER = struct.Struct("<4siiidi")   # magic, version, nTraces, nSamples, dt, window
_RESPONSE_PREFIX = struct.Struct("<4si")      # magic, status
_RESPONSE_OK = struct.Struct("<id")           # nFreq, df

_STARTUP_TIMEOUT = 15.0   # seconds to wait for the module to announce its port
_CALL_TIMEOUT = 120.0     # seconds to wait for a spectrum result


class SpectrumError(RuntimeError):
    """Raised when the C# module cannot be reached or reports a failure."""


def default_module_path() -> str:
    """Where the build script drops the executable."""
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(here, "csharp", "bin", "SpectrumService.exe")


def default_source_path() -> str:
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(here, "csharp", "SpectrumService.cs")


def find_csc() -> str | None:
    """Locate a C# compiler: the in-box .NET Framework one, or Roslyn."""
    candidates = [
        r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe",
        r"C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe",
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def build_module(source: str | None = None, output: str | None = None) -> str:
    """Compile the C# module with the in-box compiler."""
    source = source or default_source_path()
    output = output or default_module_path()

    csc = find_csc()
    if csc is None:
        raise SpectrumError(
            "No C# compiler found. Install the .NET SDK and run "
            "'csharp\\build.bat', or build SpectrumService.cs manually."
        )
    if not os.path.isfile(source):
        raise SpectrumError("C# source not found at %s" % source)

    os.makedirs(os.path.dirname(output), exist_ok=True)
    result = subprocess.run(
        [csc, "-nologo", "-optimize+", "-target:exe", "-out:" + output, source],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not os.path.isfile(output):
        raise SpectrumError("C# build failed:\n%s\n%s" % (result.stdout, result.stderr))
    return output


class SpectrumClient:
    """Owns the C# child process and the socket that talks to it."""

    def __init__(self, module_path: str | None = None, auto_build: bool = True) -> None:
        self.module_path = module_path or default_module_path()
        self.auto_build = auto_build

        self._process: subprocess.Popen | None = None
        self._socket: socket.socket | None = None
        self._port: int | None = None
        self._lock = threading.RLock()
        self._stderr_thread: threading.Thread | None = None
        self.last_error: str = ""

    # ------------------------------------------------------------------ state

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def port(self) -> int | None:
        return self._port

    def status_text(self) -> str:
        if not self.is_running:
            return "C# module: stopped"
        return "C# module: running (pid %d, port %d)" % (self._process.pid, self._port or 0)

    # ---------------------------------------------------------------- startup

    def ensure_started(self) -> None:
        with self._lock:
            if self.is_running and self._socket is not None:
                return
            self._teardown()

            if not os.path.isfile(self.module_path):
                if not self.auto_build:
                    raise SpectrumError("Spectrum module not found at %s" % self.module_path)
                build_module(output=self.module_path)

            self._start_process()
            self._connect()

    def _start_process(self) -> None:
        creation = 0
        if sys.platform == "win32":
            creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        try:
            self._process = subprocess.Popen(
                [self.module_path, "--port", "0"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                creationflags=creation,
            )
        except OSError as exc:
            raise SpectrumError("Cannot launch %s: %s" % (self.module_path, exc)) from exc

        self._port = self._read_announced_port()

        self._stderr_thread = threading.Thread(
            target=self._drain_stderr, name="spectrum-stderr", daemon=True
        )
        self._stderr_thread.start()

    def _read_announced_port(self) -> int:
        """Parse the 'PORT n' / 'READY' handshake the module prints on stdout."""
        assert self._process is not None and self._process.stdout is not None

        lines: "queue.Queue[str | None]" = queue.Queue()

        def reader() -> None:
            try:
                for line in self._process.stdout:  # type: ignore[union-attr]
                    lines.put(line.strip())
            except Exception:
                pass
            lines.put(None)

        threading.Thread(target=reader, name="spectrum-stdout", daemon=True).start()

        port: int | None = None
        while True:
            try:
                line = lines.get(timeout=_STARTUP_TIMEOUT)
            except queue.Empty:
                break
            if line is None:
                break
            if line.startswith("PORT "):
                try:
                    port = int(line.split()[1])
                except (IndexError, ValueError):
                    pass
            elif line == "READY":
                break

        if port is None:
            stderr = ""
            if self._process.stderr is not None:
                try:
                    stderr = self._process.stderr.read() or ""
                except Exception:
                    stderr = ""
            self._teardown()
            raise SpectrumError(
                "The spectrum module did not report a port within %.0fs.\n%s"
                % (_STARTUP_TIMEOUT, stderr.strip())
            )
        return port

    def _drain_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        try:
            for line in process.stderr:
                line = line.strip()
                if line:
                    self.last_error = line
        except Exception:
            pass

    def _connect(self) -> None:
        assert self._port is not None
        try:
            sock = socket.create_connection(("127.0.0.1", self._port), timeout=_STARTUP_TIMEOUT)
        except OSError as exc:
            self._teardown()
            raise SpectrumError("Cannot connect to the spectrum module: %s" % exc) from exc

        sock.settimeout(_CALL_TIMEOUT)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._socket = sock

    # ------------------------------------------------------------------ calls

    def average_spectrum(
        self,
        section: np.ndarray,
        dt: float,
        window: int = WINDOW_HANN,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Ask the C# module for the average amplitude spectrum of a section."""
        data = np.ascontiguousarray(section, dtype="<f4")
        if data.ndim != 2:
            raise SpectrumError("expected a 2D section, got shape %r" % (data.shape,))
        n_traces, n_samples = data.shape
        if n_traces < 1 or n_samples < 2:
            raise SpectrumError(
                "the selected region is too small (%d x %d)" % (n_traces, n_samples)
            )

        data = np.nan_to_num(data, copy=False, nan=0.0, posinf=0.0, neginf=0.0)

        with self._lock:
            self.ensure_started()
            try:
                return self._exchange(data, n_traces, n_samples, dt, window)
            except (OSError, SpectrumError) as first_error:
                self._teardown()
                try:
                    self.ensure_started()
                    return self._exchange(data, n_traces, n_samples, dt, window)
                except Exception as exc:
                    raise SpectrumError(
                        "Spectrum module call failed: %s (first attempt: %s)"
                        % (exc, first_error)
                    ) from exc

    def _exchange(
        self, data: np.ndarray, n_traces: int, n_samples: int, dt: float, window: int
    ) -> tuple[np.ndarray, np.ndarray]:
        sock = self._socket
        if sock is None:
            raise SpectrumError("not connected")

        header = _REQUEST_HEADER.pack(
            b"SPEC", PROTOCOL_VERSION, n_traces, n_samples, float(dt), int(window)
        )
        sock.sendall(header)
        sock.sendall(data.tobytes(order="C"))

        magic, status = _RESPONSE_PREFIX.unpack(self._recv_exactly(_RESPONSE_PREFIX.size))
        if magic != b"SPCR":
            raise SpectrumError("bad response magic %r" % magic)

        if status != 0:
            (length,) = struct.unpack("<i", self._recv_exactly(4))
            message = self._recv_exactly(max(0, length)).decode("utf-8", "replace")
            raise SpectrumError("module error %d: %s" % (status, message))

        n_freq, df = _RESPONSE_OK.unpack(self._recv_exactly(_RESPONSE_OK.size))
        if n_freq <= 0:
            raise SpectrumError("module returned an empty spectrum")

        payload = self._recv_exactly(n_freq * 4)
        amplitudes = np.frombuffer(payload, dtype="<f4").astype(np.float32)
        frequencies = np.arange(n_freq, dtype=np.float32) * float(df)
        return frequencies, amplitudes

    def _recv_exactly(self, count: int) -> bytes:
        sock = self._socket
        if sock is None:
            raise SpectrumError("not connected")

        chunks: list[bytes] = []
        remaining = count
        while remaining > 0:
            chunk = sock.recv(min(remaining, 1 << 20))
            if not chunk:
                raise SpectrumError("the spectrum module closed the connection")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    # --------------------------------------------------------------- shutdown

    def shutdown(self) -> None:
        with self._lock:
            self._teardown()

    def _teardown(self) -> None:
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
            self._socket = None

        if self._process is not None:
            try:
                if self._process.poll() is None:
                    self._process.terminate()
                    try:
                        self._process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        self._process.kill()
            except OSError:
                pass
            self._process = None

        self._port = None

    def __enter__(self) -> "SpectrumClient":
        self.ensure_started()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.shutdown()