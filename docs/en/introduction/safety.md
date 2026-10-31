# Safety

!!! danger "Read this before you start measuring"
    This setup involves an **infrared laser**, **sensor bias voltage** and a **motorized stage** at the same time.
    If you are using the setup for the first time, always work with an experienced person present.

## Laser

- The light source is an NKT Photonics PiLAS DX (wavelength 1064 nm, pulse width 50 ps). **1064 nm is invisible.**
  The beam may be on even if you cannot see it.
- The light is guided through a polarization-maintaining fiber to the objective in the dark room. Never fire the laser with the fiber or objective removed.
- Pressing [Emission ON (ld=1)] in the GUI opens a confirmation dialog asking whether the interlock and optics setup have been checked.
  Check them, then press [Yes].
- **Before entering the dark room or opening the dark room (or dark box), always press [Emission OFF (ld=0)] and check that the status display shows emission OFF.**
  Fire the laser only while the dark room is closed and no light can escape. Until the interlock has been confirmed, follow this rule without relying on an interlock.
- **Closing the GUI does not stop the laser.** Always press [Emission OFF (ld=0)] before quitting the GUI.
- If anything seems wrong, press **[■ STOP ALL]** at the right end of the toolbar (aborts the task → laser OFF → stops the stage → scope STOP).

!!! question "To be confirmed"
    - Laser safety class (a comment in the GUI code says "Class 1", but this is unconfirmed)
    - Whether safety goggles are required, where they are stored, and their rated wavelengths
    - Whether there is an interlock and how it works (the laser panel shows `Interlock: OK`)
    - Laser safety officer

## Stage (collision with the objective)

- The 3-axis stage SHRC-203 needs a **Mechanical Home (H:W)** right after startup. **All axes move a long way** during this.
  First make sure the sample (readout board) and the objective cannot collide.
- The travel per pulse is 2 µm/pulse for X and Y (confirmed on the hardware). **2 µm/pulse for Z is not yet confirmed on the hardware.**
  On first use, jog in small steps and check the actual travel. Moving a long way with a wrong setting risks hitting the objective.
- At scan start, a warning appears if the Z of the first point is 30 µm or more away from the current Z. Always check that this is intended.
- [Stop (L:W)] and [EMERGENCY STOP] on the stage panel can be pressed even while a task is running.

!!! question "To be confirmed"
    Whether the µm/pulse of the Z axis has been confirmed on the hardware.

## Sensor bias voltage { #bias }

- The AC-LGAD gets HV −185 V (sensor bias), and the readout board gets LV 6.0 V (amplifier supply).
  **These are not controlled from the GUI.** The GUI status bar always shows reference values (`HV -185 V / LV 6 V`).
- Set the supply's **current limit (compliance)** before applying voltage.
- **Ramp the voltage in steps**; never change it in one jump.
- Until the supply-specific procedure (current limit value, step, speed) is documented in this wiki, **apply HV and LV only together with an experienced person**.
- Before changing cables or swapping the sensor (readout board), **ramp the voltage down to 0 V and turn the output OFF**.

!!! question "To be confirmed"
    - Models and operation of the HV and LV supplies, and the current limit (compliance) setting
    - Ramp-up and ramp-down step and speed

## Electrostatic discharge (ESD)

- The readout board (FNAL 16-channel board, amplifier Mini-Circuits GALI-S66) and the sensor are sensitive to static electricity.
  Wear a wrist strap when handling them.

!!! question "To be confirmed"
    The lab's ESD rules (where the wrist straps are, etc.).

## General notes for other TCT setups

The Hiroshima setup measures unirradiated sensors at room temperature (in a dark room). Setups that use cooling or irradiated samples need the following additional precautions.

??? note "Cooling and condensation"
    - While cooling, flush with dry air or nitrogen and make sure the **dew point is well below the measurement temperature**.
    - After measuring, **warm up to room temperature before** opening the dark box. Opening it cold causes condensation that can destroy sensors and electronics.

??? note "Irradiated samples"
    - Irradiated samples may be activated. Handle them according to your institute's regulations.
    - Mind the storage temperature to avoid unwanted annealing.
