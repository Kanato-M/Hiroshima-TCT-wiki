# What is TCT?

The **Transient Current Technique (TCT)** creates electron–hole pairs inside a semiconductor detector with a short laser pulse.
As the carriers drift in the electric field, they induce a **transient current** on the electrodes.
This current is measured with time resolution using a fast amplifier and an oscilloscope.

## What can we learn?

The TCT waveform gives access to the following detector properties.

| Quantity | How |
| --- | --- |
| Collected charge | Integral of the current waveform |
| Full depletion voltage | Saturation point of the collected charge versus bias voltage |
| Electric field and drift velocity | Shape of the waveform, especially the prompt current in edge-TCT |
| Effective trapping time | Loss of charge in radiation-damaged sensors |
| Internal gain | Signal ratio between a sensor with a gain layer, such as an LGAD, and a PIN diode |
| Position dependence | Scanning the laser across pixels, strips and electrode edges |
| Timing | Rise time, timing jitter, and so on |

## Comparison with beam and source tests

- **Advantages**
    - The laser position, timing and intensity are fully controllable, so µm-scale position scans are possible.
    - It is simpler than a source or beam test, and many waveforms can be averaged to obtain low-noise data.
    - Changing the wavelength lets you create charge only near the surface or throughout the bulk.
- **Limitations**
    - The absolute amount of generated charge depends on the laser intensity, and intensity fluctuations become a systematic uncertainty.
    - Energy deposition fluctuations (Landau fluctuations) differ from those of charged particles.
      The timing resolution obtained with a laser is therefore not the same as for a MIP (minimum ionizing particle).
    - Metal electrodes block the laser, which limits where you can illuminate.

## Main TCT variants

| Method | Description |
| --- | --- |
| Top-TCT (front/back) | The basic method: the laser hits the front (or back) surface perpendicularly |
| Edge-TCT | The laser enters through a polished side face and is scanned in depth |
| TPA-TCT | Two-photon absorption creates charge only near the focus, giving 3D resolution |

See [Lasers and illumination](../principle/lasers.md) for details.

## Next

- [Safety](safety.md)
- [Signal formation](../principle/signal-formation.md)
