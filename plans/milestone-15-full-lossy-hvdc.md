# Milestone 15 — Full lossy HVDC (sign-switching converter losses)

Extends Milestone 7 to carry losses when the flow direction is itself a
decision (i.e. `free` mode and zero-straddling `band` steps). Research 
direction: a charge/discharge-stylesplit of `p_in` into non-negative
positive/negative parts (same machinery as the deferred lossy battery
model), which keeps the loss equality affine while
letting the direction vary. Deferred because the MVP (Milestone 7) covers the
dominant proportional loss on fixed-direction links, and the fixed-loss sign
and `dcline` `LOSS0` units are cleaner to settle alongside this split. Also, fixed
converter loss (MATPOWER `LOSS0`), which was already prototyped in the 
`dnlp_vs_pypower` experiment. Finally, add reactive power support, propose apparent
power circle to match energy storage.
