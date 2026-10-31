# 3. Laser Intensity Adjustment (TUNE Optimization)

## What TUNE is

TUNE is the setting that determines the PiLAS pulse intensity. Note the direction: **0 % gives the strongest pulse and 100 % the weakest.**

- The relation between TUNE and signal amplitude changes with the **repetition rate**, the **camera light ON/OFF** and the optical conditions (alignment).
  There is no fixed conversion, so match the amplitude by measurement every time.
- The optimal TUNE depends on the repetition rate. If you change the rate while keeping a manual TUNE tuned for another rate, the laser can drop below the lasing threshold and emit no light
  (TRIG OUT keeps coming, so the scope waveform keeps updating but stays flat). **Redo TUNE optimization after changing the rate.**
- For a quick check you can also set TUNE to **auto** (the controller uses calibration values for each rate). For measurements where the pulse height must be matched, TUNE optimization is more reliable.

## Using TUNE optimization

Use the "TUNE optimization (match target amplitude)" group in the laser panel.

![Laser panel](../images/gui/laser-panel.png){ width="480" }

*The screenshot shows the old version of the screen. The current version also has the "Method" field, the tolerance "±" field and the "Move to the middle Y between the 2 strips" checkbox.*

!!! note "Differences from the English UI version"
    The following describes the Japanese UI version (`laser_gui_v4`). As of 2026-10, the English UI version (`laser_gui_v4_en`) has the old behavior:
    it has no "Method" field, tolerance, fine sweep or automatic move to the middle Y. It lowers TUNE from 100 % by [step] while measuring the amplitude of a single CH,
    sets TUNE to the value where the target was reached, and stops.

### Prerequisites

- [ ] Laser ON, oscilloscope connected, signal present with the EXT trigger
- [ ] Focus is set by [Find Origin](alignment.md#origin) or [Z Focus](alignment.md#z-focus)
- [ ] The beam is at a suitable position for the measurement (see the table below for each method)

### Methods

| Method | What is measured | Beam position | Default target |
| --- | --- | --- | --- |
| "Single CH" | Amplitude (PKPK) of the selected CH | On the silicon next to a strip (where the signal is clearly visible) | 200 mV |
| "Adjacent 2-ch sum (MIP-equivalent)" | Sum of the amplitudes of the CHs reading two adjacent strips \(A_\text{lead} + A_\text{sub}\) | **Midway between the 2 strips** (250 µm from the electrode for a 500 µm pitch) | **64 mV** |

The adjacent 2-ch sum uses the property of AC-LGADs that \(A_\text{lead} + A_\text{sub}\) changes little when the beam moves between two strips
(the charge is only shared). Matching the sum at the strip midpoint **sets a MIP-equivalent signal level that is insensitive to position offsets**.
The 64 mV target comes from the DESY beam test (2026): 64 ± 1 mV at the strip midpoint (for reference, 77 mV for a single CH near a strip).

!!! question "To be confirmed"
    The standard method and target for normal measurements (whether "Adjacent 2-ch sum, 64 mV" may be the standard).

### Procedure

1. Select the "Method".
    - "Single CH": select the [CH].
    - "Adjacent 2-ch sum": select the CH pair (e.g. C1+C2). Only adjacent pairs, determined from the electrode Y values in the sensor profile, are listed.
2. Check the [Target] amplitude, the tolerance "±" (default ±1 mV) and the coarse-sweep [step] (default 2 %). When you switch methods, the target resets to the value for that method.
3. For the adjacent 2-ch sum, with **"Move to the middle Y between the 2 strips (current X; returns afterwards)"** (default ON),
   the stage moves automatically, at the current X, to the Y midway between the electrode centers of the selected CH pair (Y and Z corrected for in-plane rotation and tilt), and returns to the original position after finishing or aborting.
   This requires the stage to be connected and the beam to be in focus at the current position. If OFF, it measures at the current position.
4. Press **[Optimize]** and press [Yes] in the confirmation dialog.

### What it does

1. **Coarse sweep**: lowers TUNE from 100 % (weak-light side) by [step] while measuring the amplitude (PAVA).
2. **Fine sweep**: sweeps the interval that crossed the target **in 0.5 % steps with many reads (median)**, and sets the TUNE closest to target ± tolerance.
3. **Verification**: measures the amplitude again at the set TUNE.

It takes from a few tens of seconds to 1 min. With the coarse sweep alone, the threshold crossing would be determined by read noise of ± a few mV, so a fine sweep is done.

### Result

- On success, "TUNE optimization done" is shown and TUNE is set.
- If the target is never reached, **TUNE is set to the value with the maximum amplitude and a warning is shown** (decide whether the maximum output at that rate meets your requirements).
- If even TUNE 100 % (weakest) exceeds the target, a warning says that attenuation is needed.
- On abort or failure, **the original TUNE is restored**.
- The sweep curve is saved in `logs/tuneopt_<datetime>.csv` (phase = coarse / fine / verify, amplitude per CH, sum, spread).
  The lasing threshold at that rate can also be read from it.

## About MIP-equivalent intensity

In the analysis notes (matome), scans where the pulse height measured next to a strip is 0.7–1.3 times the beam Landau MPV (76 mV), i.e. 53–99 mV, are called "MIP-equivalent".
The laser has no Landau fluctuations, so the time resolution obtained at MIP-equivalent intensity is interpreted as a lower limit (floor) for MIPs.

## Sending TUNE by command

The GUI shows and accepts TUNE in %, but the units are different when you send it directly from the command field at the bottom of the laser panel.

| Command / response | Unit | Example |
| --- | --- | --- |
| `tune=` | 4 digits in 0.1 % units | `tune=0650` → 65.0 % |
| TUNE in the `mState?` response | 0.01 % units | `6500` → 65.00 % |
