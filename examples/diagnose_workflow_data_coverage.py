"""Diagnose which step limits the usable date range in the scratch workflow."""

from pathlib import Path

import pandas as pd


RAW_CSV = Path("outputs/raw_df.csv")
MEMBERSHIP_FILE = Path("datasets/cn_data/instruments/csi300.txt")


def main() -> None:
    raw = pd.read_csv(RAW_CSV, parse_dates=["datetime"])
    raw = raw.set_index(["datetime", "instrument"]).sort_index()
    close = raw["close"].unstack("instrument")
    high = raw["high"].unstack("instrument")
    low = raw["low"].unstack("instrument")
    volume = raw["volume"].unstack("instrument")

    daily_return = close.pct_change(fill_method=None)
    factor_valid = (
        (close / close.shift(5) - 1).notna()
        & (close / close.shift(20) - 1).notna()
        & daily_return.rolling(20).std().notna()
        & (volume / volume.rolling(5).mean()).notna()
        & (close / close.rolling(20).mean() - 1).notna()
        & ((high - low) / close.shift(1)).notna()
        & ((close - low) / (high - low + 1e-9)).notna()
        & (close.shift(-1) / close - 1).notna()
    )

    membership = pd.read_csv(
        MEMBERSHIP_FILE,
        sep="\t",
        names=["instrument", "start", "end"],
        parse_dates=["start", "end"],
    )
    active = pd.DataFrame(False, index=close.index, columns=close.columns)
    for row in membership.itertuples(index=False):
        if row.instrument in active.columns:
            active.loc[row.start : row.end, row.instrument] = True

    raw_counts = close.notna().sum(axis=1)
    factor_counts = factor_valid.sum(axis=1)
    eligible_counts = (factor_valid & active).sum(axis=1)
    report = pd.DataFrame(
        {
            "raw_close": raw_counts,
            "all_factors_and_label": factor_counts,
            "after_membership": eligible_counts,
        }
    )

    print("Last raw close date:", report.index[report["raw_close"] > 0].max().date())
    print(
        "Last all-factor-valid date:",
        report.index[report["all_factors_and_label"] > 0].max().date(),
    )
    print(
        "Last membership-eligible date:",
        report.index[report["after_membership"] > 0].max().date(),
    )
    print("\nCoverage tail:")
    print(report.loc["2019-04-20":].to_string())


if __name__ == "__main__":
    main()
