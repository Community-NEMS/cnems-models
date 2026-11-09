# Notes for Implementation of Myopic Mode(s)

## Temporal Basics

- Currently, the `PowerModel` drives the bus for timeline and `analysis_years`
- Crosswalks/weightings are generated in temporal models and retained as `pandas` df's (for now)
- Years of Interest:
  - Base Year:  First year with data that can be processed
  - y0_learning:  Base year for learning operations
  - y0_discounting:  NYI base year for cost discounting.  TBD if this "moves" with myopic index (talk to Cam)
  - Analysis Years:  Years, not necessarily continuous, that are optimized (see more in Aggregation)

## Year Aggregation

When Year Aggregation is enabled in the `ElecConfig`, load values of the aggregated years are AVERAGED into the LAST
year of each aggregation period.  The weights (number of years averaged) are stored in `ModelSets.year_agg_weight`
and the mapping of years to base year is similarly stored.  See `test_year_agg_weights` for an example.  
(need to verify) are only used in calculating the costs in the objective

Everything other than load is *dropped* by filtering.  Consequences:
- Capacity:  None really.  Existing capacity is mapped over years.
- Builds:  None can occur except in `analysis_years`.

## Myopic principles:

### Definitions:

- **Myopic View** should delineate how many time periods can be seen during a single optimization
- **Myopic Step** is how many time periods are advanced.  This must be LTE view.
- Must carry the weights (or the whole weight df) along
- **Status Year** is the trail year of the last good solve (for data pickup).  In first index, `None`
- **Base Year** is the first year analyzed in a single optimization.  It *may* overlap prior computations
in the case that step < view.  Note: periods are inclusive of last year, so:
  - The first index needs to include the first analysis year (and its weight)
- **Last Year** is the last year analyzed in a single optimization, and in the case of aggregated years,
this will be the "roll up" year

### Requirements:
- Accurate computation of the myopic timeline given some analysis years
- Solve status tracking
- Retain "build/retire" quantities for prior iterations in case reversion needed.  Maybe just keep building
it along the way, then it is usable to the learning mechanism
- Reversion (rearward window expansion) to accommodate failed solves.
- Visualizer

```text
–––––––––––––––––– Visualization

Data Years:  2030 - 2040 [inclusive]
Summary Years:  2030, 2031, 2032, 2036, 2038
Aggregation:  False

Example for view depth=3, step size=1

periods:        30      31      32      33      34      35      36      37      38      39      40         

MI[0]           |–––––––x–––––––x
                BY              LY
                
MI[1]                   |–––––––x–––––––––––––––––––––––––––––––x

MI[2]                           |–––––––––––––––––––––––––––––––x–––––––––––––––x

MI[3]                                                           |–––––––––––––––x

MI[4]                                                                           |
```