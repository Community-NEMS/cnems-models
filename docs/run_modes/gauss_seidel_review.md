# Gauss-Seidel branch: notes for reviewers

The Gauss-Seidel branch is ready for a look: https://github.com/Community-NEMS/cnems-models/tree/gauss-seidel

It is a comparison second iteration scheme for the integrated electricity and natural gas run, beside the Jacobi
iterator and inside the same IterativeSequencer framework. Right now it's experimental and would love your feedback on it, both the math and the structure. I tried to structure it so it ran within the Jacobi infrastructure, but does Gauss-Seidel with some additional efficiencies:

- Both models are built once and held. Each iteration applies the inbound packages to the built
  models (update_model) and re-solves them (solve_iteration), instead of rebuilding them.
- Each model keeps its solver between solves, so a re-solve sends only what changed.
- The models solve in turn, electricity then gas, so gas sees this iteration's burn rather than
  last iteration's.

The economics are main's unchanged: the same multiplicative price link, gas turbine only, the same
4 USD/MMBtu reference. The packages, the population-weighted crosswalk, route_updates, the
ConvergenceTracker and the monitor are Jacobi's. The per-iteration bookkeeping both loops share is
in src/integrator/control_loop.py. We might change some of these in the future, but I wanted it to match Jacobi right now so we can compare and verify both methods.

## How to try it

    pixi run gs            # Gauss-Seidel on run_configs/gs_compare.toml, about 1.5 minutes
    pixi run gs-compare    # Jacobi then Gauss-Seidel on that config, side by side, 5 to 7 minutes
    pixi run jacobi        # as before

gs-compare writes one folder per comparison, output/gs_compare_comparison (then _1, _2, ...),
holding the two runs' folders, jacobi/ and gauss_seidel/, beside comparison.md,
comparison.json and two figures. They hold the iterations, wall time, time per step, final objectives, and the burn and
mean gas price for each iteration. gs_compare.toml is every electricity and gas region for 2025 and 2030. That is a
case where the two models' regions cover each other fully, so both schemes solve the same problem.

Here's some comparison results:

| | Jacobi | Gauss-Seidel |
|---|---|---|
| Epsilon 0.001, run 1: iterations, wall time | 6, 312 s | 4, 67 s |
| Epsilon 0.001, run 2: iterations, wall time | 6, 306 s | 4, 75 s |
| Epsilon 1e-5: iterations, wall time | 8, 432 s | 5, 80 s |
| Final objectives, burn and mean gas prices | | identical to Jacobi's in every run (largest relative objective difference 5e-8) |

Good news is results match! There are some computational efficiencies worth considering. Most of the difference is rebuilding. Summed over the run, Jacobi spent 212 to 301 s loading data
and building models; Gauss-Seidel spent about 35 s, once. Updating a held model takes about 0.01 s,
and the kept solvers make each re-solve cheaper. Gauss-Seidel also needs fewer iterations, because
gas sees the same iteration's burn. Its wall time includes writing result files at the end, which
Jacobi does not do.

Two figures are generated, from the epsilon 1e-5 run, and shown on the
[comparison page](gauss_seidel_comparison.md) with the full results. gs-compare draws the same
two for any run.

- objectives.png: each model's objective by iteration, and below it the relative change from the
  previous iteration against the stopping tolerance.
- exchange.png: the power-sector burn and the mean gas price the models exchanged in each
  iteration, for 2025 and 2030.

Gauss-Seidel's gas model sees electricity's burn from the first iteration. Its first gas price is
within 0.3% of the final one, while Jacobi starts from the gas model's own baseline and moves
in pairs of iterations, each model reacting to the other's previous iteration.

## The commits, in order

1. Add the gas fuel cost adjustment to the dispatch objective: ng_fuel_adj, a mutable parameter
   added to supply_price in dispatch_cost, zero by default.
2. Let a model declare which of its inputs another model owns: declare_external and is_external on
   IntegratedModel, and exact setters on the gas model.
3. Resolve the coupled footprint before the first solve: build_preflight checks that the regions
   cover each other.
4. Apply update packages to a built model without rebuilding it: update_model becomes "apply these
   packages to the built model in place", and solve_iteration is split from full_run. Tests check
   that an update leaves exactly the problem a rebuild with the same packages gives.
5. Keep one solver per built model across solves.
6. Add a Gauss-Seidel iterator beside the Jacobi one, with timings on IterationResult, the
   comparison script and docs.
7. Remove the one-to-one gas coupling helpers the iterators no longer use.
8. Share the per-iteration bookkeeping between the two iterators, and add the pixi tasks.
9. Draw how the iterators approach the answer in the comparison, one folder per comparison.
10. Add the comparison write-up and these notes to the docs.

## Shared code it touches

- src/common/integrated_model_sequencer.py: update_model takes update_packages; solve_iteration is
  split from full_run; IterationResult gains a timings field. Jacobi's path through full_run is
  unchanged.
- src/common/integrated_model.py: declare_external and is_external.
- src/integrator/jacobi/jacobi_iterator.py: its bookkeeping now calls control_loop.py, with the
  same behaviour and log messages.
- Each model's update_reader.py gains a reader for the built model, beside the pre-build one.
