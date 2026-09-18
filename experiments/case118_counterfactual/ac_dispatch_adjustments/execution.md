# AC dispatch adjustments — 2026-09-17

Execution authorized by owner: “committed. please proceed with the test”. Implementation commit: 35d951b.

One three-hour window, May 3 12:00–15:00 UTC in the synthetic study calendar
(indices 2940–2942). The experiment compares small generator adjustments,
lower-cost small adjustments, unrestricted generator redispatch, and then
battery rescheduling. It was selected from the highest joint six-hour candidate
before new solves. See context.png and context-summary.json for surrounding
48 hours. Results apply to this selected window, not a representative annual sample.

All arms retain historical toy economics and common DC initial/terminal SoC. Historical AC begins about 63 MWh below DC and recharges during this window; that state difference is context, not part of the matched experiment. The historical battery change therefore does not predict a positive matched B benefit.

R2 allowance is 1 MWh above observed R1 departure, about 0.025% of historical generator absolute departure over this window. It is a small declared optimization allowance, not a claim about the minimum achievable repair or a physical tolerance. Locks use 0.0001 MW (matching 1e-6 pu on 100 MVA); departure reconstruction/budget use 0.001 MWh, 1000 times smaller than the R2 allowance. Cost reconstruction uses absolute 0.0001 plus relative 1e-9. Existing frozen physical residual tolerances remain unchanged. ac_options={} preserves the existing solver defaults, recorded with software versions.

Resource limits: 90 minutes study wall time; 90 minutes per worker including build; 180 aggregate worker minutes; 40 attempts including helpers and replays. Existing supervisor memory gates and escalation timing are reused. Two main lanes and one shared helper are configured; one main lane is unused for this sequential one-window comparison. Stop at completion or resource/recovery exhaustion, retain all attempts and accepted incumbents, and assess before further windows.

Launch disposition: experiments/case118_counterfactual/results/ac_dispatch_adjustments_20260917_failed_launch stopped after 0.019 s because the sandbox denied ps; the sole worker was terminated/reaped before any build or solve phase. The permission-corrected execution uses experiments/case118_counterfactual/results/ac_dispatch_adjustments_20260917 with exactly the same protocol. Reused AC supervisor limits: 16 GiB per worker, 24 GiB aggregate, 8 GiB helper reservation, 22 GiB pressure threshold (the separate analysis-pass worker cap is not this AC supervisor policy).
