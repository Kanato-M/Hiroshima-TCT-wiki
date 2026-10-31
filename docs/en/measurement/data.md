# 5. Data Retrieval and Quick Analysis

## Data retrieval (scope → PC) { #transfer }

Scan waveforms are first saved on the oscilloscope's disk. To move them to the PC, use the **[Data Retrieval] tab** of the auto scan panel.

![[Data Retrieval] tab](../images/gui/scan-transfer.png){ width="520" }

### Procedure

1. Specify [Date (YYYYMMDD)] (default is today; past dates can also be specified).
2. Check [PC destination] (default is `data` next to the GUI folder).
3. If needed, check [Delete on scope after transfer] (default **OFF**; see below).
4. Press **[Start Transfer]**.
5. Enter the SSH password when asked (user name `lcrydmin`; ask the administrator for the password).
   The password is not stored and must be entered for each transfer (within the same session the previous value is filled in).

The whole scope-side folder `<scope_save_dir>\<year>\<YYYYMMDD>` is copied over SSH/SFTP to `<transfer_dest_dir>\<year>\<YYYYMMDD>` on the PC.

### Details

- **Duplicate skipping**: files with the same name and size already on the PC are skipped automatically. If all files in a scan folder are already retrieved,
  it reports "skipped n already-retrieved folders". **If you stop midway, just run it again.**
- **Conflicts**: files with the same name but a **different size** are not overwritten and are reported as "conflicts".
- **Verification**: file sizes are compared after transfer.
- **Deletion**: with [Delete on scope after transfer] ON, the original files on the scope are deleted and empty folders cleaned up only if there were no errors, conflicts or aborts
  and the size check succeeded for all files (to free scope disk space).
- The results (path, size, status, whether deleted) are saved in `logs/transfer_<datetime>.csv`.
- **Transfer is not possible while a scan is running** (exclusive in the GUI). Also avoid transferring while the scope autosave is active.

!!! tip "Retrieving from a terminal"
    GUI retrieval uses the same SSH/SFTP protocol as running `scp -r lcrydmin@192.168.0.2:C:\...\<YYYYMMDD> .` in a terminal.

!!! question "To be confirmed"
    The final storage location of retrieved data (backup to a NAS, etc.), and when the data on the scope may be deleted.

## Quick analysis ([Analysis] tab) { #analysis }

A quick analysis to check data quality right after acquisition. Run it in the **[Analysis] tab** of the main window.
The analysis runs in a separate process, so **it can be run during a measurement**.

![[Analysis] tab](../images/gui/analysis-tab.png)

*Screen right after analyzing a real-data 1D scan (scan_20260824_195826_1dY).*

### Procedure

1. Select a retrieved scan from the list at the top left ([Refresh] reloads the list; [Browse...] lets you choose any folder).
2. Select options.
    - [Convert to ROOT]: combines the `.trc` files into one ROOT file
    - [Rebuild cache (--force)]: rebuilds the reduced cache (`reduced.npz`)
    - [No raw waveforms (features only)]
    - [Mode] (manual type selection), Hit thr, Noise max, ToT thr, Workers, X offset / Y offset, Pitch, Body X range (same as the command-line options below)
3. Press **[Run analysis]**. Progress and the run log are shown; stop with [Abort].
4. When finished, figures are shown on the right and can be switched with [Figure]. [Open results] opens the output folder.

The scan type is detected automatically from the scan log.

| Type | Analysis code | Main outputs |
| --- | --- | --- |
| 1dX (along the strip) | `analyze_1dx.py` | Amplitude, rise time, jitter, time resolution, propagation along the length |
| 1dY (across the strips) | `analyze_1dy.py` | Amplitude, rise time, jitter, position resolution, time resolution, transverse propagation velocity |
| 1dZ (focus) | `analyze_1dz.py` | Optimal Z (`best_z.txt`) |
| 2dYX (X × Y) | `analyze_2dyx.py` | All of the above (maps, in-gap, along the length) |
| tune (intensity sweep at a fixed position) | `analyze_tune.py` | TUNE for MIP-equivalent (pulse height 60–75 mV), σt vs. amplitude |
| 1d_tune (position × intensity) | `analyze_1d_tune.py` | σt vs. amplitude, intensity dependence of position resolution |

All types produce `fig_waveforms.png` (mean waveform, raw waveforms and amplitude distribution at the maximum-amplitude point), `summary.txt` and `numbers.json`.

### Output locations

| Output | Location |
| --- | --- |
| Figures, `summary.csv`, `summary.txt`, `numbers.json`, `mean_waveforms.root` | `<workspace>\results\<scan name>\` |
| ROOT file (with [Convert to ROOT]) | `<scan folder>.root` (next to the scan folder) |

`<workspace>` is the parent folder of the destination `data` folder.

!!! warning "Time origin in the ROOT file"
    When computing arrival times from the ROOT file, use the per-segment `trig_offset` branch as the time origin.
    Using `horiz_offset` from the waveform header adds about 29 ps of fake jitter.

!!! note
    All numbers are preliminary internal values. Time resolution is relative to the laser TRIG OUT and includes a common floor \(\sigma_\text{ref} \approx 12.5\) ps.

### Running from the command line

```bat
cd <GUI folder>\analysis

:: Detect the type automatically and analyze (scan name or path)
python run_analysis.py scan_20260826_155255_1dX

:: Analysis + ROOT conversion
python run_analysis.py scan_20260826_152932_2dYX --root

:: Rebuild the cache (reduced.npz)
python run_analysis.py scan_... --force
```

| Option | Default | Description |
| --- | --- | --- |
| `--mode` | auto | Force `1dX` `1dY` `1dZ` `2dYX` `tune` `1d_tune` |
| `--x-off` / `--y-off` | 0 | Offset from log coordinates to sensor coordinates [µm]. **Scans before 2026-09-07 have a different X origin**, so pass `--x-off -632` |
| `--pitch` | 500 | Strip pitch [µm] |
| `--body-x LO HI` | −9900 −700 | X range of the "body" [µm] |
| `--hit-thr` / `--noise-max` / `--tot-thr` | 8 / 4 / 10 mV | Hit lower limit, noise upper limit, ToT threshold |
| `--workers` | 8 | Number of parallel workers for reduction |
| `--log` / `--results` | auto | Specify the log CSV and output folder |

For detailed analysis definitions, see `analysis/README.md` and the analysis notes (matome). General concepts of waveform analysis are in [Waveform analysis](../analysis/waveform.md).
