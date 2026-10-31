# Glossary

TCT
:   Transient Current Technique. Measures the transient current induced by laser-generated charge.

DUT
:   Device Under Test. The sensor being measured.

Depletion region
:   The region free of mobile carriers where the electric field is present. Charge created here contributes to the signal.

Full depletion voltage (\(V_\text{fd}\))
:   The voltage at which the whole sensor thickness is depleted.

Weighting field
:   The field obtained with the electrode of interest at 1 V and all others at 0 V. Used to compute induced currents with the Shockley–Ramo theorem.

CCE
:   Charge Collection Efficiency.

LGAD
:   Low Gain Avalanche Detector. A silicon sensor with an internal gain layer providing gains of about 10 to a few tens.

PIN diode
:   A basic diode sensor without a gain layer. Used as the reference for LGAD gain measurements.

MIP
:   Minimum Ionizing Particle.

Fluence
:   Number of particles per unit area. Often expressed as 1 MeV neutron equivalent (n<sub>eq</sub>/cm²).

Trapping
:   Capture of carriers by defect levels created by radiation damage.

Edge-TCT
:   TCT in which the laser enters through the side face of the sensor and is scanned in depth.

TPA-TCT
:   Two-Photon Absorption TCT. Uses two-photon absorption to obtain 3D resolution.

CFD
:   Constant Fraction Discrimination. Determines the time at a fixed fraction of the amplitude.

Bias tee
:   A component that separates or combines DC bias and RF signals.

## Terms specific to the Hiroshima setup and GUI

AC-LGAD
:   AC-coupled LGAD. A structure with a continuous n+ resistive layer and AC-coupled electrodes on top of the LGAD gain layer. The signal spreads through the resistive layer and is shared among several electrodes, giving a position resolution finer than the electrode pitch.

ePIC / bTOF
:   The ePIC experiment at the EIC (Electron-Ion Collider) and its barrel time-of-flight detector. It uses AC-LGAD strips.

laser_gui
:   The control GUI of the Hiroshima TCT setup (AC-LGAD Laser Scan Control GUI). Operates the scope, stage, laser and camera from a single window.

TUNE
:   The PiLAS laser setting [%] that sets the pulse intensity. 0 % is strongest, 100 % weakest. The optimal value changes with the repetition rate.

TRIG OUT
:   Trigger output of the laser controller. Fed into the external trigger (EXT) of the scope and used as the time reference.

Logical coordinates / logical origin
:   Sensor-based coordinates [µm] used by the GUI. The origin is X = frame (GND) edge, Y = electrode center of the reference CH, Z = focus.

Machine coordinates / mechanical origin
:   Coordinates measured from the mechanical origin (H:W) of the stage controller. The GUI tracks them across R:W.

H:W / R:W / M:W / L:W
:   SHRC-203 commands. H:W is mechanical homing, R:W resets the logical origin (sets the current position to 0), M:W is a relative move, L:W is stop.

M-ORG
:   Mechanical homing from the joystick JS-301. It breaks the GUI's machine coordinate tracking, so use H:W from the GUI instead.

Origin finding
:   Procedure that uses the laser signal as a ruler to find the focus, electrode center and frame edge automatically and set the logical origin.

Full calibration
:   Origin finding plus measurement of the sensor tilt coefficients dY/dX, dZ/dX and dZ/dY.

Sequence mode / segment
:   Scope mode that acquires many waveforms, one per trigger (segments), in one batch. 2000 segments per point is standard.

PAVA / PKPK
:   LeCroy waveform parameters. The GUI uses them to read amplitudes (PKPK is peak-to-peak).

TRIGTIME
:   The trigger time of each segment in a LeCroy sequence waveform. Used as the origin of arrival times.

VICP
:   LeCroy's own LAN control protocol (port 1861).

MIP-equivalent
:   State in which the laser pulse height is matched to the MIP signal measured in a beam test (e.g. 64 mV for the adjacent 2-ch sum at the strip midpoint in the DESY beam test).
