# -*- coding: utf-8 -*-
"""LAN control of Teledyne LeCroy WavePro/WaveRunner oscilloscopes.

MAUI remote commands can be sent over two transports:
  1. VICP (port 1861, LeCroy proprietary): implemented natively in this
     file. Works with the scope's default Remote setting "TCPIP (VICP)".
     No NI-VISA / VICP passport required. Resource name
     "VICP::<SCOPE_IP>::INSTR".
  2. VXI-11 (LXI): via PyVISA. Only when the scope's Remote setting is
     "LXI (VXI11)". Resource name "TCPIP0::<SCOPE_IP>::inst0::INSTR".
On connect, the *IDN? response is validated on the requested transport
and, if that fails, the other transport is tried automatically (prevents
the "connected but not talking" state).

References: Teledyne LeCroy "MAUI Oscilloscopes Remote Control and
Automation Manual" / "VICP Protocol" (8-byte header: operation,
version=1, seq, 0, length BE32)

Commonly used commands:
  CHDR OFF            strip command headers from responses (simplifies
                      parsing)
  TRMD AUTO|NORM|SINGLE|STOP     trigger mode
  TRSE EDGE,SR,EX     edge-trigger source setup
  EX:TRLV 410MV       trigger level
  EX:TRCP DC          trigger coupling
  TRDL -53NS          horizontal offset (trigger delay)
  Cn:TRA ON|OFF, Cn:VDIV, Cn:OFST, Cn:CPL D50  channel settings
  TDIV 50NS           timebase
  SEQ ON,200          sequence (segment) mode
  CLSW                clear sweeps (averaging accumulation)
  HCSU/SCDP           hardcopy setup / screen-image capture (for
                      mirroring)
  *SAV n / *RCL n     save/recall front-panel setups
  VBS 'app....'       Automation (ActiveDSO equivalent) — waveform
                      autosave etc.
"""

from __future__ import annotations

import socket
import struct
import threading
import time
from typing import Callable, Optional

LogFunc = Optional[Callable[[str, str], None]]


class _VicpTransport:
    """Minimal implementation of the LeCroy VICP protocol (TCP port 1861).

    Frame structure: 8-byte header + payload.
      operation: OR of DATA=0x80, REMOTE=0x40, EOI=0x01
      version:   fixed 0x01
      sequence:  cycles 1–255 (0 is not allowed)
      spare:     0
      length:    payload length (big-endian 32 bit)
    Responses concatenate frames until the EOI flag is set.
    """

    DEFAULT_PORT = 1861
    OP_DATA, OP_REMOTE, OP_EOI = 0x80, 0x40, 0x01

    def __init__(self, host: str, port: int | None = None,
                 timeout_s: float = 10.0):
        self._sock = socket.create_connection(
            (host, port or self.DEFAULT_PORT), timeout=timeout_s)
        self._sock.settimeout(timeout_s)
        self._timeout_s = timeout_s
        self._seq = 0
        # Whether the previous read ended in a timeout. While True, unread
        # frames (screen PNGs etc.) could leak into the next query
        # response, garbling it or shifting the answers, so the buffer is
        # drained and re-synchronized before the next command is sent.
        self._dirty = False

    def _next_seq(self) -> int:
        self._seq = self._seq % 255 + 1
        return self._seq

    def _drain(self, grace_s: float = 0.3) -> None:
        """Discard stale response frames left in the receive buffer
        (re-sync).

        grace_s=0 discards only already-arrived data, non-blocking.
        """
        try:
            self._sock.settimeout(grace_s if grace_s > 0 else 0.0)
            while self._sock.recv(65536):
                pass
        except (TimeoutError, socket.timeout, BlockingIOError, OSError):
            pass
        finally:
            self._sock.settimeout(self._timeout_s)

    def write(self, cmd: str) -> None:
        if self._dirty:
            # right after a timeout: wait briefly for late data, then drop it
            self._drain(0.3)
            self._dirty = False
        else:
            # in normal operation, drop any already-arrived leftovers
            # (non-blocking)
            self._drain(0.0)
        payload = (cmd + "\n").encode("ascii")
        header = struct.pack(
            ">BBBBI", self.OP_DATA | self.OP_REMOTE | self.OP_EOI,
            0x01, self._next_seq(), 0, len(payload))
        self._sock.sendall(header + payload)

    def _recv_exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self._sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("VICP connection closed")
            buf += chunk
        return buf

    def read_raw(self) -> bytes:
        """Return payloads concatenated up to the EOI frame."""
        try:
            data = b""
            while True:
                op, _ver, _seq, _sp, length = struct.unpack(
                    ">BBBBI", self._recv_exact(8))
                if length:
                    data += self._recv_exact(length)
                if op & self.OP_EOI:
                    return data
        except (TimeoutError, socket.timeout):
            # A partially-read frame arriving later would shift the
            # responses, so it is discarded before the next command
            # (via _drain on the write side)
            self._dirty = True
            raise TimeoutError(
                "VICP response timeout (leftover received data will be "
                "discarded and re-synchronized on the next command)")

    def query(self, cmd: str) -> str:
        self.write(cmd)
        return self.read_raw().decode("ascii", errors="replace").strip()

    def close(self) -> None:
        self._sock.close()


class _VisaTransport:
    """Transport via PyVISA (VXI-11/LXI)."""

    def __init__(self, resource: str, timeout_ms: int):
        import pyvisa
        try:
            # prefer a system backend (NI-VISA etc.); fall back to pyvisa-py
            rm = pyvisa.ResourceManager()
        except (ValueError, OSError):
            rm = pyvisa.ResourceManager("@py")
        self._inst = rm.open_resource(resource)
        self._inst.timeout = timeout_ms

    def write(self, cmd: str) -> None:
        self._inst.write(cmd)

    def query(self, cmd: str) -> str:
        return self._inst.query(cmd).strip()

    def read_raw(self) -> bytes:
        return bytes(self._inst.read_raw())

    def close(self) -> None:
        self._inst.close()


class LecroyScope:
    """Wrapper for LeCroy MAUI scopes (thread-safe).

    The mirroring thread and control commands share a single VISA
    session, so all I/O is serialized behind self._lock.
    """

    CHANNELS = ("C1", "C2", "C3", "C4")
    TRIGGER_SOURCES = ("EX", "C1", "C2", "C3", "C4", "LINE")

    def __init__(self, resource: str, timeout_ms: int = 10000,
                 simulate: bool = False, log: LogFunc = None):
        self.resource = resource
        self.timeout_ms = timeout_ms
        self.simulate = simulate
        # Amplitude model for simulation: callable(ch) -> amplitude [V]
        self.sim_amplitude = None
        self._log = log or (lambda d, t: None)
        self._transport = None
        self._lock = threading.Lock()
        self.connected = False
        # While True, the screen mirror is paused (get_screen_png returns
        # empty bytes). SCDP during a sequence acquisition responds
        # slowly, times out, and shifts subsequent responses, so
        # ScanWorker sets this while an auto scan is running.
        self.mirror_paused = False
        self._sim_t0 = time.monotonic()
        # number of files saved in simulation (for verification)
        self._sim_saved = 0

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------
    @staticmethod
    def _parse_resource(resource: str) -> tuple[str, str, int | None]:
        """Split the resource name into (transport, host, port).

        e.g. "VICP::<SCOPE_IP>::INSTR"           → ("VICP", ip, None)
            "VICP::<SCOPE_IP>::1861::INSTR"     → ("VICP", ip, 1861)
            "TCPIP0::<SCOPE_IP>::inst0::INSTR"  → ("VXI-11", ip, None)
        """
        parts = resource.split("::")
        kind = "VICP" if parts[0].upper().startswith("VICP") else "VXI-11"
        host = parts[1] if len(parts) > 1 else resource
        port = None
        if len(parts) > 2 and parts[2].isdigit():
            port = int(parts[2])
        return kind, host, port

    def _open_transport(self, kind: str, host: str, port: int | None):
        if kind == "VICP":
            return _VicpTransport(host, port, timeout_s=self.timeout_ms / 1000)
        return _VisaTransport(
            f"TCPIP0::{host}::inst0::INSTR", self.timeout_ms)

    def connect(self) -> None:
        """Connect with the requested transport and validate the *IDN?
        response.

        On failure the other transport (VICP ⇔ VXI-11) is tried
        automatically. A connection that does not answer *IDN? would be
        "connected but not talking", so it is rejected with an error.
        """
        if self.simulate:
            self.connected = True
            self._log("INFO", f"[SIM] LeCroy connected ({self.resource})")
            return
        kind, host, port = self._parse_resource(self.resource.strip())
        order = ["VICP", "VXI-11"] if kind == "VICP" else ["VXI-11", "VICP"]
        errors = []
        for k in order:
            try:
                transport = self._open_transport(k, host, port)
            except Exception as exc:
                errors.append(f"[{k}] connection failed: {exc}")
                self._log("WARN", errors[-1])
                continue
            try:
                # strip headers from responses to ease numeric parsing
                transport.write("CHDR OFF")
                idn = transport.query("*IDN?")
            except Exception as exc:
                transport.close()
                errors.append(f"[{k}] no *IDN? response: {exc}")
                self._log("WARN", errors[-1])
                continue
            if not idn:
                transport.close()
                errors.append(f"[{k}] empty *IDN? response")
                self._log("WARN", errors[-1])
                continue
            if "LECROY" not in idn.upper():
                self._log("WARN", f"*IDN? response is not a LeCroy: {idn}")
            self._transport = transport
            self.connected = True
            self._log("INFO", f"LeCroy connected ({k}): {idn}")
            return
        raise RuntimeError(
            "Cannot communicate with the LeCroy:\n" + "\n".join(errors) + "\n"
            "Check: (1) the scope's Utilities → Remote control setting "
            "(default TCPIP(VICP)=port 1861 / LXI(VXI11)) and IP address "
            "(2) that the PC is on the same segment (<LAB_SUBNET>.x, subnet mask 255.255.255.0) "
            "(3) that no other PC/application holds the connection")

    def disconnect(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None
        self.connected = False
        self._log("INFO", "LeCroy disconnected")

    # ------------------------------------------------------------------
    # Low-level I/O
    # ------------------------------------------------------------------
    def write(self, cmd: str) -> None:
        with self._lock:
            self._log("TX", cmd)
            if self.simulate:
                time.sleep(0.01)
                return
            if self._transport is None:
                raise RuntimeError("LeCroy is not connected")
            self._transport.write(cmd)

    def query(self, cmd: str) -> str:
        with self._lock:
            self._log("TX", cmd)
            if self.simulate:
                time.sleep(0.01)
                resp = self._simulate_query(cmd)
            else:
                if self._transport is None:
                    raise RuntimeError("LeCroy is not connected")
                resp = self._transport.query(cmd)
            self._log("RX", resp)
            return resp

    def vbs(self, statement: str) -> None:
        """Run an Automation command
        (e.g. app.SaveRecall.Waveform.SaveFile)."""
        self.write(f"VBS '{statement}'")

    def vbs_query(self, expression: str) -> str:
        return self.query(f"VBS? 'return={expression}'")

    # ------------------------------------------------------------------
    # Screen mirroring
    # ------------------------------------------------------------------
    def get_screen_png(self) -> bytes:
        """Fetch a screen hardcopy (PNG). Used for the ~1 s mirror display.

        While mirror_paused (during auto-scan acquisition) empty bytes
        are returned and the mirror thread waits for the next cycle
        instead of raising.
        """
        if self.mirror_paused:
            return b""
        with self._lock:
            if self.simulate:
                return self._simulate_screen()
            if self._transport is None:
                raise RuntimeError("LeCroy is not connected")
            # DEST,REMOTE sends the image to the remote host instead of a file
            self._transport.write(
                "HCSU DEV,PNG,FORMAT,LANDSCAPE,BCKG,BLACK,"
                "DEST,REMOTE,PORT,NET,AREA,DSOWINDOW")
            self._transport.write("SCDP")
            return self._transport.read_raw()

    # ------------------------------------------------------------------
    # Trigger / channels / timebase
    # ------------------------------------------------------------------
    def set_trigger_mode(self, mode: str) -> None:
        """mode: AUTO / NORM / SINGLE / STOP"""
        self.write(f"TRMD {mode}")

    def set_edge_trigger(self, source: str, level: str, slope: str,
                         coupling: str | None = None) -> None:
        """Edge-trigger setup.

        source: EX / C1..C4, level: e.g. '100MV', slope: POS / NEG,
        coupling: DC / AC / HFREJ / LFREJ (None leaves it unchanged).
        The standard setup, with the laser's TRIG OUT into EXT, is
        source=EX, coupling=DC, slope=POS, level=410MV.
        """
        self.write(f"TRSE EDGE,SR,{source},HT,OFF")
        self.write(f"{source}:TRLV {level}")
        self.write(f"{source}:TRSL {slope}")
        if coupling:
            self.write(f"{source}:TRCP {coupling}")

    def set_channel(self, ch: str, on: bool | None = None,
                    vdiv: str | None = None, offset: str | None = None,
                    coupling: str | None = None) -> None:
        """Channel setup (None items are left unchanged).

        For fast AC-LGAD signals use coupling 'D50' (DC 50 Ω) to avoid
        reflections.
        """
        if on is not None:
            self.write(f"{ch}:TRA {'ON' if on else 'OFF'}")
        if vdiv:
            self.write(f"{ch}:VDIV {vdiv}")
        if offset:
            self.write(f"{ch}:OFST {offset}")
        if coupling:
            self.write(f"{ch}:CPL {coupling}")

    def set_channel_average(self, ch: str, sweeps: int) -> None:
        """Set the channel's pre-processing average (Averaging).

        Writes app.Acquisition.Cx.AverageSweeps. 1 means no averaging
        (equivalent to OFF). Reset the accumulation with clear_sweeps
        (CLSW).
        """
        self.vbs(f'app.Acquisition.{ch}.AverageSweeps = {max(1, int(sweeps))}')

    def set_averaging_off(self) -> None:
        """Turn pre-processing averaging OFF on all channels
        (AverageSweeps=1).

        With averaging left ON, the completion check of SINGLE/sequence
        acquisitions (waiting for TRMD? STOP) does not work (confirmed on
        real hardware 2026-07: real-time waveforms are required). Always
        called when the standard settings are applied in one go and at
        the start of every auto scan.
        """
        for ch in self.CHANNELS:
            self.set_channel_average(ch, 1)

    def set_tdiv(self, tdiv: str) -> None:
        self.write(f"TDIV {tdiv}")

    def set_trigger_delay(self, delay: str) -> None:
        """Horizontal offset (trigger delay).

        e.g. '-53NS' → at 1 ns/div the display covers +48 ns to +58 ns
        (standard measurement conditions; the AC-LGAD signal arrives
        about 52 ns after the laser trigger).
        """
        self.write(f"TRDL {delay}")

    def set_sequence(self, on: bool, segments: int = 200) -> None:
        """Sequence mode (fast back-to-back capture, 1 trigger =
        1 segment)."""
        if on:
            self.write(f"SEQ ON,{segments}")
        else:
            self.write("SEQ OFF")

    def clear_sweeps(self) -> None:
        """Reset accumulations such as averaging."""
        self.write("CLSW")

    def arm_single(self) -> None:
        """Arm a SINGLE trigger (with sequence mode set, captures all
        segments)."""
        self.write("TRMD SINGLE")

    def measure_pava(self, ch: str, param: str = "PKPK") -> tuple[float, str]:
        """Return value and state of a waveform-parameter measurement
        (PAVA?).

        param: PKPK / AMPL / MAX / MIN etc. (see the MAUI remote manual).
        Example response (CHDR OFF): "PKPK,206.9E-3 V,OK". If the value
        cannot be parsed, NaN is returned. The state (OK/AV/NP...) is for
        the caller to judge (NP = no waveform). Used by the Z autofocus
        amplitude readout.
        """
        resp = self.query(f"{ch}:PAVA? {param}")
        parts = [p.strip() for p in resp.split(",")]
        value = float("nan")
        for p in parts:
            token = p.split()[0] if p else ""
            try:
                value = float(token)
                break
            except ValueError:
                continue
        state = parts[-1] if parts else ""
        return value, state

    def acquisition_stopped(self) -> bool:
        """Return whether the acquisition has completed (STOP state).

        A SINGLE acquisition goes to STOP automatically once its
        segments/sweeps are done, so polling this detects completion
        (used by auto scans).
        """
        return "STOP" in self.query("TRMD?").upper()

    # ------------------------------------------------------------------
    # Waveform saving / setup saving
    # ------------------------------------------------------------------
    def ensure_directory(self, directory: str) -> None:
        """Create the save folder on the scope (including parents).

        Date-based destinations (<base>\\<year>\\<YYYYMMDD>\\scan_...)
        do not exist the first time, so dig them before configuring the
        save. Existing folders are not an error (On Error Resume Next).
        """
        parts = directory.replace("/", "\\").split("\\")
        path = parts[0]  # build one level at a time from the drive ("C:")
        for part in parts[1:]:
            if not part:
                continue
            path = f"{path}\\{part}"
            self.vbs(
                'On Error Resume Next: '
                'CreateObject("Scripting.FileSystemObject")'
                f'.CreateFolder("{path}")')

    def configure_waveform_save(self, directory: str, prefix: str,
                                source: str = "AllDisplayed",
                                fmt: str = "Binary",
                                counter: bool = True) -> None:
        """Configure waveform-file saving (to the scope's local disk).

        fmt: 'Binary' (.trc) / 'ASCII' / 'MATLAB' etc.
        source: 'AllDisplayed' or 'C1'..'C4'.
        The destination folder is created automatically if missing.
        counter: whether to use the scope's built-in sequential suffix
          (--00000).
          ★For auto scans set this to False and give the sequence number
          explicitly from the PC with save_waveform_now(title=...). On
          the real hardware the built-in counter once failed to advance
          and every point was lost to same-name overwrites (2026-09-07:
          both scans left only CX--scan--00000.trc)★
        """
        self.ensure_directory(directory)
        d = directory.replace("\\", "\\\\")
        self.vbs('app.SaveRecall.Waveform.SaveTo = "File"')
        self.vbs(f'app.SaveRecall.Waveform.WaveformDir = "{d}"')
        self.vbs(f'app.SaveRecall.Waveform.TraceTitle = "{prefix}"')
        self.vbs(f'app.SaveRecall.Waveform.WaveFormat = "{fmt}"')
        self.vbs(f'app.SaveRecall.Waveform.SaveSource = "{source}"')
        self.vbs('app.SaveRecall.Waveform.EnableCounterSuffix = '
                 f'{"True" if counter else "False"}')

    def save_waveform_now(self, title: str | None = None) -> None:
        """Save the waveform once with the current settings.

        If title is given, TraceTitle is rewritten just before saving.
        Auto scans pass an explicit sequence number per point in the
        form title="scan--00042", which makes the file name (e.g.
        C2--scan--00042.trc) deterministic on the PC side (removes the
        dependence on the scope's built-in counter).
        """
        if title is not None:
            self.vbs(f'app.SaveRecall.Waveform.TraceTitle = "{title}"')
        self.vbs("app.SaveRecall.Waveform.SaveFile")
        if self.simulate:
            self._sim_saved += 1

    def count_saved_files(self, directory: str) -> int:
        """Return the number of files in the scope-side save folder
        (for save verification).

        Used during auto scans to check, point by point, that the files
        that should have been saved actually increased. Returns -1 if
        it cannot be checked (the caller then skips the verification).
        """
        if self.simulate:
            return self._sim_saved
        try:
            resp = self.vbs_query(
                'CreateObject("Scripting.FileSystemObject")'
                f'.GetFolder("{directory}").Files.Count')
            # with CHDR OFF this is the number only, but take the last
            # token to be safe
            return int(float(resp.split()[-1]))
        except Exception as exc:
            self._log("WARN", f"failed to check the saved-file count: {exc}")
            return -1

    def set_autosave(self, mode: str) -> None:
        """Automatic save on every trigger. mode: 'Off' / 'Fill' / 'Wrap'.

        Fill: stop when the disk is full / Wrap: overwrite oldest files.
        """
        self.vbs(f'app.SaveRecall.Waveform.AutoSave = "{mode}"')

    def save_panel_setup(self, slot: int) -> None:
        """Save the front-panel setup to internal memory (1–6)."""
        self.write(f"*SAV {slot}")

    def recall_panel_setup(self, slot: int) -> None:
        self.write(f"*RCL {slot}")

    def stop_acquisition(self) -> None:
        """For emergency stop: halt acquisition and switch autosave off."""
        try:
            self.set_autosave("Off")
            self.set_trigger_mode("STOP")
        except Exception as exc:
            self._log("ERR", f"failed to stop scope: {exc}")

    # ------------------------------------------------------------------
    # Simulation
    # ------------------------------------------------------------------
    def _simulate_query(self, cmd: str) -> str:
        if cmd.startswith("*IDN?"):
            return "LECROY,WAVEPRO-SIM,00000,1.0"
        if cmd.upper().startswith("TRMD?"):
            # in simulation, acquisitions complete instantly (for scan tests)
            return "STOP"
        if "PAVA?" in cmd.upper():
            # Fake amplitude. If sim_amplitude (devices/sim_world.py) is
            # connected, return a value that depends on the stage
            # position and laser state; otherwise 200 mV ± noise as before
            import random
            if self.sim_amplitude is not None:
                amp = self.sim_amplitude(cmd.split(":")[0].strip().upper())
            else:
                amp = 0.2 + random.uniform(-0.01, 0.01)
            return f"PKPK,{amp:.4E} V,OK"
        return "0"

    def _simulate_screen(self) -> bytes:
        """Generate a fake scope screen (PNG) for GUI checks."""
        import numpy as np
        import cv2
        h, w = 480, 800
        img = np.zeros((h, w, 3), dtype=np.uint8)
        # grid (8x10 div)
        for i in range(1, 10):
            cv2.line(img, (i * w // 10, 0), (i * w // 10, h), (40, 40, 40), 1)
        for i in range(1, 8):
            cv2.line(img, (0, i * h // 8), (w, i * h // 8), (40, 40, 40), 1)
        # LGAD-like negative pulses (phase wobbles over time)
        t = np.linspace(0, 1, w)
        jitter = 0.02 * np.sin(time.monotonic() - self._sim_t0)
        colors = [(0, 255, 255), (255, 100, 255), (100, 150, 255), (100, 255, 100)]
        for k, color in enumerate(colors):
            center = 0.45 + jitter
            amp = (0.30 - 0.06 * k)
            y = -amp * np.exp(-((t - center) / 0.015) ** 2)
            ypix = (h * (0.35 - y * 1.2)).astype(int)
            pts = np.stack([np.arange(w), ypix], axis=1).reshape(-1, 1, 2)
            cv2.polylines(img, [pts], False, color, 1, cv2.LINE_AA)
        cv2.putText(img, "LeCroy WavePro (SIMULATION)", (16, 28),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (200, 200, 200), 1, cv2.LINE_AA)
        ok, buf = cv2.imencode(".png", img)
        return buf.tobytes()
