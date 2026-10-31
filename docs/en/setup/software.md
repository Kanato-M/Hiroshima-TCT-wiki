# Software

## Roles

| Software | Role |
| --- | --- |
| DAQ (data acquisition) | Controls the stages, power supply and oscilloscope, automates scans and saves waveforms |
| Analysis | Extracts amplitude, charge and timing from saved waveforms and makes plots |

## DAQ (to be filled in)

!!! note "TODO"
    Describe the DAQ software used in the lab.

    - Software name and repository location: **TODO**
    - How to start it: **TODO**
    - Output file format (e.g. binary, CSV, ROOT, HDF5): **TODO**
    - Configuration file format: **TODO**

### Instrument control example (Python + PyVISA)

Most instruments can be controlled with SCPI commands. Below is a minimal example that sets the voltage of an HV power supply.
Commands differ between models, so always check the manual.

```python
import pyvisa

rm = pyvisa.ResourceManager()
hv = rm.open_resource("GPIB0::24::INSTR")  # change the address for your setup
print(hv.query("*IDN?"))

hv.write(":SOUR:FUNC VOLT")
hv.write(":SENS:CURR:PROT 10e-6")  # compliance 10 µA
hv.write(":SOUR:VOLT:LEV -100")    # bias voltage [V]
hv.write(":OUTP ON")
print(hv.query(":READ?"))
```

!!! warning
    Do not change the voltage in one jump. Always ramp it up and down in steps.

## Analysis environment

Recommended packages for analysis in Python:

```bash
pip install numpy scipy matplotlib pandas
# to read ROOT files
pip install uproot awkward
# to read HDF5 files
pip install h5py
```

See [Waveform analysis](../analysis/waveform.md) for concrete examples.

## Data management

- Include the **date, sensor ID, temperature, bias voltage and scan type** in file names.
    - Example: `2026-10-31_SensorA_-20C_bias-scan.h5`
- Save the measurement conditions (laser intensity, amplifier, temperature, humidity, etc.) as metadata next to the data.
- Record the file name, purpose and observations in the measurement log (e.g. an electronic logbook).
