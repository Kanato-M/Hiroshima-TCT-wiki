# analysis — quick-look analysis package for acquired data

Quick-look analysis of scan data (LeCroy sequence .trc) taken with laser_gui_v4_en, plus conversion to a single ROOT file.

On 2026-10-05 this package was rewritten so that it reproduces the results of the analysis note **matome**
(`laser_test\analysis\results\mip\combined\matome`) with the same definitions. Each scan kind
(1dX / 1dY / 1dZ / 2dYX / tune / 1d_tune / pixel) allows different analyses, so **there is one analysis script per kind**.

Update of 2026-10-16: (1) the kind `pixel` for **pixel sensors** (2x2 channel readout) was added (auto-detected from
`sensor_type=pixel` in the scan log). (2) **2dYX runs the template-fit analysis end to end** and writes the paper figures
01–09 (same content as `results\paper_plots_20261013`) to `results\<scan name>\paper_plots\`
(`macro\template_timing\tt_pipeline.py`; reads the .trc files directly, no ROOT conversion).

- The **code** lives in this folder (`laser_gui_v4_en\analysis\`)
- **Data and outputs live outside the repository** (so that the data are shared with the Japanese GUI):
  - Input: `<workspace>\data\<year>\<YYYYMMDD>\scan_<datetime>_<tag>\C?--scan--NNNNN.trc`
    and the scan log `scan_<datetime>.csv` (searched automatically, first in `logs\` of the data folder, then in `laser_gui_v4_en\logs\`)
  - Analysis results (figures, CSV, summary, numbers.json): `<workspace>\results\<scan name>\`
  - ROOT file: `<scan folder>.root` (next to the scan folder)

Dependencies: numpy, matplotlib, uproot (ROOT conversion and ROOT output only; the quick-look and template-fit analyses work without it).
No scipy needed (the Gaussian fit is implemented here). The template-fit and pixel analyses live in
`<workspace>\macro\template_timing\` (shared by the Japanese and English GUIs; their messages are partly in Japanese).
Without that folder only the quick-look figures are made.

> All numbers are internal, preliminary values (the analysis is still in progress). The time resolution is measured
> against the laser TRIG OUT and includes the common floor σ_ref ≈ 12.5 ps.

## Usage (GUI)

Run it from the **"Analysis" tab** of the control GUI (`python main.py`) (gui/analysis_panel.py).
Pick a scan from the list → set the options → "Run analysis". `run_analysis.py` runs as a subprocess, so it does not
block the measurement; abort, progress bar, log view, figure preview after completion and "Open results" are available.
The scan kind is detected automatically (you can also set it by hand with "Mode").
Unchecking "Also run the template-fit analysis (paper figures 01–09 / pixel pix01–06)" (default ON) makes only the quick-look figures (`--no-template`).

## Usage (command line)

```bat
cd laser_gui_v4_en\analysis

:: Detect the scan kind automatically and analyze (a scan name or a path both work)
python run_analysis.py scan_20260826_155255_1dX
python run_analysis.py ..\..\data\2026\20260824\scan_20260824_195826_1dY

:: Scans before 2026-09-07 have a different X origin, so give the offset to sensor coordinates
python run_analysis.py scan_20260824_202544_2dYX --x-off -632

:: Analysis + ROOT conversion (with raw waveforms; saved to <scan_dir>.root)
python run_analysis.py scan_20260826_152932_2dYX --root

:: Rebuild the cache (reduced.npz) (also redoes every stage of the template-fit analysis)
python run_analysis.py scan_... --force

:: Quick-look figures only (skip the template-fit analysis)
python run_analysis.py scan_... --no-template

:: The template-fit analysis alone (results in results\<scan name>\paper_plots\)
cd ..\..\macro\template_timing
python tt_pipeline.py scan_20261007_000750_2dYX --workers 12
python tt_pixel.py scan_20261015_193055_2dYX --workers 12
```

Main options (see `--help`):

| Option | Default | Meaning |
|---|---|---|
| `--mode` | auto | force `1dX` `1dY` `1dZ` `2dYX` `tune` `1d_tune` `pixel` |
| `--no-template` / `--force-template` | — | skip / redo every stage of the template-fit analysis of 2dYX / pixel (`--force` redoes it too) |
| `--x-off` / `--y-off` | 0 | offset from log coordinates to sensor coordinates (X = 0 is the outer frame edge on the readout side) [µm] |
| `--pitch` | 500 | strip pitch [µm] |
| `--body-x LO HI` | −9900 −700 | X range of the strip "body" [µm]. Used for the fits along the strip and for the representative values (all columns if no column is in the range) |
| `--hit-thr` / `--noise-max` / `--tot-thr` | 8 / 4 / 10 mV | lower limit of a hit, upper noise limit of the quality cut, ToT threshold |
| `--n-boot` | 30 | number of bootstrap samples for the MPV error (matome uses 100. The MPV value does not depend on this number) |
| `--workers` | 8 | number of parallel processes for the reduction |
| `--log` / `--results` | auto | set the log CSV / the output location |

## Scan kinds and analysis items

Analysis items of matome: 1 amplitude and charge / 2 rise time / 3 jitter / 4 position resolution / 5 time resolution /
6 propagation time and velocity / 7 amplitude and propagation.

| Kind | Script | Possible items | Output figures |
|---|---|---|---|
| **1dX** (along the strip) | `analyze_1dx.py` | 1, 2, 3, 5, 6 (along the strip: Elmore delay ρc, slopes of amplitude, rise time and σ_t) | fig1, fig2, fig3, fig5, fig6 |
| **1dY** (across the strips) | `analyze_1dy.py` | 1–5, 6 (transverse v_eff), 7 | fig1–fig5, fig6_7 |
| **1dZ** (focus) | `analyze_1dz.py` | best Z (for tuning; not an analysis item) | fig1_focus, `best_z.txt` |
| **2dYX** (X × Y) | `analyze_2dyx.py` | all of 1–7 (maps, in the gap, along the strip) | fig1–fig5, fig6_7 |
| **tune** (intensity sweep at a fixed position) | `analyze_tune.py` | amplitude dependence: TUNE for a MIP-like signal (pulse height 60–75 mV), 1/A law, σ_t vs amplitude | fig1, fig2, fig3, fig5, fig8 |
| **1d_tune** (position × intensity) | `analyze_1d_tune.py` | 1/A law (the source of the matome reference value), σ_t vs amplitude, intensity dependence of the position resolution and the effective pitch | fig1, fig2, fig2b, fig3, fig4, fig5 |
| **pixel** (2D of a 2x2-channel pixel sensor) | `analyze_pixel.py` → `tt_pixel.py` | amplitude maps (A_lead, A_total, A_sub, A_lead+A_sub, A_lead/A_total), amplitude distributions, rise time, jitter, time resolution of 2/3/4-pixel combinations (best per position), position resolution of 2/3/4-pixel centroids (best per axis), propagation | fig1_pixel_amplitude, fig2_pixel_timing, pix01–06, `pixel\` |

The position resolution and the transverse propagation velocity need a scan in Y (1dX cannot give them). The Elmore delay
along the strip needs a scan in X (1dY cannot give it). The figure numbers follow the analysis item numbers.

Outputs common to all kinds:
- `fig_waveforms.png`: mean waveforms, raw waveforms and amplitude distribution at the max-amplitude point
- `summary.txt`: scan conditions and main numbers (the key names are the same as in the `numbers.json` of matome, e.g. `a5_sigma_band_ps`)
- `numbers.json`: main numbers (value, err, unit, note)
- `summary.csv`: all per-point quantities (per channel, event level, pair)
- `mean_waveforms.root`: mean waveform and features of each point

### What each figure shows

**2dYX** (`analyze_2dyx.py`)
- fig1 amplitude and charge: maps of A_lead / A_lead+A_sub / Q_tot, profile folded into one gap (A_total for reference), along the strip
- fig2 rise time: maps of 10–90 % / 40–60 %, dependence on the distance from the strip, along the strip
- fig3 jitter: map, **2D distribution of Y × per-event jitter + MPV of each row**, in the gap (leading / sub-leading), amplitude dependence and 1/A law
- fig4 position resolution: η calibration curve, dependence of the effective pitch along the strip, map of the position resolution (test), **2D of Y × position resolution + MPV** (with the noise expectation)
- fig5 time resolution: maps of σ_lead / σ_2strip, **2D of Y × σ + MPV**, in the gap, along the strip, amplitude dependence
- fig6_7 propagation: ToA along the strip with the Elmore fit, residuals, ΔT(u), dependence of v_eff along the strip, delay of a single channel, delay vs amplitude (τ ln(A0/A))

**Template-fit analysis of 2dYX** (`macro\template_timing\tt_pipeline.py`, 2026-10-16): runs automatically after the quick-look
analysis and writes the paper figures 01–09 (PNG + PDF) with `numbers.json` to `results\<scan name>\paper_plots\<scan name>\` and
`paper_plots\key_numbers.csv`. The GUI figure list shows them as `paper_NN_*.png`. Steps and run time: ① `tt_stage1.py` fits a pulse
template per point on the .trc files (ToA = CFD50 of the fitted function, amplitude = fitted A; 16000 points × 2000 segments ≈ 30 min
with 12 workers) → `timing_template_cfd50\` ② `tt_analyze.py` time resolution (leading / 2-strip methods / optimal method per position)
③ `tt_poscent.py` position resolution of the uncalibrated amplitude centroid (A / A² weights) ④ `tt_amp_fluct.py` Gaussian mean / σ of the
amplitude distributions and the noise floor ⑤ `tt_paper.py` figures 01–09. From the second run ①–④ are skipped (minutes).
- 01 amplitude (A_lead, A_sub, A_lead+A_sub, A_all, σ of the ratio, charge) / 02 amplitude distributions / 03 rise time 10–90 % and 40–60 % /
  04 jitter / 05 position resolution (centroid with A and A² weights, σ and bias) / 06 time resolution (leading, A² weights, optimal, Δt/√2,
  amplitude dependence) / 07 propagation vs position / 08 propagation vs amplitude / 09 2D maps (amplitude, time resolution, position
  resolution). Details of the method in `results\paper_plots_20261013\README.md`.

**pixel** (`analyze_pixel.py` + `tt_pixel.py`): the pads (signal-free 100 µm squares) are detected automatically and snapped to the
500 µm lattice, and the 4 channels are assigned to pixel centers. Time: per event the 1/2/3/4 pixels with the largest amplitudes are
combined with A² weights (after subtracting the per-channel time offsets within the cell), and the best is adopted per position by a
neighborhood pool. Position: σ of the x and y residuals of the amplitude centroid of the 2/3/4 largest pixels (A / A² weights,
uncalibrated). Outputs: `results\<scan name>\pix01–06_*.png` and `pixel\` (cells.csv, numbers.json, README.md, simple\ = radial
profiles, diagonal cuts and Y slices).

**1dY** (`analyze_1dy.py`): the same items with a single scan line. fig3 is the 2D of Y × per-event jitter + MPV (2 definitions),
fig4 is the η curve with the effective pitch, the position resolution (test / in-situ / noise expectation), the residual distribution and the bias,
fig5 is the per-channel σ, σ_lead / σ_Δt/√2 / σ_2strip, the amplitude dependence and the self-estimate of the floor.

**1dX** (`analyze_1dx.py`): fig6 is the Elmore fit (ρc, L), the residuals and the apparent velocity. fig1/2/5 give the slope of a line fit in the body.

**tune / 1d_tune**: the 1/A law in fig3 uses the same steps as for the matome reference value (781/A + 5.4 ps)
(for each channel an unweighted line fit in 1/A of the points with A ≥ 20 mV, then the average over channels).

## Analysis rules (same as matome)

| Item | Rule |
|---|---|
| Baseline and noise | mean and standard deviation of the first 20 % of the samples |
| Amplitude / charge | maximum sample after baseline subtraction / gate integral from peak −3 ns to peak +4 ns (50 Ω) |
| ToA | CFD 50 % crossing with linear interpolation (searched backward from the peak). The origin is the TRIGTIME of each segment |
| Rise time / jitter | 10–90 % and 40–60 % / σ_noise ÷ (dV/dt) (3 slope definitions: 40–60 %, 10–90 %, local CFD50) |
| Quality cut / hit | noise < max(4 mV, 3 × global reference), no saturation, no multiple pulses / amplitude > max(8 mV, 5 × global reference) |
| Representative value / width | MPV (KDE peak, error from bootstrap) / ±1.5σ core Gaussian |
| leading / sub-leading | the strip with the largest / second largest amplitude in each event. The "total amplitude" is A_lead + A_sub (A_total includes the positive bias of channels with no signal, so it is only for reference) |
| Next to the strip / body | 25–75 µm from the strip center with hit rate ≥ 0.95 / the `--body-x` range |
| Position resolution | inverse lookup of the calibration curve of η = A_B/(A_A+A_B). Calibrate on even events, evaluate on odd events (test) |
| Time resolution | σ_lead (against TRIG OUT), σ_Δt/√2 (the floor cancels), σ_2strip (A²-weighted mean) |
| Propagation | along the strip: ToA = t0 + ρc(L d − d²/2), d = −X. Transverse: slope of ΔT(u) = −2/v_eff |

The strip centers are found as the midpoint of the half-maximum crossings of the amplitude profile (IR light does not pass
through the metal, so the profile has a dip at the center). Channels where this fails at the edge of the scan range are filled from the pitch.

## Reproduction check (2026-10-05)

Values from this package for `scan_20260824_202544_2dYX --x-off -632` (the BP scan of matome), compared with the matome values:

| Quantity | Quick-look | matome (BP) |
|---|---|---|
| A_lead (next to the strip) [mV] | 71.67 | 71.67 |
| Change of A_lead+A_sub at the gap center [%] | −12.07 | −12.07 |
| Rise time 10–90 % (next to the strip) [ps] | 622.9 | 622.9 |
| Jitter 40–60 % (next to the strip) [ps] | 14.66 | 14.66 |
| Effective pitch (body) [µm] | 680.6 | 680.6 |
| Position resolution test / noise expectation [µm] | 11.89 / 10.12 | 11.89 / 10.12 |
| Single-strip σ_t (next to the strip) [ps] | 24.01 | 24.01 |
| σ_lead / σ_2strip (gap center) [ps] | 38.54 / 29.94 | 38.54 / 29.94 |
| Elmore ρc (mean of 4 channels) [ps/mm²] | 3.205 | 3.205 |
| v_eff [µm/ps] | 0.916 | 0.919 |
| τ of delay = τ ln(A0/A) [ps] | 319 | 325 |

The per-point reduced quantities differ from the matome cache by 1e-5 or less in the median (from reading float32). The 1–2 % differences
in v_eff and τ come from how the strip centers are found (matome reads the center line of another analysis) and from the binning.
`scan_20260907_213928_2dYX` (FS of matome, no offset) is reproduced in the same way: A_lead 64.8 (matome 64.6) mV,
rise time 764 (763) ps, position resolution 12.89 (12.90) µm, σ_t 30.7 (30.9) ps, ρc 2.14 (2.12) ps/mm², v_eff 0.866 (0.868) µm/ps.
The 1/A law of `scan_20260707_005415_1dYtune` is 792/A + 5.3 ps (matome reference value 781/A + 5.4 ps).

## Processing flow and run time

1. **scan_data.py** — matches the scan folder with the log CSV (automatic detection of channels and file prefix, nominal coordinates with the tracking corrections removed)
2. **reduce_scan.py** — a pre-pass (about 24 points) decides the global noise reference and the polarity, then all points are reduced in parallel
   and cached in `results\<scan name>\reduced.npz` (per-channel, event-level and pair quantities, η histograms,
   per-event jitter distribution, mean waveforms). Per-segment values are not saved
3. **analyze_*.py** — figures and numbers for each kind (reads only reduced.npz)
4. **meanwave_root.py** — writes the mean waveforms and features to `mean_waveforms.root` (skip with `--no-meanwave-root`)
5. **trc_to_root.py** (optional, `--root`) — raw waveforms + features into a single ROOT file

Only the reduction takes time: about 0.03 s per point with 8 workers:

| Scan | Points | Reduction | Figures |
|---|---|---|---|
| 1dY | 101 | about 12 s | about 3 s |
| 1dX (2 ch) | 501 | about 20 s | about 5 s |
| 1dYtune | 610 | about 25 s | about 5 s |
| 2dYX (BP) | 2080 | about 70 s | about 15 s |
| 2dYX (FS) | 14451 | about 10 min | about 30 s |

From the second run reduced.npz is used, so only the figure time is needed. If you change a setting (thresholds etc.), the cache is rebuilt automatically.
The waveform template fit runs automatically after the quick-look analysis of 2dYX / pixel as the `macro\template_timing` pipeline
(see above; skip with `--no-template`). Finely binned mean waveforms, leading-edge reanalysis, zoomed analysis of the strip end etc.
are in the full analysis in `laser_test\analysis\macro\`.

## Files

| File | Contents |
|---|---|
| `run_analysis.py` | entry point. Detects the scan kind and runs the matching analysis |
| `analyze_1dx.py` `analyze_1dy.py` `analyze_1dz.py` `analyze_2dyx.py` `analyze_tune.py` `analyze_1d_tune.py` `analyze_pixel.py` | analysis for each kind (2dYX / pixel also start the template-fit subprocess) |
| `template_pipeline.py` | runs `macro\template_timing\tt_pipeline.py` (strips) / `tt_pixel.py` (pixels) as a subprocess |
| `features.py` | segment feature extraction and reduction of one scan point (implementation of the rules) |
| `reduce_scan.py` | parallel reduction of all points and the cache |
| `position.py` | position reconstruction with η and position resolution |
| `stats.py` | core-Gaussian fit, weighted fits, Elmore fit (numpy only) |
| `mpv.py` | MPV estimate (synchronized copy of `analysis\src\aclgad_tct\mpv.py`) |
| `plot_common.py` | ROOT style, accessor of reduced.npz, geometry (strip centers, gaps), 2D + MPV figures, saving the numbers |
| `scan_data.py` `trc_reader.py` | reading the scan folder and the log, .trc reader |
| `meanwave_root.py` `trc_to_root.py` | ROOT output |

## Mean-waveform ROOT (results\<scan name>\mean_waveforms.root)

Contains the mean waveform of each point and channel from reduced.npz (average over all segments, baseline subtracted, in **mV**).
It does not read the .trc files, so it is written in a few seconds.

- `meanwave` tree (1 entry = 1 scan point): `index, ix/iy/iz/itune` (grid indices), coordinates, `t_ns[nsamp]`,
  `c1..c4[nsamp]` (mean waveform in mV), `baseline_mv[nch]`, per-channel features (`amp_mpv_mV, toa_mu_ps, sigma_core_ps, ...`),
  event-level quantities (`ev_amp_lead_mpv_mV, ev_q_sum_mpv_pC, ev_sigma_lead_ps, ...`)
- `mw/<CH>/mw_<CH>_X-080_Y+040` : mean-waveform TH1D of each point (x: time ns, y: mV. The coordinate tag is the nominal coordinate in µm; T is TUNE %)
- `meta/*`: units, grid centers, channel order, quantity names

```cpp
TFile f("mean_waveforms.root");
((TH1D*)f.Get("mw/C3/mw_C3_X-080_Y+040"))->Draw();
meanwave->Draw("c3:t_ns", "ix==6 && iy==7", "l");
```

## Contents of the ROOT file (--root, <scan_dir>.root)

- `wave` tree (1 entry = 1 segment): `index, seg, x_um..zn_um, tune_pct, ok, trig_time[nch],
  trig_offset[nch], c1..c4[nsamp]` (raw ADC)
- `points` tree (1 entry = 1 point): coordinates, `gain/offset` calibration, per-channel features (same names as `features.QUANT`)
- `meta/*`: calibration formula, channel order, scan-log header

Voltage restoration: `V = gain[ch]*raw - offset[ch]`

> **ToA note**: in sequence mode, using the header `horiz_offset` as the time origin adds a fake jitter (σ≈29 ps).
> Always use the per-segment `trig_offset` branch (established by measurement in 2026-07; see `trc_reader.py`).

## Notes

- **Coordinates**: the log coordinates of the GUI from 2026-09-07 on are sensor coordinates (X = 0 is the outer frame edge on the readout side).
  For earlier scans, adjust with `--x-off` (example: −632 for the 2dYX scan of 2026-08-24). Without it the "body" range is shifted.
- **Waveforms averaged on the scope** (1 segment per file) cannot give a distribution, so MPV, jitter and σ are not available
  (the median is used for the amplitude etc., and a warning is printed).
- **2D scans**: only the X and Y combination (2dYX / 2dXY) is supported.
- An old-format reduced.npz (before 2026-10-05) is rebuilt automatically.
- This folder is the English mirror of the Japanese GUI analysis (`laser_gui_v4\analysis`). When you change something, change both together
  (`macro\template_timing` is shared; its messages are in Japanese).
- **Without uproot** (e.g. when Windows Smart App Control blocks the awkward DLL) everything except the ROOT conversion and the ROOT
  output still works (the template-fit analysis reads the .trc files directly).
