# -*- coding: utf-8 -*-
"""Serial control of the NKT Photonics PiLAS DX (controller EGI-D2-40).

Communication spec (PILAS Product Guide chapter 6, "RS232 Commands
v1.1.AEe"):
  - 19200 baud, 8 bit, 1 stop bit, no parity
  - commands are terminated with CR (\\r)
  - set commands answer "done"; invalid ones answer "invalid command!" etc.
  - queries (ld?, f? ...) answer with a human-readable single line
  - mState? returns the system state as a space-separated list of numbers
    (15 fields)

Over USB the controller appears as a virtual COM port via the FTDI VCP
driver.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional

LogFunc = Optional[Callable[[str, str], None]]

# Field definitions of the mState? response (positional). Matches the
# table in the Product Guide.
MSTATE_FIELDS = (
    "interlock",          # 1: interlock (1=on, 0=off)
    "emission_pulsed",    # 2: pulsed emission (1=on)
    "emission_cw",        # 3: CW emission (1=on)
    "trigger_source",     # 4: trigger source (0=internal, 1=ext.Adj, 2=ext.TTL)
    "trigger_edge",       # 5: trigger edge (1=rising, 0=falling)
    "tune_mode",          # 6: TUNE mode (1=auto, 0=manual)
    "freq_fine",          # 7: internal frequency mode (1=fine, 0=coarse)
    "tec_on",             # 8: laser-diode TEC (1=on)
    "temp_ok",            # 9: LD temperature state (1=ok, 0=bad)
    "trigger_level",      # 10: measured trigger level
    "head_temp_raw",      # 11: laser-head temp. (x100 °C, 2393 -> 23.93 °C)
    "ext_freq_hz",        # 12: measured external trigger frequency [Hz]
    "int_freq_hz",        # 13: internal oscillation frequency [Hz]
    "tune_permil",        # 14: TUNE value ×100 (5000 -> 50.00%). The key
                          #     says permil for historical reasons, but
                          #     the unit is 0.01%. Confirmed in a real-
                          #     hardware log: tune=0650 (65.0%) → 6500
                          #     (2026-10-01). The value converted to %
                          #     is tune_percent in get_status
    "cw_power_permil",    # 15: CW power (2200 -> 22.00%)
)

TRIGGER_SOURCE_NAMES = {0: "internal", 1: "ext. Adj", 2: "ext. TTL"}


class PilasLaser:
    """Serial wrapper for the PiLAS DX laser (thread-safe)."""

    def __init__(self, port: str, baudrate: int = 19200,
                 timeout: float = 0.4, simulate: bool = False,
                 log: LogFunc = None):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.simulate = simulate
        self._log = log or (lambda d, t: None)
        self._ser = None
        self._lock = threading.Lock()
        self.connected = False
        # Internal state for simulation (close to the hardware defaults)
        self._sim = {
            "interlock": 1, "emission_pulsed": 0, "emission_cw": 0,
            "trigger_source": 0, "trigger_edge": 1, "tune_mode": 0,
            "freq_fine": 0, "tec_on": 1, "temp_ok": 1, "trigger_level": 0,
            "head_temp_raw": 2393, "ext_freq_hz": 0,
            "int_freq_hz": 1_000_000, "tune_permil": 5000,
            "cw_power_permil": 0,
        }

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------
    def connect(self) -> None:
        if self.simulate:
            self.connected = True
            self._log("INFO", f"[SIM] PiLAS connected ({self.port})")
            return
        import serial  # lazy import: pyserial not needed in simulation
        self._ser = serial.Serial(
            self.port, self.baudrate, bytesize=8, parity="N",
            stopbits=1, timeout=self.timeout,
        )
        # The controller may leave a power-up status message in the
        # buffer, so discard it
        self._ser.reset_input_buffer()
        self.connected = True
        self._log("INFO",
                  f"PiLAS connected ({self.port}, {self.baudrate} baud)")

    def disconnect(self) -> None:
        if self._ser is not None:
            self._ser.close()
            self._ser = None
        self.connected = False
        self._log("INFO", "PiLAS disconnected")

    # ------------------------------------------------------------------
    # Low-level I/O
    # ------------------------------------------------------------------
    def send(self, cmd: str) -> str:
        """Send a command and return the response string.

        Responses can span multiple lines (query + done, etc.), so we
        keep reading until reception pauses.
        """
        cmd = cmd.strip()
        with self._lock:
            self._log("TX", cmd)
            if self.simulate:
                resp = self._simulate_response(cmd)
            else:
                if self._ser is None:
                    raise RuntimeError("PiLAS is not connected")
                self._ser.reset_input_buffer()
                self._ser.write((cmd + "\r").encode("ascii"))
                chunks = b""
                # read until the receive buffer stays empty for one timeout
                while True:
                    chunk = self._ser.read(256)
                    if not chunk:
                        break
                    chunks += chunk
                resp = chunks.decode("ascii", errors="replace").strip()
            self._log("RX", resp if resp else "(no response)")
            return resp

    # ------------------------------------------------------------------
    # High-level operations
    # ------------------------------------------------------------------
    def set_emission(self, on: bool) -> str:
        """Pulsed laser emission ON/OFF (ld=1 / ld=0)."""
        return self.send(f"ld={1 if on else 0}")

    def set_trigger_source(self, source: int) -> str:
        """Set the trigger source (0=internal, 1=ext.Adj, 2=ext.TTL)."""
        return self.send(f"ts={source}")

    def set_trigger_edge(self, rising: bool) -> str:
        return self.send(f"te={1 if rising else 0}")

    def set_trigger_level_mv(self, level_mv: int) -> str:
        """Trigger level for ext.Adj, [-4800, +4800] mV."""
        level_mv = max(-4800, min(4800, int(level_mv)))
        return self.send(f"tl={level_mv}")

    def set_frequency(self, hz: int) -> str:
        """Internal oscillation frequency [Hz] (25 Hz to max 40 MHz)."""
        hz = int(hz)
        return self.send(f"f={hz}")

    def set_tune_mode(self, auto: bool) -> str:
        return self.send(f"tm={1 if auto else 0}")

    def set_tune_percent(self, percent: float) -> str:
        """Set the TUNE value. percent=50.0 -> 'tune=0500' (0–100%)."""
        permil = max(0, min(1000, round(percent * 10)))
        return self.send(f"tune={permil:04d}")

    def get_status(self) -> dict:
        """Send mState? and return the parsed status dict.

        Extra keys: head_temp_c (converted to °C), trigger_source_name
        """
        resp = self.send("mState?")
        parts = resp.split()
        values = []
        for token in parts:
            try:
                values.append(int(token))
            except ValueError:
                continue
        if len(values) < len(MSTATE_FIELDS):
            raise RuntimeError(f"Failed to parse mState? response: {resp!r}")
        status = dict(zip(MSTATE_FIELDS, values))
        status["head_temp_c"] = status["head_temp_raw"] / 100.0
        status["tune_percent"] = status["tune_permil"] / 100.0
        status["trigger_source_name"] = TRIGGER_SOURCE_NAMES.get(
            status["trigger_source"], "?")
        return status

    def emergency_off(self) -> None:
        """Emergency stop: turn emission off."""
        try:
            self.send("ld=0")
        except Exception as exc:  # never let a stop operation raise
            self._log("ERR", f"failed to stop laser: {exc}")

    # ------------------------------------------------------------------
    # Simulated responses (same format as the hardware)
    # ------------------------------------------------------------------
    def _simulate_response(self, cmd: str) -> str:
        time.sleep(0.02)
        s = self._sim
        if cmd == "mState?":
            order = list(MSTATE_FIELDS)
            return " ".join(str(s[k]) for k in order)
        if cmd == "ld?":
            return "pulsed laser emission: " + ("on" if s["emission_pulsed"] else "off")
        if cmd == "f?":
            return f"int. frequency: {s['int_freq_hz']} Hz"
        if cmd == "ts?":
            return "trigger source: " + TRIGGER_SOURCE_NAMES.get(s["trigger_source"], "?")
        if cmd == "tune?":
            return str(s["tune_permil"] // 100)
        if cmd == "version?":
            return "PiLAS DX simulator V11AEe"
        if "=" in cmd:
            key, _, val = cmd.partition("=")
            try:
                num = int(val)
            except ValueError:
                return "data format error!"
            mapping = {
                "ld": "emission_pulsed", "ts": "trigger_source",
                "te": "trigger_edge", "tm": "tune_mode",
                "f": "int_freq_hz", "tune": "tune_permil",
                "tl": "trigger_level", "cw": "emission_cw",
                "cwl": "cw_power_permil",
            }
            if key in mapping:
                # tune= is in units of 0.1% (tune=0650 → 65.0%), while
                # mState reports in units of 0.01% (6500), so store ×10
                s[mapping[key]] = num * 10 if key == "tune" else num
                return "done"
            return "invalid command!"
        return "invalid command!"
