"""Stream-validate the company OHLCV CSV without loading it into memory."""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path


EXPECTED_COLUMNS = ["datetime", "instrument", "open", "high", "low", "close", "volume"]
INSTRUMENT_RE = re.compile(r"^(SH|SZ)\d{6}$")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path", type=Path)
    args = parser.parse_args()

    row_count = 0
    dates: set[str] = set()
    instruments: set[str] = set()
    blank_volume = 0
    invalid_instrument = 0
    invalid_ohlc = 0
    invalid_ohlc_samples: list[tuple] = []
    zero_ohl_with_close = 0
    other_invalid_ohlc = 0
    max_high_shortfall = 0.0
    max_low_excess = 0.0
    duplicate_keys = 0
    order_errors = 0
    previous_key: tuple[str, str] | None = None

    with args.csv_path.open(encoding="utf-8", newline="") as file:
        reader = csv.reader(file)
        header = next(reader)
        if header != EXPECTED_COLUMNS:
            raise ValueError(f"Unexpected header: {header}")

        for row in reader:
            row_count += 1
            if len(row) != len(EXPECTED_COLUMNS):
                raise ValueError(f"Row {row_count + 1} has {len(row)} columns")

            trade_date, instrument = row[0], row[1]
            key = (trade_date, instrument)
            if previous_key is not None:
                if key == previous_key:
                    duplicate_keys += 1
                elif key < previous_key:
                    order_errors += 1
            previous_key = key

            dates.add(trade_date)
            instruments.add(instrument)
            invalid_instrument += not bool(INSTRUMENT_RE.fullmatch(instrument))

            open_, high, low, close = map(float, row[2:6])
            high_shortfall = max(open_, close, low) - high
            low_excess = low - min(open_, close, high)
            if min(open_, high, low, close) <= 0 or high_shortfall > 0 or low_excess > 0:
                invalid_ohlc += 1
                if open_ == high == low == 0 and close > 0:
                    zero_ohl_with_close += 1
                else:
                    other_invalid_ohlc += 1
                max_high_shortfall = max(max_high_shortfall, high_shortfall)
                max_low_excess = max(max_low_excess, low_excess)
                if len(invalid_ohlc_samples) < 10:
                    invalid_ohlc_samples.append((key, open_, high, low, close))
            if row[6] == "":
                blank_volume += 1
            elif float(row[6]) < 0:
                raise ValueError(f"Negative volume at {key}")

    result = {
        "header": header,
        "rows": row_count,
        "min_date": min(dates),
        "max_date": max(dates),
        "dates": len(dates),
        "instruments": len(instruments),
        "blank_volume": blank_volume,
        "duplicate_keys": duplicate_keys,
        "order_errors": order_errors,
        "invalid_instrument": invalid_instrument,
        "invalid_ohlc": invalid_ohlc,
        "zero_ohl_with_close": zero_ohl_with_close,
        "other_invalid_ohlc": other_invalid_ohlc,
        "max_high_shortfall": max_high_shortfall,
        "max_low_excess": max_low_excess,
    }
    for key, value in result.items():
        print(f"{key}: {value}")
    print(f"invalid_ohlc_samples: {invalid_ohlc_samples}")

    if any(
        result[key]
        for key in (
            "duplicate_keys",
            "order_errors",
            "invalid_instrument",
            "invalid_ohlc",
        )
    ):
        raise SystemExit("Validation failed")


if __name__ == "__main__":
    main()
