# Lasers and illumination

## Wavelength and absorption length

The absorption length of light in silicon depends strongly on the wavelength.
TCT uses this to choose where charge is created.

| Wavelength (example) | Absorption length at room temperature (approx.) | Charge generation | Main use |
| --- | --- | --- | --- |
| Red (≈ 660 nm) | a few µm | Near the surface only | Separate electron/hole measurements, field profile studies |
| Infrared (≈ 1064 nm) | ≈ 1 mm | Almost uniform through the thickness | MIP-like measurements, edge-TCT |
| Infrared (≈ 1550 nm, fs pulses) | Transparent for single photons | Only near the focus (two-photon absorption) | 3D measurements with TPA-TCT |

!!! warning "Temperature dependence"
    The absorption length around 1064 nm is close to the band edge and therefore temperature-sensitive.
    When comparing different temperatures, watch out for changes in the generated charge.

## Illumination methods

### Top-TCT (front/back illumination)

The basic method, where the laser hits the front or back surface perpendicularly.

- You cannot illuminate through metal electrodes, so aim at openings in the metallization (optical windows) or between strips.
- With a red laser you can observe the signal of a single carrier type.
- With an infrared laser charge is created through the whole thickness, giving a MIP-like signal.

### Edge-TCT

An infrared laser enters through a polished **side face** of the sensor, parallel to the surface.
Scanning the focus in depth \(y\) gives the **charge collection and drift velocity as a function of depth**.

Right after the laser pulse (\(t \approx 0\)) the carriers have not moved yet and trapping is negligible.
The current at that time, the **prompt current**, is

\[
I(y, t \approx 0) \propto \big(v_e(y) + v_h(y)\big)\,E_w(y)
\]

which allows the drift velocity, and hence the field profile, to be estimated as a function of depth [^kramberger2010].

!!! note "Side-face preparation"
    A rough diced edge scatters light and broadens the focus.
    Samples for edge-TCT should ideally have a polished side face.

### TPA-TCT

With a femtosecond laser whose photon energy is below the silicon band gap (≈ 1550 nm),
single photons are not absorbed, and **two-photon absorption happens only near the focus**, where the photon density is high.
The charge generation point can therefore be placed in three dimensions by moving the focus.

## Beam spot and focus

- The position resolution is set by the beam focus size (typically a few µm to a little over 10 µm).
- The focus position is found with a [focus scan](../measurement/scans.md#focus-scan).
- If the laser intensity is too high, the large amount of charge screens the electric field (plasma effect) and distorts the waveform.
  Check that the waveform shape does not change when you vary the intensity.

[^kramberger2010]: G. Kramberger *et al.*, "Investigation of Irradiated Silicon Detectors by Edge-TCT", *IEEE Trans. Nucl. Sci.* **57** (2010) 2294.
