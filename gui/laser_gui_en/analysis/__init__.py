# -*- coding: utf-8 -*-
"""Quick-look analysis package for data taken with laser_gui_v2_en.

Takes a scan folder (data\\<year>\\<YYYYMMDD>\\scan_<datetime>_<tag>)
and the GUI scan-log CSV (laser_gui_v2_en\\logs\\scan_<datetime>.csv)
as input, and produces the figures and numbers for each scan kind
(1dX / 1dY / 1dZ / 2dYX / tune / 1d_tune; same definitions as the
"matome" analysis note) as well as the .trc → single ROOT file conversion.

See README.md and run_analysis.py --help for usage.
Outputs (ROOT, figures, CSV) are placed outside the repository
(data\\... and results\\...).
"""
