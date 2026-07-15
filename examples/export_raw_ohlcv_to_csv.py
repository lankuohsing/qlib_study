"""
Export the raw OHLCV dataset used by quant_workflow_from_scratch.py.

This script treats Qlib only as a one-time data exporter.  It saves the
DataFrame immediately after:

    raw_df = raw_df.swaplevel().sort_index()

The exported CSV can then be used as a fixed, Qlib-independent dataset.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


DEFAULT_PROVIDER_URI = r"D:\projects\github\qlib_study\datasets\cn_data"
DEFAULT_OUTPUT_DIR = "datasets/exported"
DEFAULT_UNIVERSE = "csi300"
DEFAULT_DATA_START = "2014-06-01"
DEFAULT_DATA_END = "2020-08-01"

FIELDS = ["$open", "$high", "$low", "$close", "$volume"]
COLUMNS = ["open", "high", "low", "close", "volume"]


def export_raw_ohlcv(
    provider_uri: str,
    output_dir: str | Path,
    universe: str = DEFAULT_UNIVERSE,
    start_time: str = DEFAULT_DATA_START,
    end_time: str = DEFAULT_DATA_END,
) -> dict[str, Path]:
    from qlib.constant import REG_CN
    from qlib.data import D
    import pandas as pd
    import qlib

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    qlib.init(provider_uri=provider_uri, region=REG_CN)

    membership_dict = D.list_instruments(
        D.instruments(universe),
        start_time=start_time,
        end_time=end_time,
        freq="day",
        as_list=False,
    )
    all_stocks = list(membership_dict.keys())

    raw_df = D.features(
        all_stocks,
        fields=FIELDS,
        start_time=start_time,
        end_time=end_time,
        freq="day",
    )
    raw_df.columns = COLUMNS
    raw_df.index.names = ["instrument", "datetime"]
    raw_df = raw_df.swaplevel().sort_index()

    dataset_name = f"raw_ohlcv_{universe}_{start_time.replace('-', '')}_{end_time.replace('-', '')}"
    raw_csv_path = output_path / f"{dataset_name}.csv"
    membership_csv_path = output_path / f"{dataset_name}_membership.csv"
    metadata_path = output_path / f"{dataset_name}_metadata.json"

    raw_df.to_csv(raw_csv_path, index=True)

    membership_rows = []
    for instrument, spans in membership_dict.items():
        for span_start, span_end in spans:
            membership_rows.append(
                {
                    "instrument": instrument,
                    "start_time": span_start,
                    "end_time": span_end,
                }
            )
    pd.DataFrame(membership_rows).to_csv(membership_csv_path, index=False)

    metadata = {
        "provider_uri": provider_uri,
        "universe": universe,
        "start_time": start_time,
        "end_time": end_time,
        "fields": FIELDS,
        "columns": COLUMNS,
        "index": ["datetime", "instrument"],
        "shape": list(raw_df.shape),
        "n_days": int(raw_df.index.get_level_values("datetime").nunique()),
        "n_instruments": int(raw_df.index.get_level_values("instrument").nunique()),
        "raw_csv": raw_csv_path.name,
        "membership_csv": membership_csv_path.name,
        "read_example": (
            "pd.read_csv(raw_csv, parse_dates=['datetime'])"
            ".set_index(['datetime', 'instrument']).sort_index()"
        ),
    }
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"Saved raw dataset: {raw_csv_path}")
    print(f"Saved membership:  {membership_csv_path}")
    print(f"Saved metadata:    {metadata_path}")
    print(f"raw_df shape:      {raw_df.shape}")
    print(f"index names:       {raw_df.index.names}")
    print(f"date range:        {raw_df.index.get_level_values('datetime').min()} ~ "
          f"{raw_df.index.get_level_values('datetime').max()}")
    print(f"instruments:       {raw_df.index.get_level_values('instrument').nunique()}")

    return {
        "raw_csv": raw_csv_path,
        "membership_csv": membership_csv_path,
        "metadata": metadata_path,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export Qlib raw OHLCV data to a fixed CSV dataset.",
    )
    parser.add_argument("--provider-uri", default=DEFAULT_PROVIDER_URI)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--universe", default=DEFAULT_UNIVERSE)
    parser.add_argument("--start-time", default=DEFAULT_DATA_START)
    parser.add_argument("--end-time", default=DEFAULT_DATA_END)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    export_raw_ohlcv(
        provider_uri=args.provider_uri,
        output_dir=args.output_dir,
        universe=args.universe,
        start_time=args.start_time,
        end_time=args.end_time,
    )
