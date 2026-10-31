# Output files

The files created by the GUI. Waveforms are saved on the scope; records and state are saved in `logs/` on the PC.

- `<scope_save_dir>` defaults to `C:\Users\LeCroyUser\Desktop\data` (on the scope)
- `<transfer_dest_dir>` defaults to `data` next to the GUI folder (on the PC)
- `<workspace>` is the parent of the `data` folder

| Location | File | Content |
| --- | --- | --- |
| Scope | `<scope_save_dir>\<year>\<YYYYMMDD>\scan_<datetime>_<tag>\C<n>--<prefix>--<number>.trc` | Auto scan waveforms. One file per point per CH |
| Scope | `<scope_save_dir>\<year>\<YYYYMMDD>\manual\` | Manual saves from the [Save] tab of the oscilloscope panel |
| PC | `logs/scan_<datetime>.csv` | Scan log (see below) |
| PC | `logs/tuneopt_<datetime>.csv` | TUNE optimization curve (phase, amplitude per CH, sum, spread). The header has the rep rate, target amplitude and the TUNE reached |
| PC | `logs/autofocus_<datetime>.csv` | Sweep curve of [Z Focus] |
| PC | `logs/alignment_<datetime>.csv` | Raw data of all sweeps in origin finding and full calibration |
| PC | `logs/sensor_calibration_<sensor ID>_<datetime>.json` | History of full calibrations and manual coefficient inputs |
| PC | `logs/transfer_<datetime>.csv` | Data retrieval record (path, size, status, whether deleted) |
| PC | `logs/gui_YYYYMMDD.log` | Communication log (all commands and responses) |
| PC | `logs/sensors.json` | Sensor dictionary |
| PC | `logs/stage_frame.json` | Machine coordinates of the logical origin (tracking across R:W) |
| PC | `captures/snap_<datetime>.png` | Camera snapshots |
| PC | `<transfer_dest_dir>\<year>\<YYYYMMDD>\scan_<datetime>_<tag>\` | Retrieved waveforms |
| PC | `<workspace>\results\<scan name>\` | Quick-analysis figures, `summary.csv`, `summary.txt`, `numbers.json`, `mean_waveforms.root` |
| PC | `<scan folder>.root` | ROOT file combining the `.trc` files (with [Convert to ROOT]) |

In simulation mode, `logs_sim/` is used instead of `logs/`.

## Waveform file names

Names like `C2--scan--00042.trc` consist of the **CH**, the **prefix** (default `scan`) and a **5-digit sequence number**.
The GUI numbers the files from 0, and the number always matches the `index` column of the scan log.

The datetime in the per-scan folder name `scan_<datetime>_<tag>` is the same as in the PC-side scan log `scan_<datetime>.csv`; use it to match the two.
The tag encodes the mode and axes, e.g. `1dY` (1D in Y), `2dYX` (2D with fast axis Y and slow axis X), `1dYtune`, `tune`.

## Scan log `logs/scan_<datetime>.csv` { #scan-log }

The first 4 lines are comments starting with `#`, line 5 has the column names, and from line 6 on there is one line per point.

| Line | Format | Content |
| --- | --- | --- |
| 1 | `# AC-LGAD 自動スキャン記録 <start datetime>` (English UI: `# AC-LGAD auto scan log <start datetime>`) | Title |
| 2 | `# mode= total_points= segments= settle_s= prefix= save_dir=` | Mode (1d, 2d, tune, 1d_tune), total points, segments, settle time, prefix, save folder on the scope |
| 3 | `# sensor= z_follow= z_points_um=[..]` | Sensor ID, Z tracking mode (`coef_origin`, `coef_current`, `points`, `off`), measured Z |
| 4 | `# tilt_correct= rot_correct= dzdx= dzdy= dydx= base_pos_um=(x,y,z)` | Whether Z tracking and rotation tracking are on, tilt coefficients [µm/µm], stage position at start |
| 5 | `index,x_um,y_um,z_um,tune_pct,status,timestamp` | Column names. `tune_pct` is filled only in modes that sweep TUNE |

- Line 4 contains commas, so by CSV rules the whole line is enclosed in double quotes.
- `x_um`, `y_um`, `z_um` are logical coordinates **after the tilt correction**, i.e. the target positions actually sent to the stage.
  If you need the nominal coordinates before correction, undo the correction using the coefficients and base position in line 4 (the quick analysis does this automatically).
- `status` is one of `ok`, `timeout` (acquisition did not finish in time at that point) or `aborted`.
