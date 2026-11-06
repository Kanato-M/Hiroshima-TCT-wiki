# -*- coding: utf-8 -*-
"""Control of the OptoSigma SHRC-203 3-axis stage controller.

Uses SHOT-series-compatible text commands (see the SHRC-203 communication
command reference):
  H:W          mechanical home, all axes
  R:W          logical origin reset, all axes (current position becomes 0)
  M:n±Pxxxx    set relative move of axis n (in pulses) → execute with G:
  A:n±Pxxxx    set absolute position of axis n (in pulses) → execute with G:
  G:           execute the move
  L:W / L:E    decelerating stop / emergency stop
  Q:           position query → "  1000,   2000,      0,K,K,K,R" format
  !:           status query → "R" (Ready) / "B" (Busy)
  D:nSsFfRr    speed setting of axis n (S: start, F: max [pps], R: accel [ms])

Machine-coordinate tracking (added 2026-10):
  Executing R:W (logical origin reset) returns the controller coordinates
  to 0, and the position relative to the mechanical origin is lost. So
  this class tracks the "machine coordinates of the logical origin"
  (machine_offset_um):
    H:W (mechanical home) → offset = (0,0,0) (coordinates = machine
                            coordinates)
    R:W                   → offset += coordinates just before R:W
  The value is stored in the frame_path JSON and survives a GUI restart
  (valid as long as the controller stays powered). After an M-ORG from
  the JS-301 or a controller restart, redo H:W from the GUI.

Connection is Ethernet (TCP, default port 23) or RS-232C. Terminator CRLF.

Note: the pulse→µm conversion depends on the stage model and microstep
setting. Always calibrate GuiConfig.stage_um_per_pulse on the hardware.
"""

from __future__ import annotations

import datetime
import json
import socket
import threading
import time
from pathlib import Path
from typing import Callable, Optional

LogFunc = Optional[Callable[[str, str], None]]

AXIS_NAMES = ("X", "Y", "Z")  # correspond to axis numbers 1, 2, 3


class Shrc203Stage:
    """Text-command wrapper for the SHRC-203 (thread-safe)."""

    def __init__(self, host: str = "<STAGE_IP>", port: int = 9004,
                 use_serial: bool = False, serial_port: str = "COM5",
                 baudrate: int = 9600,
                 um_per_pulse: tuple[float, float, float] = (1.0, 1.0, 1.0),
                 timeout: float = 1.0, simulate: bool = False,
                 log: LogFunc = None, frame_path: str | Path | None = None):
        self.host = host
        self.port = port
        self.use_serial = use_serial
        self.serial_port = serial_port
        self.baudrate = baudrate
        self.um_per_pulse = um_per_pulse
        self.timeout = timeout
        self.simulate = simulate
        self._log = log or (lambda d, t: None)
        self._sock: Optional[socket.socket] = None
        self._ser = None
        self._rxbuf = b""
        self._lock = threading.Lock()
        self.connected = False
        # Current position [pulse] for simulation (_sim_abs holds the
        # machine coordinates, unaffected by R:W; sim_world reads it as
        # the "true position")
        self._sim_pos = [0, 0, 0]
        self._sim_abs = [0, 0, 0]
        self._sim_busy_until = 0.0
        self.sim_move_s = 0.3   # simulated duration of one move
        # Machine coordinates of the logical origin [µm] (None = unknown).
        # See the module docstring
        self.frame_path = Path(frame_path) if frame_path else None
        self.machine_offset_um: Optional[list[float]] = None
        self._homing = False
        self._load_frame()

    # ------------------------------------------------------------------
    # Machine-coordinate tracking
    # ------------------------------------------------------------------
    def _load_frame(self) -> None:
        # The simulation always starts from the mechanical origin
        # (0,0,0), so nothing is loaded
        if self.simulate or self.frame_path is None \
                or not self.frame_path.exists():
            return
        try:
            data = json.loads(self.frame_path.read_text(encoding="utf-8"))
            off = data.get("machine_offset_um")
            if off is not None and len(off) == 3:
                self.machine_offset_um = [float(v) for v in off]
        except Exception:
            self.machine_offset_um = None

    def _save_frame(self) -> None:
        if self.frame_path is None:
            return
        try:
            self.frame_path.parent.mkdir(parents=True, exist_ok=True)
            self.frame_path.write_text(json.dumps({
                "machine_offset_um": self.machine_offset_um,
                "updated_at": datetime.datetime.now().isoformat(
                    timespec="seconds"),
            }, indent=2), encoding="utf-8")
        except Exception as exc:
            self._log("WARN",
                      f"failed to save the machine-coordinate offset: {exc}")

    def machine_from_logical(self, pos_um) -> Optional[list[float]]:
        """Logical coordinates → machine coordinates [µm]
        (None if not tracked)."""
        if self.machine_offset_um is None:
            return None
        return [o + p for o, p in zip(self.machine_offset_um, pos_um)]

    def logical_from_machine(self, pos_um) -> Optional[list[float]]:
        """Machine coordinates → current logical coordinates [µm]
        (None if not tracked)."""
        if self.machine_offset_um is None:
            return None
        return [p - o for o, p in zip(self.machine_offset_um, pos_um)]

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------
    def connect(self) -> None:
        if self.simulate:
            self.connected = True
            self._log("INFO", "[SIM] SHRC-203 connected")
            return
        if self.use_serial:
            import serial
            self._ser = serial.Serial(
                self.serial_port, self.baudrate, bytesize=8, parity="N",
                stopbits=1, timeout=self.timeout, rtscts=True,
            )
            self._log("INFO",
                      f"SHRC-203 connected (RS-232C {self.serial_port})")
        else:
            hints = (
                "If ping works but the connection fails, check: "
                "(1) the port number in the controller's Ethernet menu "
                "(you can enter <STAGE_IP>:<port> in the address field; "
                "try 10001 for XPort-based converters) "
                "(2) that Ethernet (remote) mode is enabled on the unit "
                "(3) that no other PC / previous session holds the "
                "connection (TCP allows a single connection; restart the "
                "controller to release it) "
                "(4) if even ping fails: check the unit's IP / subnet mask / "
                "gateway (<STAGE_IP> / 255.255.255.0 / <GATEWAY_IP>) and the "
                "PC's subnet mask (255.255.255.0), and power-cycle the unit "
                "after changing its Ethernet settings ")
            try:
                # The controller's TCP stack can respond slowly, so allow
                # at least 5 s for connection establishment, independent
                # of the I/O timeout
                self._sock = socket.create_connection(
                    (self.host, self.port),
                    timeout=max(self.timeout, 5.0))
            except socket.timeout:
                raise RuntimeError(
                    f"TCP connection to the SHRC-203 timed out "
                    f"({self.host}:{self.port}). Port {self.port} is not "
                    f"responding. {hints}")
            except OSError as exc:
                raise RuntimeError(
                    f"TCP connection to the SHRC-203 failed "
                    f"({self.host}:{self.port}): {exc}. {hints}")
            self._sock.settimeout(self.timeout)
            self._log("INFO",
                      f"SHRC-203 connected (TCP {self.host}:{self.port})")
        self.connected = True
        # Sanity check: does the status query answer? (Even with TCP up,
        # a wrong command mode or delimiter yields silence, i.e.
        # "connected but unusable".)
        resp = self.send("!:")
        if not resp:
            self.disconnect()
            raise RuntimeError(
                "TCP connection to the SHRC-203 succeeded, but the status "
                "query (!:) got no response. Check the controller's "
                "communication settings: (1) command mode is SHOT-"
                "compatible (2) delimiter is CRLF (3) the port is the "
                "control port (you may be connected to the telnet setup "
                "port)")

    def disconnect(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None
        if self._ser is not None:
            self._ser.close()
            self._ser = None
        self.connected = False
        self._log("INFO", "SHRC-203 disconnected")

    # ------------------------------------------------------------------
    # Low-level I/O
    # ------------------------------------------------------------------
    def send(self, cmd: str) -> str:
        """Send a command and return one line of response (up to CRLF).

        In SHOT-compatible mode, set commands answer "OK"/"NG". Some
        settings produce no response at all, so a timeout returns an
        empty string.
        """
        cmd = cmd.strip()
        with self._lock:
            self._log("TX", cmd)
            if self.simulate:
                resp = self._simulate_response(cmd)
            else:
                data = (cmd + "\r\n").encode("ascii")
                if self._ser is not None:
                    self._ser.reset_input_buffer()
                    self._ser.write(data)
                    raw = self._ser.read_until(b"\r\n")
                    resp = raw.decode("ascii", errors="replace").strip()
                elif self._sock is not None:
                    # A late response left over from a previous command
                    # shifts every following response by one (observed in
                    # a real-hardware log: A:W receiving the answer of
                    # Q:). Discard anything already received before
                    # sending to re-synchronize.
                    self._rxbuf = b""
                    try:
                        self._sock.settimeout(0.0)
                        while self._sock.recv(4096):
                            pass
                    except (BlockingIOError, TimeoutError,
                            socket.timeout, OSError):
                        pass
                    finally:
                        self._sock.settimeout(self.timeout)
                    self._sock.sendall(data)
                    resp = self._read_line_socket()
                else:
                    raise RuntimeError("SHRC-203 is not connected")
            self._log("RX", resp if resp else "(no response)")
            return resp

    @staticmethod
    def _strip_telnet_iac(data: bytes) -> bytes:
        """Remove telnet IAC control sequences (0xFF + 2 bytes).

        If port 23 happens to be a telnet server, option-negotiation
        bytes arrive right after connecting and would break response
        parsing, so strip them.
        """
        out = bytearray()
        i = 0
        while i < len(data):
            if data[i] == 0xFF and i + 2 < len(data):
                i += 3
            elif data[i] == 0xFF:
                break  # mid-sequence: the rest arrives with the next recv
            else:
                out.append(data[i])
                i += 1
        return bytes(out)

    def _read_line_socket(self) -> str:
        """Read one non-empty line from the socket (returns what arrived
        on timeout).

        CRLF is the standard terminator, but depending on the device
        settings it may be CR only or LF only, so any of them ends a line
        (avoids timing out on every read while waiting for CRLF).

        Empty lines are skipped: when the CR and the LF of the previous
        response arrive in separate packets, the lone LF slips past the
        pre-send flush, arrives before the next response and used to be
        returned immediately as an "empty line" (2026-10-08: during a 2D
        scan, the moment the GUI's position polling and the worker's Q:
        ran back to back, the scan stopped with "Q: no response" after
        0 ms).
        """
        deadline = time.monotonic() + self.timeout
        while True:
            # Drop leftovers of the previous response (e.g. the LF after a
            # CR-only terminator) and delayed LFs
            self._rxbuf = self._rxbuf.lstrip(b"\r\n")
            for term in (b"\r\n", b"\r", b"\n"):
                if term in self._rxbuf:
                    line, _, self._rxbuf = self._rxbuf.partition(term)
                    if line.strip():
                        return line.decode("ascii", errors="replace").strip()
                    break      # empty line: discard and keep reading
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                self._sock.settimeout(max(0.05, remaining))
                chunk = self._sock.recv(4096)
            except socket.timeout:
                break
            finally:
                self._sock.settimeout(self.timeout)
            if not chunk:
                break
            self._rxbuf += self._strip_telnet_iac(chunk)
        line, self._rxbuf = self._rxbuf, b""
        return line.decode("ascii", errors="replace").strip()

    # ------------------------------------------------------------------
    # Coordinate conversion
    # ------------------------------------------------------------------
    def um_to_pulse(self, axis: int, um: float) -> int:
        """µm → pulse conversion (axis: 0=X, 1=Y, 2=Z)."""
        return round(um / self.um_per_pulse[axis])

    def pulse_to_um(self, axis: int, pulse: int) -> float:
        return pulse * self.um_per_pulse[axis]

    # ------------------------------------------------------------------
    # High-level operations
    # ------------------------------------------------------------------
    def home_all(self) -> str:
        """Mechanical home of all axes (mandatory right after power-up;
        equivalent to M-ORG on the JS-301).

        On completion the controller coordinates = machine coordinates
        (offset 0). If the homing motion is stopped midway, tracking is
        dropped to unknown (None).
        """
        resp = self.send("H:W")
        self._homing = True
        self.machine_offset_um = [0.0, 0.0, 0.0]
        self._save_frame()
        return resp

    def zero_all(self) -> str:
        """Reset the current position to the logical origin (0).

        Reads the coordinates just before R:W and accumulates them into
        the machine coordinates of the logical origin (machine_offset_um)
        (only while the machine coordinates are being tracked).
        """
        pos = None
        if self.machine_offset_um is not None:
            try:
                pos, _ = self.get_position_um()
            except Exception:
                pos = None
        resp = self.send("R:W")
        if pos is not None:
            self.machine_offset_um = [
                o + p for o, p in zip(self.machine_offset_um, pos)]
        else:
            self.machine_offset_um = None
        self._save_frame()
        return resp

    def move_relative_um(self, axis: int, um: float) -> str:
        """Move axis (0=X, 1=Y, 2=Z) relatively by um and execute."""
        pulses = self.um_to_pulse(axis, um)
        return self.move_relative_pulse(axis, pulses)

    def move_relative_pulse(self, axis: int, pulses: int) -> str:
        sign = "+" if pulses >= 0 else "-"
        self.send(f"M:{axis + 1}{sign}P{abs(pulses)}")
        return self.send("G:")

    def move_absolute_um(self, x_um: float, y_um: float, z_um: float) -> str:
        """Simultaneous absolute move of all 3 axes (in µm)."""
        parts = []
        for i, um in enumerate((x_um, y_um, z_um)):
            p = self.um_to_pulse(i, um)
            sign = "+" if p >= 0 else "-"
            parts.append(f"{sign}P{abs(p)}")
        self.send("A:W" + "".join(parts))
        return self.send("G:")

    def move_to_um_relative(self, x_um: float, y_um: float, z_um: float) -> str:
        """Reach the target position (µm, logical coordinates) via a
        relative move (M:W).

        Reads the current position with Q: and moves all axes at once by
        the difference to the target pulses. Auto scans use this instead
        of the absolute move (A:W) (2026-07 hardware policy: fix the
        logical origin — e.g. C1 edge = 0,0 — with R:W beforehand, then
        relative moves work without issues). The difference is computed
        from the actual position every time, so rounding errors do not
        accumulate.
        If called during a move, the difference would be computed from
        an intermediate position, so it raises while Busy (guards
        against rapid repeated manual operation).
        """
        cur, busy = self.get_position_pulse()
        if busy:
            raise RuntimeError("The stage is moving. Stop it, then try "
                               "again")
        parts = []
        for i, um in enumerate((x_um, y_um, z_um)):
            d = self.um_to_pulse(i, um) - cur[i]
            sign = "+" if d >= 0 else "-"
            parts.append(f"{sign}P{abs(d)}")
        self.send("M:W" + "".join(parts))
        return self.send("G:")

    def _homing_interrupted(self) -> None:
        # Stopping in the middle of homing leaves the coordinates not
        # referenced to the mechanical origin
        if self._homing:
            self._homing = False
            self.machine_offset_um = None
            self._save_frame()

    def stop(self) -> str:
        """Decelerating stop of all axes."""
        self._homing_interrupted()
        return self.send("L:W")

    def emergency_stop(self) -> None:
        """Emergency stop (immediate). Never raises."""
        self._homing_interrupted()
        try:
            self.send("L:E")
        except Exception as exc:
            self._log("ERR", f"stage emergency stop failed: {exc}")

    def set_speed(self, slow_pps: int, fast_pps: int, accel_ms: int) -> None:
        """Apply the same speed parameters to all axes."""
        for n in (1, 2, 3):
            self.send(f"D:{n}S{slow_pps}F{fast_pps}R{accel_ms}")

    def get_position_pulse(self) -> tuple[list[int], bool]:
        """Return the current position [pulse] and the Busy state.

        Example Q: response: "   1000,   -200,      0,K,K,K,R"
        The trailing R/B means Ready/Busy.
        """
        # On no response, or when a delayed reply of another command
        # ("OK" etc.) is mixed in, re-read a few times (so that a brief
        # communication hiccup does not stop a long scan)
        last_err = "no response"
        for attempt in range(3):
            resp = self.send("Q:")
            fields = [f.strip() for f in resp.split(",")]
            try:
                if not resp:
                    raise ValueError("no response")
                positions = []
                for f in fields[:3]:
                    # handle whitespace between sign and digits ("-   200")
                    positions.append(int(f.replace(" ", "")))
                if len(fields) < 4:
                    raise ValueError("malformed")
                break
            except ValueError as exc:
                last_err = f"{exc}: {resp!r}" if resp else "no response"
                self._log("WARN", f"Invalid Q: response ({last_err}), "
                                  f"retry {attempt + 1}/3")
                time.sleep(0.1)
        else:
            raise RuntimeError(f"Q: {last_err}")
        busy = any(f == "B" for f in fields[3:])
        if not busy:
            self._homing = False   # mechanical home has completed
        return positions, busy

    def get_position_um(self) -> tuple[list[float], bool]:
        pulses, busy = self.get_position_pulse()
        return [self.pulse_to_um(i, p) for i, p in enumerate(pulses)], busy

    def is_busy(self) -> bool:
        """True while Busy. Returns False only when the reply is "R" (Ready).

        Treating no response, or a delayed reply of another command (e.g.
        the coordinate line of Q:), as Ready would start the acquisition
        before the move has finished, so anything that is not an explicit
        Ready counts as Busy (wait_ready re-checks on the next cycle).
        """
        resp = self.send("!:")
        return not resp.startswith("R")

    def wait_ready(self, timeout: float = 60.0, poll_s: float = 0.1,
                   abort_check=None) -> None:
        """Block until the move completes (Ready). Used by auto scans.

        Pass abort_check: callable() -> bool to abort immediately when it
        returns True.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if abort_check is not None and abort_check():
                return
            if not self.is_busy():
                return
            time.sleep(poll_s)
        raise TimeoutError(
            f"Stage move did not complete within {timeout} s")

    # ------------------------------------------------------------------
    # Simulated responses
    # ------------------------------------------------------------------
    def _simulate_response(self, cmd: str) -> str:
        time.sleep(0.01)
        busy = time.monotonic() < self._sim_busy_until

        if cmd == "Q:":
            state = "B" if busy else "R"
            pos = ",".join(f"{p:10d}" for p in self._sim_pos)
            return f"{pos},K,K,K,{state}"
        if cmd == "!:":
            return "B" if busy else "R"
        if cmd.startswith("M:"):
            self._sim_pending = ("M", cmd[2:])
            return "OK"
        if cmd.startswith("A:"):
            self._sim_pending = ("A", cmd[2:])
            return "OK"
        if cmd == "G:":
            kind, spec = getattr(self, "_sim_pending", (None, ""))
            if kind:
                self._sim_apply_move(kind, spec)
                self._sim_busy_until = time.monotonic() + self.sim_move_s
            return "OK"
        if cmd == "H:W":
            self._sim_pos = [0, 0, 0]
            self._sim_abs = [0, 0, 0]
            self._sim_busy_until = time.monotonic() + 3 * self.sim_move_s
            return "OK"
        if cmd == "R:W":
            self._sim_pos = [0, 0, 0]
            return "OK"
        if cmd.startswith(("L:", "D:")):
            return "OK"
        return "NG"

    def _sim_apply_move(self, kind: str, spec: str) -> None:
        """Parse an M:/A: command string and update the simulated
        position."""
        import re
        if spec.startswith("W"):
            # all-axis form: A:W+P100-P200+P0
            terms = [(i, sign, num) for i, (sign, num) in
                     enumerate(re.findall(r"([+-])P(\d+)", spec)[:3])]
        else:
            m = re.match(r"(\d)([+-])P(\d+)", spec)
            terms = [(int(m.group(1)) - 1, m.group(2), m.group(3))] if m \
                else []
        for axis, sign, num in terms:
            val = int(num) * (1 if sign == "+" else -1)
            delta = val - self._sim_pos[axis] if kind == "A" else val
            self._sim_pos[axis] += delta
            self._sim_abs[axis] += delta
