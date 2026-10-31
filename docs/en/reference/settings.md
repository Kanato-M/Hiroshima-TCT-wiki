# Standard settings

Standard measurement conditions and GUI defaults. The values come from `config.py` (standard conditions fixed by measurements in 2026-07) and the GUI source code.

## Sensor and bias

| Item | Value | Notes |
| --- | --- | --- |
| HV (sensor bias) | 190–195 V (at a sensor temperature of 33 °C). Changed with temperature | Matsusada P4L650-0.1, manual. The reference value in the GUI status bar is −185 V |
| LV (readout board) | 6.0 V | Rohde & Schwarz NGP800 series, manual |
| Power-on order | After the other equipment, LV → HV | The other equipment can be powered on in any order |
| Readout | C1=J8, C2=J7, C3=J6, C4=J5 | Standard sensor |

## Oscilloscope (contents of [★ Apply Standard Settings ★])

| Item | Value |
| --- | --- |
| Trigger | Source EXT, coupling DC, slope POS, level 410 mV |
| Timebase | 1 ns/div |
| Horizontal offset | −53 ns (display range +48 to +58 ns) |
| Vertical | C1–C4: 50 mV/div, offset +100 mV (display range −300 to +100 mV) |
| Input coupling | DC 50 Ω (D50) |
| Averaging | OFF |
| Sampling | 10 GS/s (setting in the analysis note) |

## Laser

| Item | Value | Notes |
| --- | --- | --- |
| Trigger source | internal (ts=0) | The laser is the reference clock |
| Rep. rate | **1 MHz** | Can be changed. Note that the GUI initial value is 1.25 MHz |
| TUNE | manual **50 %** | Adjusted to MIP-equivalent at 1 MHz (2026-10). 0 % strongest, 100 % weakest |
| Camera light | OFF during measurement | Operated by hand inside the dark room |
| TUNE optimization: single-CH target | 200 mV (PKPK) | |
| TUNE optimization: adjacent 2-ch sum target | 64 mV | MIP-equivalent value at the strip midpoint from the DESY beam test (2026) |
| TUNE optimization: tolerance / coarse sweep step | ±1 mV / 2 % | Fine sweep in 0.5 % steps |

## Stage

| Item | Value |
| --- | --- |
| Travel | 2 µm/pulse (X and Y confirmed, Z unconfirmed) |
| Speed | Start 1000 pps, max 10000 pps (= 20 mm/s), acceleration 200 ms |
| Jog steps | 2, 4, 10, 20, 50, 100, 500, 1000, 5000 µm |

## Alignment

| Item | Value |
| --- | --- |
| Z Focus | Range ±20 µm, step 2 µm, mode "Min amplitude (focus)" |
| Z measurement position | On silicon, 80 µm beside the electrode center |
| X-edge detection position | 50 µm beside the electrode center |
| X design offset (signal edge → frame edge) | 632 µm |
| Fine finishing | 1-pulse (2 µm) steps, 4 reads per point (median), up to 2 verifications |
| Full calibration points (standard sensor) | X = −5.3, −9.9 mm, Y = C2, C4 |

## Tilt coefficients (defaults for the standard sensor)

| Coefficient | Value |
| --- | --- |
| dZ/dX | −0.02 µm/mm |
| dZ/dY | +3.79 µm/mm |
| dY/dX | +7.67 µm/mm |

## Auto scan

| Item | Default |
| --- | --- |
| Fast axis Y | +750 → −2250 µm, 10 µm steps |
| Slow axis X | −632 → −9999 µm, 500 µm steps |
| Position step for 1D + TUNE | 50 µm (set automatically when the mode is selected) |
| TUNE sweep | 50 → 90 %, 5 % steps |
| Sequence | 2000 segments/point |
| Settle / Timeout | 0.5 s / 60 s |
| Z tracking | Coefficients (focus at origin) |
| Rot. tracking (Y) | ON |
| Snake (bidirectional) | ON (2D) |
| prefix | `scan` |
| Laser ON at start / Laser OFF at end | OFF / ON |

## Network and connections

| Device | Address |
| --- | --- |
| Control PC | 192.168.0.1 |
| Oscilloscope | 192.168.0.2 (VICP, SSH user `lcrydmin`) |
| Stage SHRC-203 | 192.168.0.3:9004 |
| Laser PiLAS DX | COM5 (19200 baud, 8N1, CR terminated) |
| Camera | Device index 0 |
| NAS (backup destination) | `quark-lab@10.28.57.235:/share/laser_data/<year>/` |
