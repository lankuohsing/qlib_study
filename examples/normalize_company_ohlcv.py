"""Normalize zero-OHL suspension placeholders into valid flat OHLC bars."""

from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path", type=Path)
    args = parser.parse_args()

    temporary_path = args.csv_path.with_suffix(args.csv_path.suffix + ".normalize.tmp")
    repaired = 0
    with args.csv_path.open(encoding="utf-8", newline="") as source, temporary_path.open(
        "w", encoding="utf-8", newline=""
    ) as target:
        reader = csv.reader(source)
        writer = csv.writer(target, lineterminator="\n")
        header = next(reader)
        writer.writerow(header)
        for row in reader:
            if row[2] == row[3] == row[4] == "0" and float(row[5]) > 0:
                row[2] = row[3] = row[4] = row[5]
                repaired += 1
            writer.writerow(row)

    os.replace(temporary_path, args.csv_path)
    print(f"Normalized {repaired:,} zero-OHL suspension rows in {args.csv_path}")


if __name__ == "__main__":
    main()
