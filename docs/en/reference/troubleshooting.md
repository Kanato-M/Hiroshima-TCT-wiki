# Troubleshooting

| Symptom | Possible cause | Check / fix |
| --- | --- | --- |
| No signal at all | Laser not firing, shutter closed | Check laser status, interlock and fiber connection |
| | Beam hits a metal electrode | Find optical windows or inter-strip gaps with an XY scan |
| | No bias applied | Check the supply output, bias tee and cables |
| | Wrong trigger settings | Check trigger source, level and horizontal position |
| Small signal | Out of focus | Redo the focus scan |
| | Not fully depleted | Increase the bias (watch the leakage current) |
| Oscillating waveform (ringing) | Impedance mismatch, long cables | 50 Ω termination, shorten cables and wire bonds |
| Saturated or clipped waveform | Input range of amplifier or scope exceeded | Widen the vertical range, reduce the laser intensity |
| Pulse shape changes with intensity | Too much charge generated (plasma effect) | Reduce the laser intensity |
| Large noise | Ground loops, external pickup | Use a single ground point, check shielding |
| Signal drifts over time | Laser intensity or temperature fluctuations | Re-measure a reference point regularly, wait for temperature to stabilize |
| Leakage current jumps | Breakdown, condensation | Ramp down immediately, check the dew point |
| Supply trips on compliance | Limit too low, damaged sensor | Check the sensor with an IV measurement, revisit the limit |
| Condensation during cooling | Insufficient flushing | Ramp down, dry the box, then warm up |

!!! note "Please contribute"
    Add any problems you actually ran into, and how you solved them, to this table.
