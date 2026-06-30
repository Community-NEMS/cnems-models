# throw-away to fix columns that are floats --> integers.  Possibly useful for other conversions

from pathlib import Path

import pandas as pd


def fix_cap_factor_vre_csv(
    csv_path: Path = Path('./CapFactorVRE.csv'),
) -> None:
    df = pd.read_csv(csv_path)

    for col in ['tech', 'step', 'hour']:
        df[col] = df[col].astype(int)

    df.to_csv(csv_path, index=False)


if __name__ == '__main__':
    fix_cap_factor_vre_csv()
