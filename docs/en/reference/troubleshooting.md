# Troubleshooting

First look at the **communication log** at the bottom left of the GUI (`logs/gui_YYYYMMDD.log`) to see which command returned what.

## Cannot connect { #connection }

| Symptom | Fix |
| --- | --- |
| Scope: cannot connect, or shows "Connected" but settings are not applied | Set the resource name to `VICP::192.168.0.2::INSTR`. On the scope, check the control method (TCPIP (VICP) or LXI (VXI11)) and IP under Utilities → Utilities Setup → Remote. If ping works, VICP should connect. On connect, the `*IDN?` response is verified; if there is no response, VICP ⇔ VXI-11 is tried automatically before reporting an error |
| Stage: ping works but the connection times out | The TCP port number does not match the controller setting. The real hardware uses **9004**. Enter `192.168.0.3:<port>` in the address field. If a previous session is holding the connection, restart the controller. If it connects but does not respond, check the SHOT-compatible mode and the delimiter (CRLF) |
| Laser: cannot connect | Check the COM number in Device Manager (COM5 on the control PC). Check that the FTDI VCP driver is installed |
| Camera: no image | Try a different [Device] number (default 0) |

## No signal or strange signal { #no-signal }

| Symptom | Possible cause | Fix |
| --- | --- | --- |
| No signal at all | The laser is not emitting | Check that the laser panel status shows the red "Emission: ON" bar |
| | The beam is on an electrode (metal) | Jog Y by a few tens of µm to put the beam on silicon |
| | No bias applied | Check HV −185 V and LV 6.0 V |
| | Wrong horizontal offset | Set it to −53 ns with [★ Apply Standard Settings ★] |
| Waveforms update but are flat | After a rep-rate change, TUNE fell below the lasing threshold (TRIG OUT keeps coming) | Redo [TUNE optimization](../measurement/tune.md). For a quick check, set TUNE to auto |
| Amplitude differs from expectation | The TUNE–amplitude relation changes with rep rate, camera lighting and alignment | Match it with TUNE optimization |
| In a scan the signal on the electrode does not drop to 0, or the rise at the electrode edge is gradual | Defocus, or a coordinate offset | Redo origin finding (check Z) |

## Stage and alignment

| Symptom | Fix |
| --- | --- |
| "Machine coordinates: unknown" is shown and you cannot go to the saved origin | The GUI lost track of the machine coordinates (M-ORG from the JS-301, controller restart, interrupted homing, etc.). Run [Mechanical Home (H:W)] from the GUI |
| Origin finding stops with "No clear dip" or "No clear edge" | Check that the beam is on the reference strip, 50–200 µm inside its end, that the laser is ON, and that a signal is visible; then run again |
| "Valley too shallow", or the Z valley cannot be found | Widen the Z range to ±30–50 µm, or roughly focus with the camera first, then run again |
| The Z residual of the full calibration exceeds a few µm, or the coefficients changed a lot since last time | Run it again, or use the "Measured Z" mode for Z tracking in the scan |
| After origin finding or full calibration the right column becomes wider, or the tilt coefficient display is stale | Bug in an older version (fixed in the 2026-10 revision). Use the latest version |
| The jog direction does not match the camera image | The camera or stage mounting orientation was changed. Fix `stage_screen_dir` in `config_local.json` |

## Scans

| Symptom | Fix |
| --- | --- |
| Aborted with "saved files are not increasing" | The scan stops automatically if the number of files on the scope does not increase for 2 points in a row. Check the scope's Save Waveform setup and the save folder |
| `timeout` lines in the scan log | Acquisition did not finish in time. Check that the laser is ON and that the EXT trigger is firing. Also check that [Averaging OFF] has not been unchecked |
| Warning at start that "Z is 30 µm or more away" | Check that this is intended. Origin finding may not have been done, or the Z tracking mode may be wrong |

## Data retrieval

| Symptom | Fix |
| --- | --- |
| SSH connection fails | Check that the scope is on and responds to ping, that the OpenSSH server on the scope is running, and that the password is correct |
| A "conflict" is reported | A file with the same name but a different size exists on the PC. It is not overwritten; check which one is correct and clean up manually |
| Cannot transfer during a scan | By design (mutually exclusive). Transfer after the scan finishes |

## Camera

| Symptom | Fix |
| --- | --- |
| The image is completely dark or completely white | Adjust [Gain] on the camera panel (the communication log shows the actual value, so you can see whether it took effect; if the actual value does not change, the camera does not support it). If that does not help, adjust the illumination brightness and the lens aperture |

## General TCT problems

??? note "Problems common to other TCT setups"

    | Symptom | Possible cause | Check / fix |
    | --- | --- | --- |
    | Oscillating waveform (ringing) | Impedance mismatch, long cables | 50 Ω termination, shorten cables and wire bonds |
    | Saturated or clipped waveform | Input range of amplifier or scope exceeded | Widen the vertical range, reduce the laser intensity |
    | Pulse shape changes with intensity | Too much charge generated (plasma effect) | Reduce the laser intensity |
    | Large noise | Ground loops, external pickup | Use a single ground point, check shielding |
    | Signal drifts over time | Laser intensity or temperature fluctuations | Re-measure a reference point regularly |
    | Leakage current jumps | Breakdown | Ramp down immediately |

!!! note "Please contribute"
    Add any problems you actually ran into, and how you solved them, to this page ([Contributing](contributing.md)).
