# Control PC and software

All measurements are done with the GUI **laser_gui** (AC-LGAD Laser Scan Control GUI) on the control PC.
This page explains how to install the GUI, the initial settings on the device side, and how to start it.

## Control PC

| Item | Details |
| --- | --- |
| User name | `epic` (ask the contact person for the password) |
| GUI folders | `~/workspace/laser_gui_v4/` (Japanese UI version), `~/workspace/laser_gui_v4_en/` (English UI version) |
| Where to get the code | The latest version will be published on GitHub (not yet public) |

!!! question "To be confirmed"
    The Python environment in use (Anaconda / venv, etc.) and the usual way to start the GUI (command prompt, shortcut, etc.).

## GUI versions

| Folder | Content |
| --- | --- |
| `laser_gui_v4` | Japanese UI version |
| `laser_gui_v4_en` | English UI version. Same basic functions and numbers; the on-screen text is in English. **Use this version if you do not read Japanese** |

!!! warning "TUNE optimization in the English UI version is the older spec"
    In the code at hand as of 2026-10, TUNE optimization in the English UI version lacks the "method" selector ("Single CH" / "Adjacent 2-ch sum"), the tolerance and the fine sweep;
    it only does a coarse sweep on a single CH ([TUNE optimization](../measurement/tune.md)). To match the adjacent 2-ch sum (MIP-equivalent), use the Japanese UI version.

The screenshots in this wiki were taken in simulation mode when the analysis note matome was written (2026-10 revision).
Some parts of the display (such as the "method" field of TUNE optimization) differ from the current version. Screenshots of the current version will be added later.

## Required software

On the Windows control PC (Python 3.10 or later), go to the GUI folder and install the dependencies (first time only).

```bat
pip install -r requirements.txt
```

Contents of `requirements.txt`:

| Package | Purpose |
| --- | --- |
| PySide6 | GUI framework |
| pyvisa, pyvisa-py | Oscilloscope control (when using VXI-11) |
| pyserial | PiLAS DX (USB virtual COM), SHRC-203 (when using RS-232C) |
| opencv-python | Focus camera |
| numpy | Numerics |
| paramiko | Data retrieval (scope → PC, SSH/SFTP) |
| matplotlib, uproot | Quick analysis (figure output, `.trc` → ROOT conversion) |

!!! tip "NI-VISA is not needed"
    The LeCroy VICP protocol is implemented in pure Python in `devices/lecroy_scope.py`,
    so neither NI-VISA nor the LeCroy VICP passport is needed.

## Initial device settings (first time only)

These settings are needed only when the setup is newly assembled or a device is replaced. For normal measurements they are already done.

1. **PiLAS DX**: the USB connection requires the FTDI VCP driver (PiLAS Product Guide section 3.3).
   Check the COM number in Device Manager (**COM5** on the control PC).
2. **LeCroy oscilloscope**: check the control method under Utilities → Utilities Setup → Remote on the scope.
    - **TCPIP (VICP)** (default): `VICP::192.168.0.2::INSTR`
    - **LXI (VXI11)**: `TCPIP0::192.168.0.2::inst0::INSTR`

    Either way, the GUI falls back to the other one automatically on connect. The IP is 192.168.0.2.
3. **LeCroy SSH server**: data retrieval connects to the OpenSSH server (sshd) on the scope's Windows.
   Already set up as of 2026-09.
4. **SHRC-203**: enable Ethernet in the controller menu and set the IP to 192.168.0.3. Use the SHOT-compatible command mode,
   and match the port number to the controller setting (**9004** on the real hardware).
5. **Control PC**: set the IP address to **192.168.0.1**.

## Per-PC settings `config_local.json` { #config }

Settings that differ per PC, such as the COM port number or the save folder on the scope, go into
`config_local.json` in the same folder instead of editing `config.py`. Only the keys you write are overridden.

```json
{
  "laser_port": "COM5",
  "stage_um_per_pulse": [2.0, 2.0, 2.0],
  "stage_speed_pps": [1000, 10000, 200],
  "scope_save_dir": "C:\\Users\\LeCroyUser\\Desktop\\data",
  "scope_smb_user": "lcrydmin",
  "transfer_dest_dir": "..\\data"
}
```

| Key | Default | Content |
| --- | --- | --- |
| `scope_resource` | `VICP::192.168.0.2::INSTR` | Scope address |
| `scope_save_dir` | `C:\Users\LeCroyUser\Desktop\data` | Base folder for waveforms on the scope |
| `scope_smb_user` | `lcrydmin` | SSH user name for data retrieval |
| `transfer_dest_dir` | `..\data` | Destination on the PC (may be relative to the GUI folder) |
| `stage_host` / `stage_port` | `192.168.0.3` / `9004` | Stage address |
| `stage_um_per_pulse` | `[2.0, 2.0, 2.0]` | Travel per pulse [µm] (X, Y, Z). **Z unconfirmed** |
| `stage_speed_pps` | `[1000, 10000, 200]` | Start speed [pps], max speed [pps], acceleration time [ms]. Setting them too high risks losing steps |
| `laser_port` | `COM5` | Laser COM port |
| `camera_index` | `0` | Camera device index |
| `scope_trig_delay` | `-53NS` | Standard horizontal offset |
| `stage_screen_dir` | `[1, -1]` | Jog direction: signs of the stage axes that move toward (right, up) in the camera image. Fix this if you change the camera or stage orientation |

!!! warning "Do not write the password in a file"
    The SSH password for data retrieval is not stored; enter it in the GUI for each transfer. Ask the setup administrator for the password.

## Starting the GUI

The folder `logs/` for logs and state files is **relative to the directory you start from**, so always start in the GUI folder.

```bat
cd %USERPROFILE%\workspace\laser_gui_v4_en
python main.py              :: real hardware mode
python main.py --simulate   :: simulate all devices
```

On startup, the window opens sized to fit the usable area of the monitor. For the screen layout, see [GUI overview](../measurement/gui-overview.md).

## Practicing in simulation mode { #simulate }

Starting with `python main.py --simulate` turns every device into a simulated one, and the window title shows `[SIMULATION MODE]`.

- It has a simulated sensor response (`devices/sim_world.py`) that reproduces zero signal on the electrodes, minimum amplitude at focus, a tilted sensor and TUNE dependence.
- You can run origin finding, full calibration, Z focus, TUNE optimization and scans all the way through.
- State files are saved in `logs_sim/`; the real-hardware `logs/` is not touched.

Practice in this order:

1. Press [Connect] on each panel ([Start] for the camera).
2. [Mechanical Home (H:W)] on the [Move] tab of the stage panel.
3. [Go to Start] on the [Positions] tab.
4. [Emission ON (ld=1)] on the laser panel.
5. [Find Origin (Z→Y→X→R:W)] on the [Alignment] tab.
6. Run a 1D position scan on the auto scan panel.

We recommend going through the following [Measurement overview](../measurement/procedure.md) once in simulation mode first.

## Folder structure

```text
laser_gui_v4/
├── main.py              # entry point
├── config.py            # settings (overridden by config_local.json)
├── sensors.py           # registry of sensor / DUT profiles
├── scan_geometry.py     # measured coordinates and tilt coefficients of the standard sensor (2026-08-24)
├── devices/             # device drivers (simulate mode built in)
│   ├── pilas_laser.py   #   PiLAS DX
│   ├── shrc203_stage.py #   SHRC-203 + machine coordinate tracking
│   ├── lecroy_scope.py  #   LeCroy (SCPI + VBS, screen capture)
│   ├── scope_scp.py     #   data retrieval (SSH/SFTP)
│   ├── focus_camera.py  #   USB camera
│   └── sim_world.py     #   simulated sensor response for --simulate
├── gui/                 # PySide6 panels
├── logs/                # communication log, scan records, state files
├── captures/            # camera snapshots
└── analysis/            # quick analysis of acquired data (run_analysis.py)
```

The retrieved data (`..\data`) and analysis results (`..\results`) on the PC are placed **outside** (next to) the GUI folder,
so that the Japanese and English GUIs can share the data. For details, see [Output files](../reference/files.md).

Retrieved data are backed up to the NAS ([Data retrieval and quick analysis](../measurement/data.md#backup)).

## Measurement log

Every command sent to a device and its response are shown in the communication log at the bottom left of the GUI, and are also kept in `logs/gui_YYYYMMDD.log`.
When something goes wrong, look at this file first.
