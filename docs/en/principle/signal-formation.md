# Signal formation

## Generation and drift of electron–hole pairs

When laser light is absorbed in a semiconductor, it creates electron–hole pairs.
In the region depleted by the bias voltage, electrons and holes drift in opposite directions and induce a current on the electrodes until they are collected.

The drift velocity is set by the electric field \(E\) and the mobility \(\mu\).

\[
\vec{v}_{e,h} = \pm \mu_{e,h}(E)\,\vec{E}
\]

The mobility decreases at high field, so the drift velocity in silicon saturates
(on the order of \(10^7\) cm/s for both electrons and holes at room temperature).

## The Shockley–Ramo theorem

The current induced on an electrode is given by the **Shockley–Ramo theorem**.
When a charge \(q\) moves with velocity \(\vec{v}\) at position \(\vec{r}\), the current induced on the electrode of interest is

\[
I(t) = q\,\vec{v}(t)\cdot\vec{E}_w\big(\vec{r}(t)\big)
\]

Here \(\vec{E}_w\) is the **weighting field**: the field obtained by setting the electrode of interest to 1 V, all other electrodes to 0 V, and removing all space charge.

For a parallel-plate geometry (pad diode) of thickness \(d\), \(E_w = 1/d\), so

\[
I(t) = \frac{q}{d}\,\big(v_e(t) + v_h(t)\big)
\]

The current waveform therefore carries **information on the drift velocity, and hence on the electric field profile**.

!!! tip "Segmented electrodes"
    For strips and pixels the weighting field is concentrated near the electrode,
    so charges moving close to the electrode induce larger signals.
    Neighboring electrodes may show bipolar induced currents that look like crosstalk.

## Collected charge

The collected charge is the time integral of the induced current.

\[
Q = \int I(t)\,dt
\]

In practice the oscilloscope records a voltage \(V(t)\) across a 50 Ω termination, so taking the amplifier gain \(G\) into account,

\[
Q = \frac{1}{G\,R}\int V(t)\,dt, \qquad R = 50\ \Omega
\]

Take \(G\) from the amplifier datasheet or from a calibration measurement.

## Single-sided injection with a red laser and the pulse shape

A red laser (for example around 660 nm) is absorbed within a few µm of the surface, so charge is created almost only at the surface.

- One carrier type is collected immediately by the nearby electrode.
- Only the other carrier type drifts through the full sensor thickness.

By choosing which side to illuminate you can therefore observe an **electron-only** or **hole-only** signal.
In a parallel-plate geometry \(I(t) \propto v(E(x(t)))\), so the waveform maps the depth profile of the electric field onto the time axis.

!!! example "Example: a p⁺–n–n⁺ diode"
    Illuminating the p⁺ side with a red laser, holes are immediately collected by the p⁺ electrode and electrons drift toward the n⁺ side (electron signal).
    Illuminating the n⁺ side, holes drift toward the p⁺ side (hole signal).
    In a depleted n-type bulk the field is highest at the p⁺ side and decreases linearly toward the n⁺ side.
    The electron signal therefore decreases with time and the hole signal increases with time.

## Radiation damage and trapping

Defect levels created by irradiation capture (trap) carriers and reduce the signal.
With an effective trapping time \(\tau_\text{eff}\), the number of drifting carriers decreases as

\[
N(t) = N_0\,\exp\!\left(-\frac{t}{\tau_\text{eff}}\right)
\]

and the induced current is reduced by the same factor.
\(\tau_\text{eff}\) becomes shorter roughly in inverse proportion to the fluence.

In irradiated sensors the space-charge distribution also changes,
and a **double-peak** structure with high fields near both electrodes can appear.

## Related pages

- [Lasers and illumination](lasers.md)
- [Waveform analysis](../analysis/waveform.md)
