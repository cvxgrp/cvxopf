# End-to-end review of `plans/milestone-7-hvdc.md`                                                  
                                                                                                    
I read the full plan and cross-checked its structural claims against `ac_problem.py`,               
`dc_problem.py`, `singlenode_dc_problem.py`, `problem.py`, `results.py`, and `cost.py`. The         
modeling core is sound. There is **one must-fix mechanical error**, a few **inconsistencies**, and  
several **blind spots**.                                                                            
                                                                                                    
## The modeling core holds up                                                                       
                                                                                                    
- **The affine-branch loss model is correct and DCP-valid.** The sign-split (`p_out =               
-(1∓loss_frac)·p_in`), the "no `abs` in equality" rule, Convention B (both balance terms `+`), and  
the `hvdc_loss = p_in + p_out ≥ 0` identity all check out mathematically and against the sign       
convention used by storage (`b`). Verified against the `t_case9_dcline` loss law `Pt = Pf - loss0 - 
loss1·Pf` arithmetic.                                                                               
- **The balance-integration points are real.** AC Section 3 (`p == Cg @ Pg - Pd + ...`, single      
`p==`) and DC (`A @ p_flows + p_gen + ... == Pd`, single line) exist exactly as described, and      
storage/nd already add injection terms there with the same `(1/baseMVA)` idiom the plan specifies   
(AC: `(1.0/baseMVA) * (...)`; DC: `cp.multiply(...)`). Confirmed.                                   
- **Units/detection contracts match** the storage/nd precedent (`"ns"`/`"nnd" in data`; engineering 
units; no rescale in `extract_results`).                                                            
                                                                                                    
## MUST FIX — the "silent-ignore" mechanism is mechanically wrong (Step 3 / R4)                     
                                                                                                    
This is the one that will bite during implementation. `problem.py` dispatches through a **single    
unified call site** per entry point:                                                                
                                                                                                    
```python                                                                                           
# build_opf:                                                                                        
return builders[formulation](case, options, storage, delta, nondispatchable)                        
# build_opf_multistep:                                                                              
return builders[formulation](case, df_P, df_Q, T, options, coupling_constraints, storage, delta,    
nondispatchable, df_nd)                                                                             
```                                                                                                 
                                                                                                    
All three builders — including `_build_singlenode_dc_single/multistep` — share the **identical      
positional signature**. The plan (Step 3, R4) says: add `hvdc`/`df_hvdc`, forward to ac/lossy_dc,   
and *"do not forward to singlenode; singlenode builder signatures unchanged."*                      
                                                                                                    
That is not achievable at a single positional call site. If `hvdc` is appended positionally, the    
singlenode builder receives it too — either an arity `TypeError`, or it silently lands in           
`nondispatchable`/`df_nd`. You cannot forward-to-some-but-not-others without either:                
- **(a)** branching the dispatch on `formulation` (a real change to the unified call site the plan  
doesn't mention), or                                                                                
- **(b)** giving the singlenode builders the `hvdc`/`df_hvdc` params too (ignored) — which          
**contradicts** "singlenode signatures unchanged."                                                  
                                                                                                    
The plan must pick one and say so. Option (b) is closest to the existing pattern (storage/nd/df_nd  
are already threaded through every builder including singlenode) and is the lower-risk choice — but 
then "silent ignore" means "accepted and dropped," and R4's "signatures unchanged" line is false    
and should be rewritten. Note the plan itself flags detection via `"n_hvdc" not in build.data` for  
singlenode (Gate 3), which is consistent with (b); it's specifically the "signatures unchanged /    
omit from call path" wording that's wrong.                                                          
                                                                                                    
## Inconsistencies / smaller correctness gaps                                                       
                                                                                                    
1. **`p_hvdc_in`/`p_hvdc_out` variable container shape is unspecified and inconsistent with the     
extraction pattern.** Step 2's helper returns `p_in_list`, `p_out_list` (lists of *per-link scalar* 
Variables), but storage/nd use a *single `(ns,)`/`(nnd,)` Variable per step* and `results.py`       
extracts them as `var["p_nd"][t].value` (one array per step). Step 6 says results are `(n_hvdc,)`   
single / `(T, n_hvdc)` multi. The plan never states whether `build.variables["p_hvdc_in"]` is (a)   
one `(n_hvdc,)` Variable, (b) a list of `n_hvdc` scalar Variables, or (c) multistep list-of-lists.  
This ambiguity will directly complicate Step 6. Recommend: mandate a single `(n_hvdc,)` Variable    
per step (matching storage/nd), and have the Step 2 helper stack rather than return Python lists —  
or explicitly document the list-of-scalars choice and how `extract_results` walks it.               
                                                                                                    
2. **`dclinecost` coefficient ordering is a latent trap.** `poly_cost_expr` reads gencost           
**highest-power-first**; the plan stores `HVDCLink.cost_coeffs` as `(c0, c1, c2)` **lowest-first**. 
The plan is internally self-consistent (it extracts `c1=7.3` correctly from the `[2,0,0,2,7.3,0]`   
row), but "same polynomial layout as `gencost`" invites a reversed-index bug. Add an explicit "note 
the order flips" caution in Step 1.                                                                 
                                                                                                    
3. **`c0` in an inequality-free objective is a no-op that can mislead a solve-comparison.** The     
plan says the MVP always adds `c0` "for objective consistency." Fine — but Gate 6b compares         
`objective` against Pypower. Pypower's dcline cost model and whether it adds `c0` per active line   
must match, or the objective comparison is off by `sum(c0)`. Since `t_case9_dcline`'s `dclinecost`  
has all `c0=0`, this is inert for the fixture, but the plan's Gate 6b "loose tolerance on           
objective" should note the cost-model alignment risk explicitly (it currently attributes all        
discrepancy to `loss0`).                                                                            
                                                                                                    
4. **Gate 6b vs. `compare_to_reference`.** `results.py` already has `compare_to_reference`, and the 
DC result dict has **no `Qg`/`Vm`/`Va_deg`**, while the Pypower fixture schema (Step 0b) includes   
`Qg`/`Vm`/`Va_deg`. For a **lossy_dc** cvxopf solve vs an **AC** Pypower oracle, only               
`objective`/`Pg`/`p_net` overlap — and even `Pg` alignment across a DC-vs-AC solve is loose at      
best. The plan's Gate 6b options (a)/(b)/(c) don't say **which formulation** cvxopf solves for the  
comparison. Comparing cvxopf-AC to Pypower-AC is the only apples-to-apples objective/Pg comparison; 
comparing lossy_dc to Pypower-AC conflates DC-vs-AC modeling error with the dropped-`loss0` error.  
This should be pinned down: **Gate 6b should specify cvxopf formulation = `ac`**, or explicitly     
accept that (c) internal-consistency is the only meaningful check for a DC solve.                   
                                                                                                    
5. **`hvdc_from_dcline` `mode="band"` + `p_scheduled_mw=Pf` can produce a degenerate/empty band.**  
Step 1 maps `[Pmin,Pmax] → box` and sets `mode="band"` with `p_scheduled_mw=Pf` and `bandwidth_mw`  
defaulting to `0.0`. With `bandwidth=0`, the intersected interval `[Pf-0, Pf+0] ∩ [Pmin,Pmax]`      
collapses to the single point `{Pf}` (if `Pf∈[Pmin,Pmax]`) — effectively pinning `p_in=Pf`, i.e.    
silently "scheduled," not a usable band. For row 1 (`Pf=2, [Pmin,Pmax]=[2,10]`) the point is at the 
boundary; for row 3 (`Pf=10,[0,10]`) at the upper bound. Is that intended (import = fixed at `Pf`)? 
If the intent was "optimize within `[Pmin,Pmax]`," `bandwidth` must be set wide (e.g. `Pmax-Pmin`)  
or `mode` should be a pure box without a band. The plan is ambiguous here and it directly affects   
what Gate 6b actually solves.                                                                       
                                                                                                    
## Blind spots (not blocking, but worth a line each)                                                
                                                                                                    
6. **Builder signature bloat.** `_make_dc_step_constraints` and `_make_step_constraints` already    
take ~12–22 params; HVDC adds ~5–6 more each. No refactor is requested (correctly, per repo norms), 
but the plan could acknowledge the growing positional-arg surface and consider a small params       
object *if* a reviewer pushes back. Flag only.                                                      
                                                                                                    
7. **`df_hvdc` semantics only partially specified.** Step 3 says columns are integer indices        
`0..n_hvdc-1` (unlike `df_nd` which uses bus IDs). Good that it differs deliberately — but the plan 
doesn't say what `df_hvdc` *contains* (per-step `p_scheduled`? per-step bounds?). For               
`scheduled`/`band` the natural content is per-step `p_scheduled_mw`; confirm and state it. The      
multistep zero-crossing gate is per-step precisely because `p_sched_t` varies — so `df_hvdc` must   
carry `p_sched` per step. Make that explicit.                                                       
                                                                                                    
8. **AC "Section 3b" naming.** Step 5 introduces `Section 3b: HVDC bounds`. The existing code puts  
*all* operating constraints in Sections 4/4b/5 (after balance). Numbering HVDC bounds "3b" (between 
balance §3 and storage §4) is a cosmetic deviation from the established convention. Prefer `Section 
4c` for consistency, or note the intentional break.                                                 
                                                                                                    
9. **Gate 7's `702 + new`.** The baseline is asserted as 702 at the top and Gate 7 says "702 +      
new." CLAUDE.md says the expected suite is 512 — but that's a stale CLAUDE.md number (the plan's    
702 is presumably current). Not the plan's error, but if you regenerate baselines, reconcile the    
two documents.                                                                                      
                                                                                                    
## The one thing I could not verify without running code                                            
                                                                                                    
**R9 and Step 0b hinge on two empirical facts I cannot confirm by reading:** (i)                    
`pypower.t.t_case9_dcline` is importable at that dotted path in `pypower==5.1.19`, and (ii)         
`toggle_dcline` actually raises under `numpy==2.2.6` (the pervasive float-index bug). The plan      
states both were "verified empirically this session," but that was in an earlier context window. If 
either is wrong, Step 0b's scope (and the monkeypatch/R9) changes materially. Everything else in    
the plan is independent of this and stands on its own.                                              
                                                                                                    
**May I run the isolated fixture-script environment** to confirm those two facts? It would be a     
read-only probe, something like:                                                                    
```                                                                                                 
uv run --with 'pypower==5.1.19' --with 'numpy==2.2.6' python -c "from pypower.t.t_case9_dcline      
import t_case9_dcline; ..."                                                                         
```                                                                                                 
(plus a `toggle_dcline(ppc,'on')` call to observe the `IndexError`). This is the only outstanding   
item; I'll hold until you approve, per your instruction to ask before running anything besides      
tests.                                                                                              
                                                                                                    
## Bottom line                                                                                      
                                                                                                    
The plan is in strong shape and the hard modeling questions are correctly settled. **One must-fix** 
(the silent-ignore dispatch mechanism in Step 3/R4 is mechanically impossible as written against    
the current single-call-site dispatch). **Five inconsistencies** (2–5 above are the substantive     
ones: variable container shape, cost-coeff ordering, Gate 6b formulation ambiguity, and the         
`bandwidth=0` degenerate band). The blind spots are minor. None of these touch the core loss/sign   
model, which is solid.                                                                              
                                                                                                    
Want me to (a) hold for your approval to run the pypower probe, (b) draft the specific plan edits   
for the must-fix + inconsistencies, or both? I have not modified anything. 