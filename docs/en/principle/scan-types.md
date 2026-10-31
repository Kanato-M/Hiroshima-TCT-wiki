# Scan types (general)

!!! info "This page is general"
    This page introduces scans commonly done with TCT. For the scans actually done on the Hiroshima setup (AC-LGAD + laser_gui),
    see [Auto scan](../measurement/scans.md).
    Note in particular that **focus is determined the opposite way from the general case**. On the Hiroshima AC-LGAD, the gain is
    suppressed as the beam comes into focus and the amplitude becomes **smaller**, so "the Z of minimum amplitude = focus"
    (see [Alignment](../measurement/alignment.md#focus)).

## Focus scan { #focus-scan }

A scan to find the position of the beam focus (beam waist).

1. Scan in \(x\) across the edge of a metal electrode (knife edge).
2. Fit the signal (amplitude or charge) as a function of position with an error function.

    \[
    S(x) = \frac{A}{2}\left[1 + \operatorname{erf}\!\left(\frac{x - x_0}{\sqrt{2}\,\sigma}\right)\right] + C
    \]

3. Repeat at each position along the beam axis \(z\) and take the \(z\) with the smallest \(\sigma\) as the focus.

The beam width is sometimes quoted as \(\text{FWHM} = 2\sqrt{2\ln 2}\,\sigma \approx 2.355\,\sigma\).

!!! tip
    In edge-TCT you can evaluate the focus size in the same way using the front and back surfaces (the edges in depth).

## Position scan (XY scan)

Scan the illumination point in one or two dimensions to study the position dependence of the response.

- Charge sharing between strips or pixels
- Uniformity of the LGAD gain layer and the no-gain region between pads (inter-pad distance)
- Behavior near electrode edges and guard rings

!!! note
    The laser does not pass through metal electrodes, so the signal is zero there.
    Overlay the map with the layout drawing when interpreting it.

## Bias scan

Acquire waveforms at the same position while changing the bias voltage.

- **Collected charge vs. bias**: estimate the full depletion voltage from where the charge saturates.
- **Pulse shape vs. bias**: check how the field profile changes.
- **LGAD gain**: from the charge ratio to a PIN diode measured under the same conditions, \(G = Q_\text{LGAD} / Q_\text{PIN}\).

!!! warning
    Always monitor the leakage current while raising the voltage, and stop at any sign of breakdown.

## Edge-TCT depth scan

The laser enters through the side face of the sensor and is scanned in depth \(y\). It is usually repeated at several bias voltages.

- **Collected charge** at each depth → charge collection profile (extent of the depletion region)
- **Prompt current** at each depth → drift velocity profile (field profile)

See [Lasers and illumination](../principle/lasers.md#edge-tct) for details.

## Temperature scan

Measure at different temperatures to study the temperature dependence of leakage current, trapping, LGAD gain (higher at lower temperature) and so on.
After changing the temperature, wait long enough for it to stabilize.

## Laser intensity scan

Change the laser intensity and check that the signal scales linearly with it.
Too high an intensity distorts the waveform through the plasma effect or amplifier saturation, so it is worth doing once before the main measurement.
