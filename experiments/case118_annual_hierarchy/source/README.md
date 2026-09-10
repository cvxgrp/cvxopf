# PGLib-OPF source snapshot

This directory contains the unmodified `pglib_opf_case118_ieee.m` case and
PGLib-OPF data license used by the annual hierarchy experiment. Both files
were acquired from the official PGLib-OPF repository at commit
`dc6be4b2f85ca0e776952ec22cbd4c22396ea5a3`.

The data are redistributed under CC BY 4.0. The adjacent `manifest.json`
records source-file and normalized numeric-array SHA-256 hashes. The strict
loader verifies all hashes before returning a MATPOWER case.

Acquisition URLs:

- `https://raw.githubusercontent.com/power-grid-lib/pglib-opf/dc6be4b2f85ca0e776952ec22cbd4c22396ea5a3/pglib_opf_case118_ieee.m`
- `https://raw.githubusercontent.com/power-grid-lib/pglib-opf/dc6be4b2f85ca0e776952ec22cbd4c22396ea5a3/LICENSE`

The source case retains branch angle-difference fields. cvxopf does not model
those limits; the experiment reports that omission rather than silently
claiming full PGLib constraint equivalence.
