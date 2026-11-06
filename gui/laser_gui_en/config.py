# -*- coding: utf-8 -*-
"""Global GUI configuration.

Environment-dependent parameters (target addresses, ports, ...) are
collected here. Per-PC differences go into config_local.json (same
directory as this file) as JSON; only the listed keys are overridden.

Example: config_local.json
{
    "laser_port": "COM4",
    "stage_um_per_pulse": [0.1, 0.1, 0.1]
}
"""

from __future__ import annotations

import datetime
import json
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path


def _default_serial_port() -> str:
    # Typical serial-port name per OS. Check the actual port in Device
    # Manager (or equivalent) and override it in config_local.json.
    return "COM3" if sys.platform.startswith("win") else "/dev/ttyUSB0"


@dataclass
class GuiConfig:
    # ------------------------------------------------------------------
    # LeCroy WavePro oscilloscope (LAN, SCPI)
    # ------------------------------------------------------------------
    # Default is VICP (LeCroy's own protocol, port 1861). The scope's
    # Remote setting defaults to "TCPIP (VICP)", so this is the most
    # reliable choice (natively implemented in lecroy_scope.py; neither
    # NI-VISA nor the VICP passport is required).
    # If the scope is set to LXI (VXI-11), use
    # "TCPIP0::<SCOPE_IP>::inst0::INSTR" instead (uses pyvisa).
    # On connect, the other transport is tried automatically as fallback.
    # 2026-10-23: the lab LAN moved from <OLD_SUBNET>.x to <LAB_SUBNET>.x
    # (PC <PC_IP> / scope <SCOPE_IP> / stage <STAGE_IP>; subnet mask
    #  255.255.255.0 on every device, gateway <GATEWAY_IP>).
    scope_resource: str = "VICP::<SCOPE_IP>::INSTR"
    scope_timeout_ms: int = 10000
    # Base directory for waveform saving on the scope itself (scope-local
    # path). Actual save locations are date folders below it:
    # <base>\<year>\<YYYYMMDD>\
    #   manual saves → ...\<YYYYMMDD>\manual
    #   auto scans   → ...\<YYYYMMDD>\scan_<stamp>_<mode> (per scan)
    scope_save_dir: str = "C:\\Users\\<SCOPE_WIN_USER>\\Desktop\\data"
    scope_simulate: bool = False
    # --- Data retrieval (scope → PC) ---
    # Switched to SSH/SFTP on 2026-09-07 (devices/scope_scp.py).
    # scope_smb_user is also used as the SSH login user name (real
    # hardware: <SCOPE_USER>). The SSH password is not stored; it is entered
    # in the GUI for every transfer.
    # scope_smb_password / scope_smb_root are leftovers of the old SMB
    # method (devices/scope_files.py, currently unused).
    scope_smb_user: str = "<SCOPE_USER>"
    scope_smb_password: str = ""
    # UNC path corresponding to scope_save_dir itself. If empty, the
    # administrative share (\\<scope IP>\c$\Users\...) is used
    # automatically. On units where the administrative share is not
    # available (access denied: error 5), share the scope_save_dir folder
    # on the scope (e.g. share name "data") and set
    # "\\\\<SCOPE_IP>\\data" here.
    scope_smb_root: str = ""
    # Destination on the PC. May be relative to laser_gui (default is
    # "data" next to the repository).
    transfer_dest_dir: str = "..\\data"

    # ------------------------------------------------------------------
    # OptoSigma SHRC-203 3-axis stage controller
    # ------------------------------------------------------------------
    stage_host: str = "<STAGE_IP>"
    # SHRC-203 Ethernet port (confirmed on the lab unit 2026-07; match
    # the controller setting)
    stage_port: int = 9004
    stage_use_serial: bool = False
    stage_serial_port: str = field(default_factory=_default_serial_port)
    stage_baudrate: int = 9600
    # Travel per pulse [µm/pulse] (X, Y, Z).
    # Calibrated on the lab unit 2026-07: commanding 100 µm in the GUI
    # actually moved 200 µm (= 2 µm/pulse). Verified for X/Y.
    # ★The Z axis may be a different stage — verify on the hardware★
    stage_um_per_pulse: tuple[float, float, float] = (2.0, 2.0, 2.0)
    # Motion speed (applied to all axes via D: on connect): (start speed
    # S [pps], max speed F [pps], accel/decel time R [ms]). Trapezoidal
    # drive: long moves accelerate to F and decelerate automatically near
    # the target, so homing-to-sensor moves get faster without losing
    # stop accuracy.
    # At 2 µm/pulse, F=10000 pps = 20 mm/s.
    # ★Too high an F risks step loss (position error). Always verify the
    #  actual travel on first use★
    stage_speed_pps: tuple[int, int, int] = (1000, 10000, 200)
    stage_simulate: bool = False

    # ------------------------------------------------------------------
    # NKT Photonics PiLAS DX laser (USB virtual COM port, 19200 8N1,
    # CR-terminated)
    # ------------------------------------------------------------------
    # Measured port on the analysis PC is COM5 (confirmed on hardware
    # 2026-07; corrected from the initially reported COM4).
    laser_port: str = field(default_factory=lambda: (
        "COM5" if sys.platform.startswith("win") else "/dev/ttyUSB0"))
    laser_baudrate: int = 19200
    laser_simulate: bool = False
    # --- Laser standard conditions (sent at once by "★ Apply Standard
    #     Settings ★" on the laser panel; also the initial values of the
    #     input fields. After connecting, the fields sync to the device) ---
    # Repetition rate: 1 MHz (2026-11-01: the standard was changed from the
    # TCT-recommended 1.25 MHz. The scope horizontal offset -53 ns was also
    # determined by measurement at 1 MHz)
    laser_freq_hz: int = 1_000_000
    # TUNE: manual 50 % = MIP-equivalent output (adjacent 2ch sum midway
    # between the strips matched to the 64 mV of the DESY beam test 2026;
    # the HV dependence of 2026-10-19 was also taken at tune 50 %). "auto"
    # uses the controller's per-frequency calibration and is no longer
    # MIP-equivalent
    laser_tune_auto: bool = False
    laser_tune_percent: float = 50.0

    # ------------------------------------------------------------------
    # Standard measurement conditions (settings fixed during the 2026-07
    # AC-LGAD laser measurements)
    # ------------------------------------------------------------------
    # Scope defaults: trigger Ext/DC/positive/410 mV, vertical 50 mV/div
    # +100 mV offset (display -300 to +100 mV), horizontal 1 ns/div
    # offset -53 ns (display +48 to +58 ns), C1–C4 DC 50 Ω
    # ★Horizontal offset (changed 2026-10: -4 ns → -53 ns)★
    #   The AC-LGAD signal arrives about 52 ns after the laser trigger
    #   (measured at 1 MHz: signal at the -53 ns offset position). What
    #   was visible with the old -4 ns setting was the same signal
    #   folded back to 52 − 2×25 = +2 ns relative to the trigger two
    #   periods later at 40 MHz (period 25 ns). With -53 ns the signal
    #   falls in the same window regardless of the repetition rate
    #   (at the TCT-recommended 1.25 MHz as well as at 40 MHz).
    scope_trig_source: str = "EX"
    scope_trig_coupling: str = "DC"
    scope_trig_slope: str = "POS"
    scope_trig_level_mv: int = 410
    scope_tdiv: str = "1NS"
    scope_trig_delay: str = "-53NS"    # horizontal offset (TRDL)
    scope_vdiv: str = "50MV"
    scope_ch_offset_mv: int = 100      # vertical offset (OFST)
    scope_coupling: str = "D50"        # DC 50 Ω
    # The readout channel mapping (scope CH → sensor pad) and geometry
    # are managed by the sensor profile (sensors.py,
    # <log_dir>/sensors.json)
    # AC-LGAD bias settings (not controlled from the GUI; shown in the
    # status bar for reference)
    hv_bias_v: float = -185.0
    lv_bias_v: float = 6.0
    # Jog-button orientation: sign of the stage axis (X, Y) that moves
    # toward (right, up) in the camera image. On this setup X+ is image
    # right and Y+ is image down (= moving up means Y−). Override in
    # config_local.json if the camera or stage mounting is changed
    stage_screen_dir: tuple[int, int] = (1, -1)

    # ------------------------------------------------------------------
    # Focus camera (USB camera, OpenCV)
    # ------------------------------------------------------------------
    camera_index: int = 0
    camera_simulate: bool = False

    # ------------------------------------------------------------------
    # Log / snapshot destinations (control-PC side)
    # ------------------------------------------------------------------
    log_dir: str = "logs"
    capture_dir: str = "captures"

    def dated_save_dir(self) -> str:
        """Date-based save directory on the scope
        (<base>\\<year>\\<YYYYMMDD>)."""
        now = datetime.datetime.now()
        return f"{self.scope_save_dir}\\{now:%Y}\\{now:%Y%m%d}"

    @classmethod
    def load(cls, path: str | Path | None = None) -> "GuiConfig":
        """Return the defaults overridden with config_local.json
        (if present)."""
        cfg = cls()
        if path is None:
            path = Path(__file__).parent / "config_local.json"
        path = Path(path)
        if path.exists():
            overrides = json.loads(path.read_text(encoding="utf-8"))
            for key, value in overrides.items():
                if hasattr(cfg, key):
                    # tuple fields arrive as JSON lists; convert them back
                    if isinstance(getattr(cfg, key), tuple):
                        value = tuple(value)
                    setattr(cfg, key, value)
        cfg._warn_placeholders(path)
        return cfg

    def _warn_placeholders(self, path: Path) -> None:
        """Warn if public-release placeholders (<SCOPE_IP> etc.) are still set.

        The real IP addresses, user names, ... are not kept in the public
        repository; put them in config_local.json (not tracked by git).
        See config_local.example.json and the internal page of the wiki.
        """
        left = [k for k, v in asdict(self).items()
                if isinstance(v, str) and re.search(r"<[A-Z_]+>", v)]
        if left:
            print(f"[config] real device values are not set in {path} "
                  f"(see config_local.example.json): {', '.join(left)}",
                  file=sys.stderr, flush=True)

    def force_simulate(self) -> None:
        """Put every device into simulation mode (GUI check without
        hardware)."""
        self.scope_simulate = True
        self.stage_simulate = True
        self.laser_simulate = True
        self.camera_simulate = True
        # Use a separate destination so that simulated calibration
        # results, sensor registrations and logs do not pollute the
        # real-hardware state (logs/sensors.json etc.)
        self.log_dir = "logs_sim"

    def dump(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)
