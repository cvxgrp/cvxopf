# README figure provenance

`network_fidelity_soc.png` is an unchanged copy of the current PES draft's
24-hour deficit-to-surplus SoC figure. It compares **joint** full AC and copper
plate optimizations, not an AC rollout or a fixed-power transfer.

- Source: `experiments/case118_tracy_2021/results/pes_soc_names_001/soc_case_4.png`.
- Image SHA-256: `265c0b2630fd3f6bb694c39e51aca6b4b38dc420ad5261e5dec0fbf23157cdaf`.
- Numerical snapshot: `soc_data.json` in that bundle, SHA-256
  `9ba23d8dcc1137d9f7efa90962379ffc05545f146596b7c4975a982317b27c35`.
- This is a selected documentation asset; raw archives and source inputs remain
  in their original ignored locations. No optimization or new extraction was
  performed to copy the image.

The [paper reproduction guide](../../experiments/pes2027/README.md#regenerate-metrics-and-figures-without-solving)
and [figure guide](../../experiments/pes2027/PES_FIGURES.md) describe the
non-solving `experiments.pes2027.plot_pes_soc` renderer and retained inputs.
The [appendix](../../experiments/pes2027/APPENDIX.md) gives physical models,
normalization, source qualifications and interpretation. To reproduce a
bundle, use a fresh output directory and compare its numerical snapshot with
the identity above; do not overwrite historical evidence.
