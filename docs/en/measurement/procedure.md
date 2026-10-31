# Procedure

Below is a typical TCT measurement flow. Add any procedures specific to our setup.

!!! danger
    Read [Safety](../introduction/safety.md) before you start.

## 1. Preparation

- [ ] Record the sensor ID, type and irradiation history (fluence, annealing)
- [ ] Mount the sensor on the board and check the signal, bias and ground connections
- [ ] Inspect the sensor surface under a microscope for scratches or dirt
- [ ] Write the date, purpose and operator in the measurement log

## 2. Environment

- [ ] Fix the sensor on the stage in the dark box and close the box
- [ ] Start flushing with dry air (or nitrogen)
- [ ] If cooling, start **only after the dew point is low enough**
- [ ] Wait until temperature and humidity are stable

## 3. IV check (laser OFF)

- [ ] Set the compliance of the HV power supply
- [ ] With the laser OFF, ramp up the bias slowly and check the leakage current
- [ ] Make sure there is no unexpectedly large leakage current or sudden increase (breakdown)

## 4. Laser

- [ ] Check that the interlock is active and enable the laser
- [ ] Start with a low intensity and look at the signal on the oscilloscope
- [ ] Set the oscilloscope trigger to the laser trigger output

## 5. Alignment

- [ ] Find the sensor (electrode edges, optical windows) with a coarse XY scan
- [ ] Determine the focus position \(z\) with a [focus scan](scans.md#focus-scan)
- [ ] Move the beam to the position you want to measure

## 6. Measurement

- [ ] Run the scan for your purpose (see [Scan types](scans.md))
- [ ] Re-measure a reference point from time to time to check for laser intensity drift
- [ ] Look at the waveforms for saturation, ringing and noise

## 7. Shutdown

- [ ] Stop the laser
- [ ] Ramp the bias down to 0 V in steps and turn the output OFF
- [ ] If cooled, **wait until it is back at room temperature** before opening the dark box
- [ ] Back up the data and complete the measurement log

## What to record

| Item | Example |
| --- | --- |
| Sensor | ID, type, thickness, fluence |
| Environment | Temperature, humidity (dew point) |
| Bias | Voltage, leakage current |
| Laser | Wavelength, intensity setting, repetition rate, illuminated side |
| Readout | Amplifier, cables, oscilloscope settings (range, number of averages) |
| Scan | Type, range, step |
