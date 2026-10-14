# Bundled SIMsalabim / SimSS

Upstream version: **5.36**, pinned to tag `v5.36`.
Local build: **5.36-pds1**, modified 2026-10-14 for Perovskite Device Studio.

- Project and documentation: https://github.com/kostergroup/SIMsalabim
- Exact source: https://github.com/kostergroup/SIMsalabim/tree/v5.36
- Source archive: https://github.com/kostergroup/SIMsalabim/archive/refs/tags/v5.36.zip
- Original Windows executable: https://github.com/kostergroup/SIMsalabim/releases/download/v5.36/simss.exe
- Original SHA256: `beb3ad8e231346e3f6d4cda4147ac5b9102912d389c21a2bae069b600ab2595c`
- **Bundled modified executable SHA256**: `ba54d82ea8848f7e5d0e65251e61cd18699fb4e499ea587dde8e703b9d74bad6`

The executable is compiled from the exact corresponding source in `source/`.
Two targeted modifications to upstream source are marked with PDS comments:

1. `Units/DDRoutines.pas`, `Make_Grid`: reserve `NLayers-1` extra intervals
   before distributing the grid. This prevents the final layer from receiving
   too few or a negative number of mesh intervals in multilayer grading.
2. `Units/TypesAndConstants.pp`: increase `Max_NP` from 1000 to 2000 to match
   the application input limit. No physical equations are changed.

`simulation_setup.txt` and `layer_parameters.txt` are unmodified upstream templates from `SimSS/simulation_setup.txt` and
`SimSS/L2_parameters.txt`. Copies of the upstream license files and README are
included here. The Python adapter writes modified copies of templates into an
isolated temporary directory for each run; it never edits these originals.

Rebuild with Free Pascal 3.2.2 (or compatible 3.2.x):

```text
cd vendor/simsalabim/source/SimSS
fpc -B -O2 -Xs -o../../simss.exe simss.pas
```

The supplied binary was built with the official Free Pascal 3.2.2 Win32-to-Win64
cross compiler, extracted into the ignored local build directory without
installing it. `prepare_simss_source.py` reproduces the source modifications
from an unmodified upstream 5.36 checkout. The Python adapter reproduces this
patched grid allocation and sets the virtual-interface transfer speed as
`nu = mu * Vt / dx`, so artificial layer cuts preserve the bulk mobility.

Authors: Sander Heester, Tejas Sherkar, Vincent Le Corre, Marten Koopmans,
Friso Wobben, and L. Jan Anton Koster (University of Groningen).

Citation: M. Koopmans, V. M. Le Corre, L. J. A. Koster, “SIMsalabim: An
open-source drift-diffusion simulator for semiconductor devices”,
Journal of Open Source Software 7, 3727 (2022), https://doi.org/10.21105/joss.03727.

License: see `COPYING.LESSER.txt`, `COPYING.txt`, and `README.upstream.md`.
The bundled binary targets Windows x64. On other platforms supply an upstream
`simss` binary compiled from the supplied modified source in this directory
and set its executable permission.
