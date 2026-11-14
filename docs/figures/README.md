# About the README figure

`network_fidelity_soc.png` shows battery state of charge during a 24-hour
deficit-to-surplus scenario. It compares joint full AC and copper-plate
optimizations, not controller rollouts or fixed-power transfers. The image is
copied unchanged from the PES paper draft.

- Source: `experiments/case118_tracy_2021/results/pes_soc_names_001/soc_case_4.png`.
- Image SHA-256: `265c0b2630fd3f6bb694c39e51aca6b4b38dc420ad5261e5dec0fbf23157cdaf`.
- Numerical snapshot: `soc_data.json` in that bundle, SHA-256
  `9ba23d8dcc1137d9f7efa90962379ffc05545f146596b7c4975a982317b27c35`.
- The original output files and input data are stored locally and are not
  included in the repository. The hashes identify the image and data used here.

To recreate the figure from saved results, follow the
[paper reproduction guide](../../experiments/pes2027/README.md#regenerate-metrics-and-figures-without-solving)
and [figure guide](../../experiments/pes2027/PES_FIGURES.md). They describe the
required files and the `experiments.pes2027.plot_pes_soc` plotting command,
which does not solve another optimization problem.
The [appendix](../../experiments/pes2027/APPENDIX.md) gives physical models,
normalization, data sources and interpretation. Write regenerated files to a
new directory, keep the original results, and compare the saved numerical data
with the hash above.
