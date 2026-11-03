"""Build population-weighted crosswalks between electricity regions and Census divisions.

Bridges the 25 EMM electricity regions and the 9 US Census divisions ("natural gas regions").

Method: allocate each state's 2020 Census resident population across the electricity
regions that cover it, giving an elec_region x state population matrix. Census divisions
are exact unions of states, so both directional weightings fall out of that matrix.
"""

from collections import defaultdict
from pathlib import Path

# 2020 Census resident population, thousands.
POP = {
    'AL': 5024,
    'AZ': 7152,
    'AR': 3011,
    'CA': 39538,
    'CO': 5773,
    'CT': 3606,
    'DE': 990,
    'DC': 689,
    'FL': 21538,
    'GA': 10712,
    'ID': 1839,
    'IL': 12813,
    'IN': 6786,
    'IA': 3190,
    'KS': 2938,
    'KY': 4506,
    'LA': 4658,
    'ME': 1362,
    'MD': 6177,
    'MA': 7030,
    'MI': 10077,
    'MN': 5706,
    'MS': 2961,
    'MO': 6155,
    'MT': 1084,
    'NE': 1961,
    'NV': 3105,
    'NH': 1378,
    'NJ': 9289,
    'NM': 2117,
    'NY': 20201,
    'NC': 10439,
    'ND': 779,
    'OH': 11799,
    'OK': 3959,
    'OR': 4237,
    'PA': 13003,
    'RI': 1097,
    'SC': 5118,
    'SD': 887,
    'TN': 6911,
    'TX': 29146,
    'UT': 3272,
    'VT': 643,
    'VA': 8632,
    'WA': 7706,
    'WV': 1794,
    'WI': 5894,
    'WY': 577,
}
# AK and HI are Pacific-division states with no representation in the 25-region
# electricity set (separate interconnects); excluded so pacific weights describe the
# modeled footprint rather than an unmodeled remainder.

CENSUS_DIVISION = {
    'new_england': ['CT', 'ME', 'MA', 'NH', 'RI', 'VT'],
    'middle_atlantic': ['NJ', 'NY', 'PA'],
    'east_north_central': ['IL', 'IN', 'MI', 'OH', 'WI'],
    'west_north_central': ['IA', 'KS', 'MN', 'MO', 'NE', 'ND', 'SD'],
    'south_atlantic': ['DE', 'DC', 'FL', 'GA', 'MD', 'NC', 'SC', 'VA', 'WV'],
    'east_south_central': ['AL', 'KY', 'MS', 'TN'],
    'west_south_central': ['AR', 'LA', 'OK', 'TX'],
    'mountain': ['AZ', 'CO', 'ID', 'MT', 'NV', 'NM', 'UT', 'WY'],
    'pacific': ['CA', 'OR', 'WA'],
}
STATE_TO_DIV = {s: d for d, ss in CENSUS_DIVISION.items() for s in ss}

# Share of each state's population falling in each electricity region. Shares per state
# sum to 1.0 (asserted below). See the companion README for how each region was scoped.
COVERAGE = {
    # -- ERCOT / Florida -------------------------------------------------------
    'TRE': {'TX': 0.85},
    'FRCC': {'FL': 0.95},
    # -- MISO ------------------------------------------------------------------
    'MISW': {'WI': 1.0, 'MN': 0.90, 'IA': 0.80, 'ND': 0.50, 'SD': 0.40, 'MT': 0.15},
    'MISC': {'IL': 0.35, 'IN': 0.70, 'MO': 0.45},
    'MISE': {'MI': 0.90},
    'MISS': {'LA': 1.0, 'AR': 0.75, 'MS': 0.65, 'TX': 0.05},
    # -- NPCC ------------------------------------------------------------------
    'ISNE': {'CT': 1.0, 'ME': 1.0, 'MA': 1.0, 'NH': 1.0, 'RI': 1.0, 'VT': 1.0},
    'NYCW': {'NY': 0.63},
    'NYUP': {'NY': 0.37},
    # -- PJM -------------------------------------------------------------------
    'PJME': {'NJ': 1.0, 'DE': 1.0, 'MD': 1.0, 'DC': 1.0, 'PA': 0.55},
    'PJMW': {'OH': 1.0, 'WV': 1.0, 'PA': 0.45, 'KY': 0.40, 'IN': 0.30, 'MI': 0.10},
    'PJMC': {'IL': 0.65},
    'PJMD': {'VA': 0.90, 'NC': 0.10},
    # -- SERC ------------------------------------------------------------------
    'SRCA': {'SC': 1.0, 'NC': 0.90},
    'SRSE': {'GA': 1.0, 'AL': 0.60, 'MS': 0.15, 'FL': 0.05},
    'SRCE': {'TN': 1.0, 'AL': 0.40, 'KY': 0.60, 'MS': 0.20, 'MO': 0.10, 'VA': 0.10},
    # -- SPP -------------------------------------------------------------------
    'SPPS': {'OK': 1.0, 'AR': 0.25, 'TX': 0.07, 'NM': 0.10},
    'SPPC': {'KS': 1.0, 'MO': 0.45},
    'SPPN': {'NE': 1.0, 'SD': 0.60, 'ND': 0.50, 'IA': 0.20, 'MN': 0.10},
    # -- WECC ------------------------------------------------------------------
    'SRSG': {'AZ': 1.0, 'NM': 0.90, 'TX': 0.03},
    'CANO': {'CA': 0.35},
    'CASO': {'CA': 0.65},
    'NWPP': {'WA': 1.0, 'OR': 1.0, 'ID': 0.55, 'MT': 0.85},
    'RMRG': {'CO': 1.0, 'WY': 0.55},
    'BASN': {'NV': 1.0, 'UT': 1.0, 'ID': 0.45, 'WY': 0.45},
}

# --- validate that every state is fully and only once allocated ------------------
by_state = defaultdict(float)
for states in COVERAGE.values():
    for st, frac in states.items():
        by_state[st] += frac
bad = {s: round(v, 6) for s, v in by_state.items() if abs(v - 1.0) > 1e-9}
assert not bad, f'state shares do not sum to 1.0: {bad}'
missing = set(POP) - set(by_state)
assert not missing, f'states with no electricity region: {missing}'

# --- elec_region x census_division population ------------------------------------
cell = defaultdict(float)  # (elec, div) -> population
elec_tot = defaultdict(float)  # elec -> population
div_tot = defaultdict(float)  # div  -> population
for reg, states in COVERAGE.items():
    for st, frac in states.items():
        p = POP[st] * frac
        cell[(reg, STATE_TO_DIV[st])] += p
        elec_tot[reg] += p
        div_tot[STATE_TO_DIV[st]] += p


def round_to_one(pairs, nd=4):
    """Round weights to `nd` places so they still sum exactly to 1.0.

    Largest-remainder: floor every value, then hand the leftover units to the
    entries with the biggest truncated remainder.
    """
    scale = 10**nd
    scaled = [v * scale for _, v in pairs]
    floors = [int(x) for x in scaled]
    short = scale - sum(floors)
    order = sorted(range(len(pairs)), key=lambda i: scaled[i] - floors[i], reverse=True)
    for i in order[:short]:
        floors[i] += 1
    return [(pairs[i][0], floors[i] / scale) for i in range(len(pairs))]


# this script and its intermediate products live in workproduct/; the two crosswalks the
# model reads are written one level up, next to the README
WORK = Path(__file__).resolve().parent
OUT = WORK.parent
SRC = OUT.parent.parent / 'natural_gas' / 'elec_to_ng_region_map.csv'
ROWS = []
for line in SRC.read_text().splitlines()[1:]:
    rid, name, ng = line.split(',')
    ROWS.append((int(rid), name, ng, name.split(' ')[0]))

# --- 1. primary: original map + weight within the assigned ng_region --------------
assigned = defaultdict(float)
for _rid, _name, ng, code in ROWS:
    assigned[ng] += elec_tot[code]
with (WORK / 'elec_to_ng_region_map_weighted.csv').open('w') as f:
    f.write('elec_region,elec_region_name,ng_region,weight\n')
    # Weights are computed per ng_region group, but emitted in the original
    # elec_region order so the file stays a drop-in superset of the source map.
    wt = {}
    for ng in dict.fromkeys(r[2] for r in ROWS):
        grp = [(r, elec_tot[r[3]] / assigned[ng]) for r in ROWS if r[2] == ng]
        wt.update({r[0]: w for r, w in round_to_one(grp)})
    for rid, name, ng, _code in ROWS:
        f.write(f'{rid},{name},{ng},{wt[rid]:.4f}\n')

# --- 2. secondary: fractional elec -> ng (rows sum to 1 per elec region) ----------
with (OUT / 'elec_to_ng_crosswalk.csv').open('w') as f:
    f.write('elec_region,elec_region_name,ng_region,weight\n')
    for rid, name, _ng, code in ROWS:
        grp = [
            (d, cell[(code, d)] / elec_tot[code])
            for d in CENSUS_DIVISION
            if cell[(code, d)] / elec_tot[code] > 5e-4
        ]
        for div, w in round_to_one(grp):
            f.write(f'{rid},{name},{div},{w:.4f}\n')

# --- 3. secondary: fractional ng -> elec (rows sum to 1 per ng region) ------------
code_of = {code: (rid, name) for rid, name, ng, code in ROWS}
with (OUT / 'ng_to_elec_crosswalk.csv').open('w') as f:
    f.write('ng_region,elec_region,elec_region_name,weight\n')
    for div in CENSUS_DIVISION:
        grp = [
            (r, cell[(r[3], div)] / div_tot[div])
            for r in ROWS
            if cell[(r[3], div)] / div_tot[div] > 5e-4
        ]
        for (rid, name, _, _), w in round_to_one(grp):
            f.write(f'{div},{rid},{name},{w:.4f}\n')

# --- provenance: the underlying state matrix -------------------------------------
with (WORK / 'elec_region_state_coverage.csv').open('w') as f:
    f.write('elec_region,elec_region_name,state,census_division,state_pop_share,pop_thousands\n')
    for rid, name, _ng, code in ROWS:
        for st, frac in sorted(COVERAGE[code].items()):
            f.write(f'{rid},{name},{st},{STATE_TO_DIV[st]},{frac:g},{POP[st] * frac:.1f}\n')

# --- report ----------------------------------------------------------------------
print('=== disagreement between the hard 1:1 map and population geography ===')
print(f'{"elec":10} {"assigned ng_region":20} {"share actually there":>20}')
worst = []
for _rid, _name, ng, code in ROWS:
    share = cell[(code, ng)] / elec_tot[code]
    worst.append((share, code, ng))
for share, code, ng in sorted(worst):
    flag = '  <-- weak' if share < 0.75 else ''
    print(f'{code:10} {ng:20} {share:>19.1%}{flag}')
print()
tot = sum(elec_tot.values())
agree = sum(cell[(code, ng)] for _, _, ng, code in ROWS)
print(f'population-weighted agreement of the 1:1 map: {agree / tot:.1%}')
