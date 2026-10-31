# Safety

!!! danger "Read this before you start measuring"
    TCT measurements involve **lasers**, **high voltage** and **low temperature (condensation)** at the same time.
    If you are using the setup for the first time, always work with an experienced person present.

## Laser

- Pulsed lasers used for TCT include invisible (infrared) wavelengths. **The beam may be on even if you cannot see it.**
- Confirm that the dark box and interlock work correctly before enabling the laser.
- Always stop the laser output (or close the shutter) before opening the dark box.
- Never enable the laser with the fiber or collimator disconnected.
- When aligning optics, wear safety goggles rated for the wavelength in use.

!!! note "Lab-specific information (to be filled in)"
    - Laser class, wavelength and maximum power: **TODO**
    - Location of safety goggles and their rated wavelengths: **TODO**
    - Laser safety officer: **TODO**

## High voltage

- Sensors may be biased at several hundred volts up to about 1 kV.
- Always set the power supply **compliance (current limit)** before raising the voltage.
- Before changing cables or swapping sensors, **ramp the voltage down to 0 V and turn the output OFF**.
- Ramp the voltage slowly, for example about 10 V per step, following the sensor specification.

## Cooling and condensation

- Irradiated sensors are often measured cold, for example at −20 °C, to suppress the leakage current.
- While cooling, flush the dark box with dry air or nitrogen and make sure the **dew point is well below the measurement temperature**.
- After measuring, **warm up to room temperature before** opening the dark box. Opening it cold causes condensation that can destroy sensors and electronics.

## Electrostatic discharge (ESD)

- Wear a wrist strap when handling sensors and amplifiers.
- Check the bias-tee connection so that high voltage never reaches the amplifier input directly.

## Irradiated samples

- Irradiated samples may be activated. Handle them according to your institute's regulations.
- Mind the storage temperature of irradiated samples to avoid unwanted annealing.
