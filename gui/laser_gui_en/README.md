# AC-LGAD Laser Scan Control GUI (`laser_gui_v4_en`, edition of 2026-11-01)

> **About this public copy**: in this repository the device IP addresses, user names, etc. are
> replaced by placeholders such as `<SCOPE_IP>` / `<STAGE_IP>` / `<PC_IP>` / `<GATEWAY_IP>` /
> `<SCOPE_USER>` / `<SCOPE_WIN_USER>`. To use the GUI on the real setup, copy
> `config_local.example.json` to `config_local.json` and fill in the real values
> (they are on the internal page of the Hiroshima TCT Wiki). `config_local.json` is not tracked by git.

English edition of `laser_gui_v4` (same functionality and numerical values; UI text, messages, comments and this README are in English).

Front-end GUI for operating the AC-LGAD TCT laser-scan evaluation system
**without entering the dark room**. After powering up the instruments,
this GUI alone covers the full workflow:
"position the irradiation point → fire the laser → inspect & save
waveforms".

Supported instruments (based on the specifications and wiring diagrams in
`guide/`):

| Panel | Instrument | Connection | Default address |
|---|---|---|---|
| Oscilloscope | Teledyne LeCroy WavePro | LAN (native VICP / VXI-11) | `VICP::<SCOPE_IP>::INSTR` |
| Stage | OptoSigma SHRC-203 (3-axis) | LAN (TCP) or RS-232C | `<STAGE_IP>:9004` |
| Laser | NKT Photonics PiLAS DX (EGI-D2-40) | USB virtual COM (19200 8N1, CR-terminated) | `COM5` (verified on the analysis PC) |
| Camera | Focus camera (USB) | OpenCV (DirectShow preferred on Windows) | device 0 |

The GUI uses a 3-column layout for 16:9 wide monitors (analysis PC:
EX-LDHU431DB) — left: scope + Communication Log / middle: camera + stage /
right: laser + scan. On startup it automatically fits into the monitor's
available area.

## Key points of the 2026-11-01 revision (laser standard conditions, manual material)

| Item | Change |
|---|---|
| **Laser standard conditions** | The standard repetition rate is now **1 MHz** (was the TCT-recommended 1.25 MHz). **TUNE manual 50 % = MIP-equivalent output** is the standard; both are registered as `laser_freq_hz` / `laser_tune_auto` / `laser_tune_percent` in `config.py`. They are the initial values of the laser panel's input fields (synced to the device after connecting) and are sent by the new **"★ Apply Standard Settings (1 MHz / TUNE manual 50% = MIP equiv.) ★"** button (trigger internal + rate + TUNE at once; counterpart of the scope button of the same name). The rate preset reads "1 MHz (standard)" |
| Unification with the Japanese edition | This edition was aligned with `laser_gui_v4` (same functionality and defaults; details such as the sensor dialog minimum width of 600 px and the stage move-button widths were unified too). Future changes go into both editions at the same time |
| Screenshots for the manual | `docs/make_screenshots.py` connects all devices in simulation and writes the whole window, the toolbar, every panel, tab, group box and the sensor dialog to individual PNG files (`docs/screenshots/`, list in `INDEX.md`) |

## Key points of the 2026-10-16 revision (v4: pixel sensors, strip analysis pipeline)

| Item | Change |
|---|---|
| **Sensor type** | The sensor editor has a "Type" (Strip / Pixel). A pixel sensor registers the pixel pitch, the bonding-pad size and CH ↔ pixel (column, row). **Logical origin = the +X/+Y corner of the reference CH's pad** (same coordinates as the pixel#3 scan of 2026-10-15: pad center = pixel center = (−50, −50), C2 = −X neighbor, C3 = −Y neighbor, 4-pixel corner (−300, −300)). The default scan range (600 × 600 µm from the pad corner in 10 µm steps), "Go to pixel" (beside pad / pad center / 4-pixel corner) and the adjacent-channel pairs and midpoints of the TUNE optimization follow from this geometry. A `pixel_...` sensor registered with an older version is migrated to the pixel type at start-up, and the template `pixel_2x2_500` is added |
| **Find Origin / Full Calibration for pixels** | Find Origin: Y dip of the pad (signal-free square) → Z (minimum amplitude) on the silicon beside the pad → X dip → R:W at the pad corner, with fine finishing + verification. Full Calibration: the same measurement on the pad centers of the other readout pixels, then a plane fit (dZ/dX, dZ/dY), dY/dX from the pads in the reference row and a pitch check (the lever arm is one pitch, so the coefficients are a coarse guide). Verified within one pulse on a simulated sensor; not yet run on hardware |
| **Scan log** | The header records `sensor_type=pixel`, and the analysis tab automatically selects the `pixel` analysis (scan, scope and camera operation are unchanged) |
| **Paper figures of the strip analysis** | With "Also run the template-fit analysis" (default ON) for 2dYX in the analysis tab, `macro\template_timing\tt_pipeline.py` fits the pulse template directly on the .trc files → time resolution (optimal method per position), position resolution of the uncalibrated amplitude centroid, amplitude distributions, rise time, jitter and propagation: the paper figures 01–09 (same as `results\paper_plots_20261013`) in `results\<scan>\paper_plots\` and `paper_NN_*.png` (no ROOT conversion; about 30 min for 16000 points, minutes afterwards; `key_numbers.csv` reproduces the values of 2026-10-13 exactly) |
| **Pixel analysis** | Kind `pixel` (`analysis\analyze_pixel.py` → `macro\template_timing\tt_pixel.py`): amplitude maps, time / position resolution of 2/3/4-pixel combinations, rise time, jitter, propagation (pix01–06). Details of the analysis package in `analysis\README.md` |

## Key points of the 2026-10 revision (v2)

| Item | Change |
|---|---|
| Sensor / DUT | Switched with the toolbar "Sensor" selector. Geometry, readout mapping, default scan ranges, tilt coefficients, saved origin and saved positions are managed in a **per-sensor dictionary** (`logs/sensors.json`). "Edit..." registers, duplicates and deletes sensors from the GUI |
| Automatic origin registration | Every Find Origin run automatically saves the **machine coordinates of the logical origin** to that sensor. Next time, "Go to Origin / Go to Start" does the coarse approach (machine coordinates are tracked across R:W) |
| Fine finishing + verification of Find Origin (2026-10-05) | After the coarse search, **fine sweeps in one-pulse (2 µm) steps with many reads (median)** determine the electrode center Y and the signal edge X by erf fits and the focus Z by the minimum of a Lorentzian fit curve; **after R:W the origin is re-measured and residuals of one pulse or more are corrected** (at most twice). A reverse sweep estimates the backlash. ON by default ("Fine finishing + verification" check box). Sweeps are logged to `logs/alignment_<stamp>.csv` |
| Z tracking | Choose from 3 methods: calibration coefficients (focus at the logical origin) / calibration coefficients (focus at the current position) / **linear interpolation of measured Z (start and end points; four corners in 2D)** |
| Screen layout | Find Origin, Full Calibration and Z Focus moved to the [Alignment] tab of the **stage panel**; TUNE optimization moved to the **laser panel**. Progress and abort are consolidated in the toolbar |
| STOP ALL | Moved to the **right end** of the toolbar (prevents stopping a scan by mis-clicking the [Control]/[Analysis] tabs) |
| Jog | Buttons are laid out to match the **orientation of the camera image** (pressing ▲ moves up in the image = stage Y−) |
| Camera | Crosshair, gain, snapshot and device number only (AF / focus / exposure / sharpness were removed) |
| Scope horizontal axis | The standard horizontal offset was changed to **−53 ns** (the signal arrives about 52 ns after the laser trigger; it falls in the same window regardless of the frequency) |
| Display bugs | Fixed the right column widening horizontally after Find Origin / Full Calibration, and the tilt-coefficient display in the scan panel staying stale after a calibration |
| Usability | The mouse wheel no longer changes values over spin boxes / combo boxes. Manual jog etc. are disabled while a task is running |
| Bug fixes | Fixed the conversion that made the TUNE display in the laser status 10× too large (65% → 650%) (when a TUNE optimization was aborted, TUNE did not return to the original value and became 100%). Wait times and timeouts are measured with a monotonic clock (prevents false timeouts caused by OS time synchronization) |

## Standard Measurement Conditions (fixed by 2026-07 measurements)

Registered as defaults in `config.py`. The scope panel's
"★ Apply Standard Settings ★" button sends the scope settings below in
one go.

- **Scope trigger**: Ext, DC, positive, 410 mV
- **Vertical**: 50 mV/div, offset +100 mV (display range -300 to +100 mV)
- **Horizontal**: 1 ns/div, offset **-53 ns** (display range +48 to +58 ns).
  The AC-LGAD signal arrives about 52 ns after the laser trigger
  (measured 2026-10 at 1 MHz). What was visible with the old -4 ns
  setting was the same signal relative to the trigger two periods later
  at 40 MHz (period 25 ns), folded back to 52 − 50 = +2 ns. With -53 ns
  the signal falls in the same window at any repetition rate, including
  the 1.25 MHz recommended for TCT (`scope_trig_delay` in `config.py`)
- **Channels**: C1–C4, DC 50 Ω
- **AC-LGAD bias** (not GUI-controlled; shown in the status bar):
  HV = -185 V, LV = 6.0 V
- **Readout channel mapping** (standard sensor): C1=J8, C2=J7, C3=J6, C4=J5
  (set per sensor profile; see "Registering sensors / DUTs" below)
- **Strip positions** (standard sensor, measured 2026-08-24 = after the
  jig upgrade, recorded in logical coordinates [µm]; machine coordinates
  are reconstructed by adding the machine coordinates of the logical
  origin. Details in `scan_geometry.py`. These become the initial values
  of the standard sensor's profile):
  - **X=0 = sensor frame (GND) edge** (definition changed 2026-09-07.
    Breakdown from the "Strip detail" slide: ⓪0.052 + ①0.380 + ②0.200
    (pad) + ③9.318 (exposed strip) + ④0.098 (end gap) mm).
    The exposed strip region spans logical X = −632 to −9950 µm, and
    **−10 mm from the origin lands at the center of gap ④ (the boundary
    between the two strip regions)**.
    Machine coordinates of the logical origin (the 2026-08-24
    measurement converted by +632 µm):
    **(108932, 100050, 1414)**
  - Y (across strips): C0 edge 100550 → C5 edge 98050 (pitch exactly
    500 µm). Readout is C1–C4; C0/C5 are the non-readout strips outside
    them
  - X (along the strip, readout-side end of the exposed region → far
    side): 108300 → 98982
  - Z (laser focus): 1406–1416 (position-dependent due to sensor tilt)
  - **Sensor tilt** (plane fit, residual ±1.6 µm): focal plane
    dZ/dX = −0.02 (essentially flat), dZ/dY = +3.79 µm/mm / in-plane
    rotation dY/dX = +7.7 µm/mm (Y drifts ~72 µm over the full strip
    length). Auto scans correct this tilt automatically via relative
    moves (see below).
  - Absolute coordinates can change after every M-ORG, so they are only
    a guide. Scans use relative moves referenced to the logical origin
    and do not depend on absolute coordinates.
- **Laser** (standardized 2026-11-01): trigger **internal**, repetition
  rate **1 MHz** (changed from the former standard, the TCT-recommended
  1.25 MHz; the horizontal offset −53 ns was measured at 1 MHz as well),
  TUNE **manual 50 % = MIP-equivalent output** (adjacent 2ch sum midway
  between the strips matched to the 64 mV of the DESY beam test 2026;
  the HV dependence of 2026-10-19 was taken at tune 50 % too). Sent at
  once by "★ Apply Standard Settings ★" on the laser panel
  (`laser_freq_hz`, `laser_tune_auto`, `laser_tune_percent` in
  `config.py`). Auto TUNE uses the controller's per-frequency
  calibration and is not MIP-equivalent
- **Laser output conversion**: the relation between TUNE and signal
  amplitude changes with, e.g., the camera's visible-light illumination
  being ON/OFF, so no fixed conversion values are kept. To match an
  amplitude other than the standard, use the TUNE optimization on the
  laser panel

The Phase 3 **auto-scan sequences** (position scan / 2D raster / TUNE
energy scan) are built in. Real-time analysis tools are not implemented
yet (planned for late Phase 4).

## Setup (control PC, Windows)

```bash
pip install -r requirements.txt
```

No additional installs are needed (`requirements.txt` alone is
sufficient). NI-VISA and the LeCroy VICP passport are **not required** —
VICP is implemented in pure Python in `lecroy_scope.py`. `pyvisa` +
`pyvisa-py` are used only for VXI-11 (also included in requirements.txt).

Preparation:

1. **PiLAS DX**: USB requires the FTDI VCP driver (Product Guide §3.3).
   Check the COM number in Device Manager (the analysis PC uses **COM5**).
2. **LeCroy**: check the control mode under Utilities → Utilities Setup →
   Remote on the scope. For **TCPIP (VICP)** (default) use
   `VICP::<SCOPE_IP>::INSTR`; for **LXI (VXI11)** use
   `TCPIP0::<SCOPE_IP>::inst0::INSTR` (either way the other transport
   is tried automatically on connect). The IP is <SCOPE_IP>.
3. **SHRC-203**: enable Ethernet in the unit's menu and set IP
   <STAGE_IP>. The communication mode is assumed to be SHOT-compatible.
   **Match the port number to the unit's setting** (the address field
   accepts the `<STAGE_IP>:<port>` form).
   **Right after power-up, always run mechanical homing (the GUI's
   "Mechanical Home (H:W)" or M-ORG on the JS-301).**
4. Set the PC's IP to <PC_IP>, **subnet mask 255.255.255.0**,
   gateway <GATEWAY_IP> (moved from <OLD_SUBNET>.x to <LAB_SUBNET>.x on
   2026-10-23). With a mask of 255.255.255.255 the PC cannot reach even
   the devices on its own segment directly; everything goes through the
   router and the stage does not answer (see the wiring diagram
   laser_setup.drawio).

Per-environment settings go into `config_local.json` (recommended to keep
out of git):

```json
{
  "laser_port": "COM5",
  "stage_um_per_pulse": [2.0, 2.0, 2.0],
  "stage_speed_pps": [1000, 10000, 200],
  "scope_save_dir": "C:\\Users\\<SCOPE_WIN_USER>\\Desktop\\data",
  "scope_smb_user": "<SCOPE_USER>",
  "scope_smb_password": "<SCOPE_PASSWORD>",
  "transfer_dest_dir": "..\\data"
}
```

Waveform destinations (on the scope) are organized by date below
`scope_save_dir`:

```
<scope_save_dir>\<year>\<YYYYMMDD>\manual\                 manual saves
<scope_save_dir>\<year>\<YYYYMMDD>\scan_<stamp>_<mode>\    auto scans (one per scan)
```

The auto-scan folder name (e.g. `scan_20260705_143022_1dY`) carries the
same timestamp as the PC-side log CSV (`logs/scan_20260705_143022.csv`),
so the two can be matched. The folder is created on the scope
automatically.

## Troubleshooting (symptoms & fixes confirmed on hardware, 2026-07)

- **Scope: shows "Connected" but settings don't apply / no screen**
  → Older versions could report "connected" without real communication.
  The GUI now validates the `*IDN?` response on connect and, if there is
  no response, tries VICP ⇔ VXI-11 automatically before raising an
  error. The recommended resource name is `VICP::<SCOPE_IP>::INSTR`
  (matches LeCroy's Remote default TCPIP(VICP); if ping works you're
  fine).
- **Stage: even ping fails (right after an IP change)** (hardware
  2026-10-23, migration to <LAB_SUBNET>.x)
  → The PC's subnet mask was `255.255.255.255`. With that mask the PC
  cannot reach the devices on its own segment directly (via ARP) and
  sends every packet to the router <GATEWAY_IP> (`tracert <SCOPE_IP>`
  shows <GATEWAY_IP> as the first hop). The scope, a Windows machine with
  a gateway setting, can still reply through the router, but the
  SHRC-203 cannot reply outside its own subnet and stays silent. Fix:
  set the PC's mask to `255.255.255.0`, set the SHRC-203 to IP
  <STAGE_IP> / mask 255.255.255.0 / gateway <GATEWAY_IP> and **power-cycle
  it** (the Ethernet settings take effect after a restart). Also check
  with `arp -a` that no other device on the LAN uses the same IP.
- **Stage: ping works but the connection times out**
  → Most likely a TCP port number mismatch with the unit. The lab unit
  uses `9004` (confirmed on hardware 2026-07; the GUI default). Check
  the port number in the SHRC-203's Ethernet menu and enter
  `<STAGE_IP>:<port>` in the address field (for Lantronix
  XPort-based LAN converters try `10001`; `23` is the XPort's telnet
  setup port and cannot be used for control). If another session may be
  holding the connection, restart the controller. On connect the GUI
  verifies communication with the status query `!:` and, if silent,
  raises an error suggesting to check the SHOT-compatible mode and the
  CRLF delimiter.
- **Data retrieval: "System error 67" (network name not found)**
  → Diagnosed on hardware 2026-07-06: ping works but both SMB ports
  (445/139) are closed — the scope's Windows is not offering file
  sharing. Operate the scope directly and (1) enable file & printer
  sharing (2) allow sharing through the firewall (3) share the
  `scope_save_dir` folder under the share name `data` (grant <SCOPE_USER>
  "Change" permission — required for deletion). On the PC side,
  `config_local.json` has `"scope_smb_root": "\\\\<SCOPE_IP>\\data"`
  (already set). See "Initial data-retrieval setup (scope side)" below
  for the detailed steps. The share was created with this procedure the
  same day and the first retrieval (7,178 files / 4.6 GB) was confirmed
  to succeed on hardware.
- **Camera: image is all black / all white**
  → Adjust "Gain" in the camera panel (whether the setting took effect
  can be checked from the actual value in the Communication Log; if the
  actual value does not change, the camera does not support it). If that
  does not help, adjust the illumination brightness and the lens
  aperture. The camera on this setup does not support autofocus
  (manual-focus lens), so the AF and exposure controls were removed from
  the GUI.
- **Stage: "Machine coordinates: unknown" is shown and moving to the
  saved origin is not possible**
  → The GUI has lost track of the machine coordinates across R:W (M-ORG
  was run from the JS-301, the controller was restarted, homing was
  stopped midway, etc.). Running the GUI's "Mechanical Home (H:W)"
  starts the tracking.

## Launch

```bash
python main.py              # hardware mode
python main.py --simulate   # simulate all devices (UI check without hardware)
```

The simulation mode has a pseudo sensor response
(`devices/sim_world.py`: zero signal above electrodes, minimum amplitude
at focus, a tilted sensor, TUNE dependence), so Find Origin, Full
Calibration, Z Focus, TUNE optimization and scans can be exercised to
the end (in the order: Mechanical Home → [Positions] "Go to Find-Origin
Start" → laser ON → Find Origin). State files are saved to `logs_sim/`
so that the real-hardware `logs/` is not polluted.

## Usage (standard measurement procedure)

1. Select the DUT to measure with the toolbar **Sensor** selector (for a
   new DUT: "Edit..." → enter the geometry → "Save As New Sensor").
2. Press **Connect** on each panel (scope / stage / laser / camera).
3. Run **Mechanical Home (H:W)** on the stage panel (once after
   power-up; watch for collisions). Running it from the GUI starts the
   machine-coordinate tracking.
4. Use **"Go to Start"** in the stage panel's [Positions]
   tab for the coarse approach to the vicinity of the previously saved
   origin (the first time, or after remounting, jog using the camera
   **crosshair** as a guide to a point on the reference strip, 50–200 µm
   inside the readout-side end; for a **pixel sensor** onto, or within
   ±50 µm of, the bonding pad of the reference CH).
   The jog arrows indicate the **direction of motion in the camera
   image**; the button text shows the axis and sign actually sent
   (▲ = Y−). Step sizes range from 2 µm to 5 mm.
5. On the laser panel press **"★ Apply Standard Settings ★"** to send
   the standard conditions (internal, 1 MHz, TUNE manual 50 % = MIP
   equivalent), then **Emission ON** (the input fields are synchronized
   to the instrument's current values on connect; for other conditions
   set the trigger source, repetition rate and TUNE individually with
   "Apply").
6. On the scope panel press **Start Mirror** and inspect the waveform.
   **"★ Apply Standard Settings ★"** sends the standard conditions
   (EXT/DC/POS/410 mV, 1 ns/div, horizontal -53 ns, C1–C4 50 mV/div
   +100 mV offset, D50).
7. Run **"Find Origin"** in the stage panel's [Alignment] tab (detects
   the focus, the electrode center Y and the frame edge X, then performs
   R:W and automatically saves the machine coordinates of the origin).
   After remounting, run "Full Calibration" to re-measure the tilt
   coefficients as well. For a **pixel sensor** the procedure switches
   automatically (Y dip of the pad → Z beside the pad → X dip → R:W at
   the pad corner; Full Calibration = plane fit on the other pads).
8. If needed, match the target amplitude with **TUNE optimization** on
   the laser panel.
9. Check the conditions in the Auto Scan panel and press
   **Start Scan**. Progress and remaining time are shown in the toolbar,
   and "Abort" stops the scan.
10. In case of trouble use **■ STOP ALL** at the right end of the
    toolbar (task abort → laser OFF → stage stop → scope STOP).

All TX/RX commands appear in the Communication Log at the bottom left
and are recorded in `logs/gui_YYYYMMDD.log` (measurement notes /
troubleshooting). The laser and scope panels have a command console, so
operations not covered by the GUI can be sent as raw commands.

## Registering sensors / DUTs (`sensors.py`, `logs/sensors.json`)

So that no code needs to be edited when the board or sensor is changed,
the following items are collected in a per-sensor profile. Switching
with the toolbar "Sensor" selector swaps the default scan ranges, tilt
coefficients, saved positions and readout mapping (CH labels in the
scope panel, status bar) to those of that sensor.

| Item | Contents |
|---|---|
| Type | Strip / Pixel (added 2026-10-16). Switches Find Origin, Full Calibration, the default scan range, "Go to electrode" and the analysis kind |
| Geometry (strip) | Strip pitch, electrode (metal) width, offset from the frame edge to the start of the exposed region, exposed strip length, end gap |
| Geometry (pixel) | Pixel pitch, bonding-pad size (signal-free square; the ruler of Find Origin) |
| Readout | Pad name per scope CH, used/unused, reference CH. Strip: electrode center Y (reference CH = 0). Pixel: pixel (column, row) (reference CH = (0, 0); columns increase towards −X, rows towards −Y; pixel center = (−pad/2 − col×pitch, −pad/2 − row×pitch)) |
| Default scan ranges | Start / end / step of the fast axis Y and the slow axis X |
| Full Calibration measurement points | Strip: X measurement points, Y measurement points ("Y:CH"), probe positions (Y offsets for the Z measurement and the X edge detection), Y sweep half-width. Pixel: pad centers of the readout pixels other than the reference (automatic), Y offset for Z, Y sweep half-width |
| Tilt coefficients | dZ/dX, dZ/dY, dY/dX (result of Full Calibration / manual entry; history in `sensor_calibration_<id>_<stamp>.json`) |
| Saved origin | Machine coordinates of the logical origin (updated automatically at every Find Origin) |
| Saved positions | Name → coordinates (logical coordinates / machine coordinates) |

- **New DUT**: select a similar sensor, then "Edit..." → rewrite the
  geometry and readout → "Derive from geometry" to derive the scan
  ranges and calibration measurement points → "Save As New Sensor".
- Four sensors are registered initially: the standard strip sensor
  (measured values from `scan_geometry.py`), 4col 1000 µm, the
  500 µm section of the variable-pitch sensor, and **Pixel 2x2 (500 µm
  pitch, 100 µm pad; the layout of pixel#3 of 2026-10-15)**. For 4col
  and the variable pitch, **the dimensions other than the pitch are
  unverified** (templates that reuse the standard sensor's values), so
  edit them to measured values before use.
- **Pixel coordinates**: logical origin = the +X/+Y corner of the
  reference CH's pad (the pad spans x, y ∈ [−pad, 0]). Pixel center =
  pad center. The 4-pixel corner is at (−pad/2 − pitch/2,
  −pad/2 − pitch/2). The analysis (tt_pixel.py) finds the pad positions
  from the amplitude itself, so a small origin offset does not change
  the results.
- The old-format `logs/sensor_calibration.json` and
  `logs/positions.json` are migrated to the standard sensor on first
  launch (the original files are kept). Of the entries in
  `positions.json`, presets originating from scan_geometry are not
  migrated; only user-registered positions are carried over as machine
  coordinates.

## Stage: saved positions and machine-coordinate tracking

- **Coordinate systems**: display and moves use logical coordinates
  (logical origin = frame edge, reference electrode center, focus Z).
  Machine coordinates (referenced to the mechanical origin) are shown
  alongside, below them.
- **Machine-coordinate tracking**: R:W resets the controller's
  coordinates to 0, so the GUI accumulates the "machine coordinates of
  the logical origin" and saves them to `logs/stage_frame.json` (reset
  to 0 by H:W; the coordinates just before each R:W are added; remains
  valid after restarting the GUI). If M-ORG was run from the JS-301, or
  after a controller restart, redo H:W from the GUI.
- **Automatic origin registration**: when Find Origin (Auto Alignment)
  completes through R:W, the machine coordinates of that logical origin
  are saved to the current sensor. For a manual "Reset Logical Origin
  (R:W)", they can be saved via the checkbox in the confirmation
  dialog.
- **[Positions] tab**:
  - "Go to Origin" and "Go to Start": coarse approach
    after mechanical homing
  - "Go to strip": select a CH and a location (readout-side end /
    center / far-side end); the coordinates are computed from the sensor
    geometry and the current tilt coefficients, and the stage moves
    there. For a pixel sensor this becomes "Go to pixel" (beside pad
    (signal) / pad center / 4-pixel corner)
  - Saved-position dictionary: save the current position under a name
    (logical coordinates = points to the same place on the sensor /
    machine coordinates = for the coarse approach before the origin is
    found). Double-clicking an entry also moves there
- Moves use the same relative-move (M:W) method as scans. While a task
  (scan, calibration, etc.) is running, jog, moves and origin operations
  are disabled (the stop buttons are always enabled).

## Auto Scan (Phase 3)

Run from the "Auto Scan" panel in the right column. The sequence
implements the Phase 3 algorithm of the specification (move → settle →
clear sweeps → SINGLE acquisition → wait for completion → save → next
point).

| Mode | Purpose | Origin of defaults |
|---|---|---|
| 1D position scan | Across strips (Y: +750 → -2250 µm = outside C0 → outside C5, step 10 µm) | positions measured 2026-08-24 (out to half a pitch beyond C0/C5) |
| 2D raster scan | Area scan of across (Y) × along (X: 0 → -9370 µm = edge → scan end, step 500 µm), with snake option | positions measured 2026-08-24 + TCT Scan plan (scan area 9.370 mm/strip) |
| 1D + TUNE | Combined 1-axis position × energy scan: **TUNE is swept at each position**. Position is the outer loop, so the stage only moves when the position changes. A wider position step is used (auto-set to 50 µm when the mode is selected) | 2026-07 plan |
| TUNE scan | Energy scan at a fixed position | tune 50–90% (fixed-position plan) |

**Sensor-tilt correction** (1D/2D; the coefficients are the current
sensor's Full Calibration / manually entered values. For the standard
sensor, if uncalibrated, the 2026-08-24 measured fit in
`scan_geometry.py`):

- **Rotation tracking (Y)**: shifts Y relative to the X scan start
  position to follow the strips' in-plane rotation dY/dX. The strip
  appears at the same scan Y coordinate in every X row, and a
  longitudinal 1D scan stays on the strip (for the standard sensor,
  without correction it drifts ~72 µm over the full length).
- **Z tracking** (focus; 2 µm/pulse resolution) is chosen from 3
  methods:

  | Method | Computation | When to use |
  |---|---|---|
  | Coefficients (focus at origin) **default** | z = dZ/dX·x + dZ/dY·y (plane anchored at the logical origin) | The standard right after Find Origin. **The Z is the same wherever the stage is at the start** |
  | Coefficients (focus at current pos.) | The same plane anchored at the current position at the start (the previous method) | When starting from a place where the focus was set manually |
  | Measured Z (end points / corners) | 1D: linear interpolation of the focus Z at the start and end points. 2D: bilinear interpolation of the focus Z at the four corners | For DUTs whose coefficients have not been calibrated, or when the calibration result is doubtful. **Simple and reliable** |
  | Off | Z is not moved | E.g. when scanning Z itself |

  Procedure for the Measured Z method: selecting the method shows rows
  for the start and end points (four corners in 2D) → "Go" on each row
  moves XY to that position → focus with the Z jog or Z Focus → capture
  it with "Get Z". The values are absolute Z in logical coordinates, so
  they do not depend on the stage position at the start of the scan.
- The corrected coordinates (the actual move targets) are recorded in
  the CSV, and the method, coefficients and measured Z are kept in the
  header. The start confirmation dialog shows the Z range and the Y
  shift, and a warning appears if the Z of the first point is 30 µm or
  more away from the current Z.

**Data sanity check**: the beam radius is a few µm or less, so with good
focus the signal is **almost 0 directly above an electrode (strip
metal)** and rises steeply at the electrode edge. Verify this in the
first (transverse) row of the scan. If it does not drop to 0 or the
transition is gradual, suspect defocus or a coordinate offset.

**Every scan start automatically runs the following order** (established
by the 2026-07 hardware debugging as the sequence that completes without
glitches):

1. **Scope setting changes** — if averaging is ON, turn it **OFF**
   (without real-time waveforms the completion check of
   sequence/SINGLE acquisitions does not work; the "Averaging OFF"
   checkbox defaults to ON) → sequence setup → save setup
   (the file counter is managed on the PC side, see below)
2. **Laser setup** (auto-ON when "Laser ON at start" is checked)
3. **Start the scan**

**Screen mirroring** does not stop (changed 2026-09-07): SCDP is only
hazardous during sequence acquisition, so it is automatically paused
only during each point's acquisition window ("clear sweeps" through
"save") and resumes during moves and settling. With the mirror started,
the most recently acquired waveform can be checked point by point even
during a scan.

**The counter of the saved files is managed on the PC side** (fixed
2026-09-07): with the scope's built-in counter (EnableCounterSuffix),
the counter failed to increment on hardware and **all points were lost
through same-name overwrites** (two scans on 2026-09-07). Therefore each
point is saved with an explicit counter of the form `scan--00042` in
TraceTitle (the file name is the same as before, `C2--scan--00042.trc`,
and always matches the CSV index). In addition, the number of files in
the scope-side folder is verified at every point, and **if it does not
increase for 2 consecutive points the scan is aborted automatically**
with an error message.

Procedure:

1. Connect scope and stage (TUNE scans also need the laser). Set
   channels, trigger (EXT) and timebase on the scope panel first
   ("★ Apply Standard Settings ★" also includes Averaging OFF).
2. **Fix the logical origin**: "Find Origin" in the stage panel's
   [Alignment] tab is recommended (just do a coarse approach to the
   vicinity of the reference strip; everything from detecting the focus,
   the electrode center Y and the frame edge X through R:W is
   automatic). To do it manually, move near the end of the reference
   strip → Z focus → at the position corresponding to the frame edge
   (signal edge +632 µm) run "Reset Logical Origin (R:W)" to make it
   **(0,0,0)**.
   Scan coordinates are entered **relative to the logical origin (µm)**
   and the stage moves with **relative moves (M:W)** (the absolute move
   (A:W) had issues and is not used; this is also immune to the
   absolute-coordinate changes after each M-ORG). Axes not being scanned
   stay at their position at scan start (Z follows the selected Z
   tracking method).
3. Set the sequence (default 2000 seg/point = 2000 events/step), settle
   time and per-point timeout. The estimate row shows total points ×
   events.
4. "Start Scan" → confirmation dialog → run. Progress and remaining time
   appear in the toolbar's task display. "Abort" or "■ STOP ALL" at the
   right end of the toolbar stops at any time (an abort stops the
   stage + scope STOP; a laser that was auto-switched ON is switched OFF
   again).

Output:

- **Waveforms**: on the scope's disk (directory/prefix as configured,
  .trc etc. with the PC-managed counter)
- **Coordinate log**: PC-side `logs/scan_YYYYMMDD_HHMMSS.csv`
  (index, x_um, y_um, z_um, tune_pct, status, timestamp).
  Coordinates are logical coordinates **after tilt correction** (the
  actual move targets). The correction coefficients and the start
  reference position are recorded in header rows.
  Match with the scope's counter-suffixed files via the index.
  status is ok / timeout (missing trigger etc.) / aborted.

## TUNE Optimization — fixing the vanishing signal at ≠40 MHz

**Symptom**: change the repetition rate away from 40 MHz and the AC-LGAD
signal disappears entirely (confirmed on hardware 2026-07).

**Cause**: the optimal PiLAS TUNE (lasing efficiency) **depends on the
repetition frequency**. Keeping the manual TUNE tuned at 40 MHz while
changing the frequency drops below the lasing threshold and no optical
pulses are emitted. TRIG OUT keeps firing electrically, so the scope
continues to trigger — "the waveform updates but stays flat".

**Fix** (**TUNE optimization** on the laser panel):

1. After changing the frequency, with the laser ON and focused, select
   the **method**, the CH and the target amplitude and run "Optimize"
2. TUNE is lowered from 100% (weak-light side) while measuring the
   amplitude (PAVA) (coarse sweep, default 2% steps); the interval that
   crosses the target is **re-swept finely in 0.5% steps with many reads
   (median)**, the **TUNE closest to target ± tolerance (default ±1 mV)
   is set automatically** and a verification measurement follows. Takes
   tens of seconds to a minute. On abort or failure TUNE returns to the
   original value (with the coarse sweep alone the threshold crossing is
   decided by a few mV of read noise; the 2026-10-05 log jumped
   65 → 77 mV between adjacent TUNE values)
3. The curve is logged to `logs/tuneopt_<stamp>.csv` (phase = coarse /
   fine / verify, amplitude per CH, sum and spread; the lasing threshold
   at that frequency can be read off as well). If the target is not
   reached, the maximum-amplitude TUNE is set and a warning shown (judge
   whether that frequency's maximum output meets the energy requirement).
   If the target is exceeded even at TUNE 100% (weakest), a warning says
   that attenuation is needed

**Methods** (added 2026-10-07):

| Method | Measures | Beam position | Default target |
|---|---|---|---|
| Single CH | amplitude (PKPK) of the selected CH | on the silicon beside a strip (where the signal is visible) | 200 mV |
| Adjacent 2ch sum (MIP equivalent) | sum **A_lead + A_sub** of the amplitudes of the CHs reading two neighboring strips | **midway between the 2 strips** (250 µm from the electrode for a 500 µm pitch) | **64 mV** |

The adjacent 2ch sum uses the property of an AC-LGAD that A_lead + A_sub
changes little when the beam moves between two strips (charge sharing
only redistributes it). Matching the sum midway between the strips
**equalizes the signal to a MIP-equivalent amount insensitively to
position errors**. The target comes from the DESY beam test (2026):
**64 ± 1 mV** midway between strips (y = 250 µm) (for reference: 77 mV on
a single CH next to a strip). With "Move to the Y midway between the 2
strips" (default ON) the stage moves automatically to the Y midway
between the electrode centers of the selected CH pair **keeping the
current X** (Y corrected by the in-plane rotation dY/dX and Z by the tilt
dZ/dY, relative to the current position), measures, and returns to the
original position after completion or abort (stage connection required;
the current position must be in focus). With it OFF the measurement is
taken at the current position (place the beam midway between the strips
by hand). The CH pairs are enumerated from the electrode Y of the readout
CHs in the sensor profile, adjacent ones only.

As a quick check you can also set TUNE to **auto** mode on the laser
panel (the controller uses per-frequency calibration values), but for
measurements requiring matched pulse heights the TUNE optimization
against a target amplitude is more reliable.

## Z Autofocus (waveform-amplitude based)

**★ Premise of focusing (corrected by the 2026-09-07 measurement) ★**:
the better the focus, the **smaller** the amplitude — LGAD gain
suppression due to the increased local carrier density (plasma effect).
When the focus was best on the camera, the amplitude was at its minimum.
The old method "maximum amplitude = focus" was wrong. The sweep is done
**on the silicon beside a strip, where the signal is clearly visible**
(the electrode is ~50 µm wide, and directly above it the spot stays
inside the metal even when defocused, so the amplitude stays 0 and no
valley is visible — confirmed on hardware 2026-09-07). The gain
suppression valley is shallow (a few tens of %), so the valley center is
determined as the midpoint of the 50%-threshold edges of the two wings.

Usage ([Alignment] tab of the stage panel. Normally "Find Origin"
includes this procedure, so that is recommended. It is also used to
focus at the end points for the Measured Z method):

1. Laser ON, **beam on the silicon beside a strip** (a position where
   the signal is clearly visible; not directly above an electrode).
   Check the signal on the scope (trigger EXT, waveforms updating in
   NORM)
2. Choose CH, range (default ±20 µm), step (default 2 µm = the Z
   resolution) and method (default "Min amplitude (focus)"), press
   "Z Focus" → sweeps upward from below in one direction (backlash
   protection) → moves automatically to the Z of the valley center.
   Takes ~20–30 s
3. To start a scan right away, set Z tracking to "Coefficients (focus at
   current pos.)"; this Z is then propagated as the reference plane
   across the whole area
4. The sweep curve is logged to `logs/autofocus_<stamp>.csv`. If it
   reports that the valley cannot be identified, widen the range
   (±30–50 µm) or reposition directly above the electrode and re-run

During execution, sequence is switched OFF and trigger to NORM (both are
re-applied when a scan starts, so no need to restore them). On abort or
error the stage returns to the original Z.

## Auto Alignment (signal based, added 2026-09-07)

A feature that removes the manual work of "note the strip center with
the camera → edit scan_geometry.py". **The laser-induced signal itself
is used as the position sensor**: the signal is zero above the metal
electrodes and the edges are cut off within the beam radius (a few µm),
so the dips/edges of the amplitude profile serve as a ruler with µm
precision (gui/alignment_worker.py; the amplitude readout is the same
PAVA as in Z autofocus).

It is operated from the [Alignment] tab of the stage panel. The
measurement points and offsets come from the current sensor profile (the
numbers below are examples for the standard sensor).

**Preparation**: using "Go to Start" in [Positions] or the
camera, place the beam on the strip of the reference CH (C1 for the
standard sensor), 50–200 µm inside the readout-side end of the exposed
region (the ②/③ boundary) (a coarse approach within ±100 µm is
sufficient). Have the laser ON and the trigger on EXT with the signal
visible.

When continuing with Full Calibration or redoing Find Origin from a
state where the origin has already been found (stage at the logical
origin = on the frame edge with no signal), check **"Pre-move to start"**;
the stage then first moves to the start
position (on the reference strip, 100 µm inside the end) relative to the
current logical origin before starting.

**Find Origin (before every measurement; coarse only about 2–3 min,
with fine finishing + verification ON about 7–12 min)**:
① coarse Y sweep to detect the electrode dip → ② **Z of minimum
amplitude (= focus) on the silicon 80 µm beside the electrode** (the
corrected premise above; if the valley touches the end of the sweep it
is re-centered automatically) → ③ fine Y sweep to fix the electrode
center (C1) → ④ X sweep (toward the + side = pad region) to detect the
signal-vanishing edge (the ②/③ boundary) → ⑤ re-check Z on the silicon →
**approaches the edge +632 µm (design offset ⓪+①+② = frame GND edge)
from the negative side on all axes and automatically performs Reset
Logical Origin (R:W)**, then automatically saves the machine coordinates
of the logical origin to the current sensor.
Subsequent scan coordinates are referenced to this logical origin (frame
edge), and for the standard sensor **X=−10 mm is the center of gap ④
(the boundary of the strip regions)**.

**Fine finishing + verification (added 2026-10-05, ON by default)**:
after ③④⑤ above, each quantity is swept again in **one-pulse (2 µm)
steps of the stage with 4 reads per point (median)** and determined to
sub-µm precision by model fits:
- Y electrode center: each edge is fitted separately with an erf (error
  function) model (a linear term absorbs the plateau slope and the
  amplitude enhancement just before the edge) → midpoint. Two sweeps;
  their difference is shown as the repeatability. One more sweep in the
  reverse (descending) direction gives the center difference to the
  ascending sweeps as a **backlash estimate** (the origin is set with an
  ascending approach)
- X signal edge: the same erf fit, 2 sweeps + 1 reverse sweep
- Z focus: the valley is fitted with a **Lorentzian + linear baseline**
  and the minimum of the curve is taken as the focus (on the 2026-10-05
  hardware data two measurements at the same spot agreed within 0.1 µm;
  the old 50 % threshold method scattered by 0.4–0.7 µm). The conversion
  from the probe position (80 µm beside the electrode) to the electrode
  center uses the current tilt coefficients (0.3 µm for the standard
  sensor)
- ⑥ **Verification**: after R:W the electrode center Y, the signal edge X
  and the focus Z are re-measured with the same fine sweeps; if the
  residual of any axis is one pulse or more it is corrected and R:W is
  redone (at most twice). The final residual, the backlash estimates and
  the fit errors appear in the completion dialog and in
  `logs/sensor_calibration_*.json` (Full Calibration), and the raw data
  of all sweeps are logged to `logs/alignment_<stamp>.csv`. Residuals
  below 1 µm are the remainder of the 2 µm pulse grid
- The recorded positions are the actual pulse-grid values, not the
  commanded ones (a 3 µm step is rounded to the 2 µm grid; the commanded
  value would add a sawtooth error of up to 1 µm)

An analysis of the 2026-10-05 hardware logs (two Full Calibrations)
showed that the electrode centers from the threshold method and the erf
fit agree within 0.3 µm and the X edges within 0.5 µm, i.e. **the
signal-based measurement itself is self-consistent below 1 µm**. A
difference of several µm from a manual (camera) origin is attributed to
**differences in the reference definition**: the offset between the
camera crosshair and the laser spot, the deviation of the real pad edge →
frame edge distance from the design value 632 µm, and the approach
direction (backlash). The X design value can be matched to the real
dimension with "frame edge → exposed-region offset" in the sensor editor.

**Full Calibration (after remounting or a jig change; coarse about
8–12 min, fine about 15–25 min)**: after Find Origin, the electrode
center and focus Z are
measured at X = −5.3 / −9.9 mm, and the electrode center and focus Z at
the Y = C2 / C4 electrodes; **dY/dX (rotation) and dZ/dX, dZ/dY (tilt)
are obtained by straight-line fits and saved to the current sensor
profile** (history in
`logs/sensor_calibration_<sensor ID>_<stamp>.json`).
The measured spacing between adjacent electrodes and the **residuals of
the straight-line fits** are also shown in the result.
With fine finishing ON the electrode center and focus Z of each
measurement point are also determined by the one-pulse-step fits. The Z
of the X points is measured along the electrode line (Y drifts with
dY/dX), so dZ/dX is the slope minus the coupling term dZ/dY·dY/dX (the
plane model of the scans uses the partial derivative at constant Y;
0.03 µm/mm for the standard sensor).
Z is only determined in 2 µm steps and the valley is shallow, so if the
Z residuals exceed a few µm or the coefficients moved a lot from the
previous time, do not take them at face value: re-run, or set the scan's
Z tracking to the "Measured Z" method.

**Priority of coefficients**: coefficients in the sensor profile (Full
Calibration / "Edit coefficients...") → for the standard sensor only,
`scan_geometry.py` (defaults from the 2026-08-24 camera measurement) →
0 for any other uncalibrated sensor. The currently active coefficients
and their source are shown both in the stage panel's [Alignment] tab and
in the "Tilt correction" field of the Auto Scan panel, and are updated
at every calibration, manual entry and sensor switch.

Note: the focus measurement is done on the silicon 80 µm beside the
electrode center, and the **X edge detection 50 µm beside it** (at 80 µm
the beam left the Y width of the bond pad, the signal did not vanish and
the result was "no clear edge" — confirmed on hardware 2026-09-07).
If "no clear dip/edge" or "valley too shallow" is reported, check the
beam position and focus and re-run. Each measurement point of Full
Calibration also proceeds in the order "dip detection → Z minimization
on the silicon".

## Data Retrieval (scope → PC, SSH/SFTP)

Scan waveforms are first stored on the scope's own disk. In the
[Data Retrieval] tab of the Auto Scan panel, **specify a date** to
retrieve the whole `<scope_save_dir>\<year>\<YYYYMMDD>` folder to the
PC-side `<transfer_dest_dir>\<year>\<YYYYMMDD>` (default `data\` next to
the repository) (specification changed from the SMB method on
2026-09-07. It uses the same SSH/SFTP protocol as
`scp -r <SCOPE_USER>@<SCOPE_IP>:C:\...\20260907 .` in a terminal and is
much faster than the SMB + SHA-256 verification method).

- **Connection**: connects via SSH to the OpenSSH server on the scope as
  `scope_smb_user` (default **<SCOPE_USER>**). **The password is not stored
  and is entered for every transfer** (input dialog; within the same
  session the previous value is pre-filled).
- **Date selection**: the default is today. Folders of past dates can
  also be specified.
- **Duplicate skipping**: files that already exist on the PC with the
  same name and size are skipped automatically. If all files in a scan
  folder have already been retrieved, it is reported as "skipped n
  already-retrieved folders" (re-running is safe). A same-named file
  with a **different size** is not overwritten and is reported as a
  "conflict".
- **Verification**: file sizes are compared after the transfer
  (transmission integrity of SSH/TCP + size check. The SHA-256 re-read
  of the old method was dropped — one of the reasons for the speed).
- **Deletion** (default **OFF**): with "Delete on scope after transfer"
  ON, the scope-side originals are deleted and empty folders cleaned up
  only if there was not a single error, conflict or abort and the sizes
  were verified (to free disk space).
- Results (path, size, status, deleted or not) are recorded in
  `logs/transfer_YYYYMMDD_HHMMSS.csv`.
- Transfers are not possible while a scan is running (mutual exclusion
  in the GUI). Also avoid transferring while autosave is active.

Prerequisite (one-time): the OpenSSH server (sshd) must be enabled on
the scope (Windows) (as of 2026-09 the real hardware is already set up,
and scp connections from a terminal have been confirmed). The
implementation of the old SMB method remains in `devices/scope_files.py`
but is not used by the GUI.

## Quick analysis of acquired data (`analysis\` / the GUI's "Analysis" tab)

An analysis package for retrieved scan data is bundled in `analysis\`.
It auto-detects the scan kind (1dX / 1dY / 1dZ / 2dYX / tune / 1d_tune)
from the log CSV and, with one analysis script per kind, outputs figures
and numbers with the same definitions as the "matome" analysis note
(amplitude and charge, rise time, jitter, position resolution, time
resolution, propagation) (updated 2026-10-05).
Conversion of .trc → a single ROOT file (`--root`) is also available.

It can also be run from the GUI: in the main window's **"Analysis"
tab**, select a scan and press "Run analysis" (the usual control
screen remains as is in the "Control" tab). The analysis runs as a
subprocess, so it does not block even during a measurement; abort,
progress display, figure preview after completion and opening the
result folder are available.

From the command line:

```bat
cd analysis
python run_analysis.py scan_20260826_152932_2dYX --root
```

Output goes outside the repository: figures and CSVs to
`<workspace>\results\<scan name>\`, the ROOT file next to the scan
folder (`<scan_dir>.root`). See
[analysis/README.md](analysis/README.md) for details.

## Structure

```
laser_gui_v2_en/
├── main.py              # entry point
├── config.py            # settings (overridden by config_local.json)
├── sensors.py           # registry of sensor / DUT profiles
├── scan_geometry.py     # measured coordinates & tilt coefficients of the standard sensor (2026-08-24)
├── devices/             # GUI-independent instrument drivers (simulation built in)
│   ├── pilas_laser.py   #   PiLAS DX (RS232/USB, ld=/ts=/f=/tune=/mState?)
│   ├── shrc203_stage.py #   SHRC-203 (SHOT-compatible) + machine-coordinate tracking
│   ├── lecroy_scope.py  #   LeCroy MAUI (SCPI + VBS Automation, SCDP screen capture)
│   ├── scope_scp.py     #   scope→PC data retrieval (SSH/SFTP, date selection + duplicate skipping)
│   ├── scope_files.py   #   (old) SMB-based data retrieval — currently unused
│   ├── focus_camera.py  #   USB camera (OpenCV)
│   └── sim_world.py     #   pseudo sensor response for --simulate
├── gui/                 # PySide6 panels (device I/O on worker threads)
│   ├── main_window.py   #   toolbar (sensor selector, task display, STOP ALL) + 3 columns
│   ├── task_runner.py   #   exclusive execution of long-running tasks and progress display
│   ├── stage_panel.py   #   jog / saved positions / alignment
│   ├── laser_panel.py   #   laser settings + TUNE optimization
│   ├── scan_panel.py    #   auto-scan conditions + data retrieval
│   ├── sensor_dialog.py #   sensor-profile editor
│   ├── widgets.py       #   eliding labels, wheel-blocking filter
│   ├── scan_worker.py / alignment_worker.py / autofocus_worker.py /
│   │   tune_worker.py / transfer_worker.py   # execution threads of the tasks
│   └── ...
├── logs/                # communication logs, scan records, state files
│   ├── sensors.json     #   sensor registry (incl. coefficients, saved origin, saved positions)
│   └── stage_frame.json #   machine coordinates of the logical origin (tracking across R:W)
└── analysis/            # quick analysis of acquired data (one analyze_*.py per scan kind, .trc→ROOT conversion)
    └── run_analysis.py  #   entry point (see analysis/README.md)
```

Additional environment-dependent settings (`config_local.json`):

- `scope_trig_delay`: standard horizontal offset (default `"-53NS"`)
- `stage_screen_dir`: jog orientation. The signs of the stage axes that
  move toward (right, up) in the camera image (default `[1, -1]` = X+ is
  right, Y− is up). Fix this if the mounting orientation of the camera
  or the stage is changed

## Known items to verify (first run on hardware)

- **Calibration of `stage_um_per_pulse`**: X/Y confirmed at 2 µm/pulse
  on hardware (2026-07: commanding 100 µm moved 200 µm, so 1.0 → 2.0 was
  corrected). **The Z axis is unverified**, so on first use jog in small
  steps and confirm the actual travel (a wrong setting with large steps
  risks crashing into the objective lens).
- The SHRC-203's Ethernet port number (default 9004, confirmed on
  hardware 2026-07) and command mode (SHOT-compatible/dedicated) must
  match the controller settings.
- LeCroy's `VBS 'app.SaveRecall...'` property names can differ between
  firmware versions. If something does not work, check from the Command
  tab with e.g. `VBS? 'return=app.SaveRecall.Waveform.WaveFormat'`.
- The PiLAS COM port number and the camera device number.

## References

- NKT Photonics, "PILAS picosecond pulsed diode laser Product Guide"
  (guide/LASER_guide.pdf, chapter "RS232 Commands v1.1.AEe") /
  product page: https://www.nktphotonics.com/products/pulsed-diode-lasers/pilas/
- OptoSigma, "3 Axis Stage Controller SHRC-203 Manual (communication
  command reference)"
  https://jp.optosigma.com/ja_jp/motorized-stages/controllers-drivers/stage-controllers/SHRC-203.html
- Teledyne LeCroy, "MAUI Oscilloscopes Remote Control and Automation Manual"
  https://cdn.teledynelecroy.com/files/manuals/maui-remote-control-automation-manual.pdf
- PyVISA Documentation: https://pyvisa.readthedocs.io/
- pySerial Documentation: https://pyserial.readthedocs.io/
- Internal documents: guide/ clean-room facility & semiconductor-detector
  implementation/evaluation system specification (both editions),
  guide/AC_LGAD_TCT_ScanGuide.pdf, guide/AC-LGAD laser timing-resolution
  evaluation summary (PDF)
