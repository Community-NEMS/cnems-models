# Magic HSM

Magic HSM is a test model for the coupling with C-NGMM, in place of a hydrocarbon supply
model. It sends C-NGMM a gas production capacity for each gas region and summary year, and the
capacity follows the hub price C-NGMM sends back. Like [Magic](magic.md), it runs only when
`models_to_run` names it (`magic_hsm`).

## Capacity

For each C-NGMM domestic region `r` and summary year `y`:

```text
C[r,y] = Q0[r,y] · (P[y] / P_ref) ^ e
```

- `Q0[r,y]` is C-NGMM's supply anchor at build: the tier capacity total times `q0_mult`
  ([Natural Gas](natural_gas.md)).
- `P[y]` is C-NGMM's hub price (`NGHubPricePackage`). Before one arrives, `P = P_ref`, so the
  first C-NGMM solve of a coupled run equals the standalone one.
- `P_ref` and `e` are set in the config.

Magic HSM has no input files of its own: it reads C-NGMM's regions, cost tiers and supply anchors
with C-NGMM's loaders. It has no objective and writes no result files.

## Configuration

Section `[magic_hsm]`, all keys required:

| Key | Meaning |
|---|---|
| `ng_input_path` | C-NGMM's input folder; in a coupled run, `[natural_gas] input_path` |
| `price_elasticity` | `e`, in [0, 1] |
| `reference_price` | `P_ref`, USD/MMBtu, near C-NGMM's standalone hub price |
| `reference_dollar_year` | the dollar year of `reference_price`; in a coupled run, `PRICE_DOLLAR_YEAR` in `src/models/natural_gas/constants.py` |

Coupled runs and their checks are in
[Integrated Runs](../run_modes/integrated.md#magic-hsm-and-natural-gas).
