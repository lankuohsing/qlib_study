"""Export company Oracle OHLCV data in the same schema as outputs/raw_df.csv.

The company table stores unadjusted prices.  Qlib's ``$open/$high/$low/$close``
and volume fields in the reference CSV are adjustment-factor normalized, so this
export applies ``TBFACTOR`` to prices and its reciprocal to volume.
"""

from __future__ import annotations

import argparse
import csv
import os
from datetime import date, datetime, timedelta
from pathlib import Path

import oracledb
import yaml


DEFAULT_SECRETS = Path(
    r"D:\projects\gitlab\quantitative_trade_agent\query_embase_database\secrets.yml"
)
DEFAULT_OUTPUT = Path("outputs/raw_df_company_20200101_20260601.csv")
CSV_COLUMNS = ["datetime", "instrument", "open", "high", "low", "close", "volume"]

EXPORT_SQL = """
SELECT TO_CHAR(d.TDATE, 'YYYY-MM-DD') AS datetime,
       CASE d.TEXCH
           WHEN '上交所' THEN 'SH' || d.SECUCODE
           WHEN '深交所' THEN 'SZ' || d.SECUCODE
       END AS instrument,
       CASE WHEN d.OPEN = 0 AND d.HIGH = 0 AND d.LOW = 0 AND d.NEW > 0
            THEN d.NEW ELSE d.OPEN END * f.TBFACTOR AS open,
       CASE WHEN d.OPEN = 0 AND d.HIGH = 0 AND d.LOW = 0 AND d.NEW > 0
            THEN d.NEW ELSE d.HIGH END * f.TBFACTOR AS high,
       CASE WHEN d.OPEN = 0 AND d.HIGH = 0 AND d.LOW = 0 AND d.NEW > 0
            THEN d.NEW ELSE d.LOW END * f.TBFACTOR AS low,
       d.NEW * f.TBFACTOR AS close,
       d.TVOL / f.TBFACTOR AS volume
FROM NEWSADMIN.TRAD_SK_DAILY_JC d
JOIN NEWSADMIN.TRAD_SK_FACTOR1 f
  ON f.SECURITYVARIETYCODE = d.SECURITYVARIETYCODE
 AND f.TRADEDATE = d.TDATE
WHERE d.TDATE >= :chunk_start
  AND d.TDATE < :chunk_end
  AND d.TEXCH IN ('上交所', '深交所')
  AND f.TBFACTOR > 0
  AND EXISTS (
      SELECT 1
      FROM NEWSADMIN.CDSY_SECUCODE s
      WHERE s.SECURITYVARIETYCODE = d.SECURITYVARIETYCODE
        AND s.SECURITYTYPECODE = '058001001'
        AND d.TDATE >= s.LISTINGDATE
        AND (s.ENDDATE IS NULL OR d.TDATE < s.ENDDATE)
  )
ORDER BY d.TDATE, instrument
"""


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def year_chunks(start: date, inclusive_end: date):
    exclusive_end = inclusive_end + timedelta(days=1)
    chunk_start = start
    while chunk_start < exclusive_end:
        next_year = date(chunk_start.year + 1, 1, 1)
        chunk_end = min(next_year, exclusive_end)
        yield chunk_start, chunk_end
        chunk_start = chunk_end


def connect(secrets_path: Path) -> oracledb.Connection:
    with secrets_path.open(encoding="utf-8") as file:
        secrets = yaml.safe_load(file)
    return oracledb.connect(
        user=secrets["user"],
        password=secrets["password"],
        dsn=f"{secrets['host']}:11521/EMBASE",
    )


def export_csv(
    connection: oracledb.Connection,
    output_path: Path,
    start: date,
    inclusive_end: date,
) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(output_path.suffix + ".tmp")
    row_count = 0

    with temporary_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file, lineterminator="\n")
        writer.writerow(CSV_COLUMNS)

        with connection.cursor() as cursor:
            cursor.arraysize = 10_000
            cursor.prefetchrows = 10_000
            for chunk_start, chunk_end in year_chunks(start, inclusive_end):
                cursor.execute(
                    EXPORT_SQL,
                    chunk_start=datetime.combine(chunk_start, datetime.min.time()),
                    chunk_end=datetime.combine(chunk_end, datetime.min.time()),
                )
                chunk_rows = 0
                while True:
                    rows = cursor.fetchmany(10_000)
                    if not rows:
                        break
                    writer.writerows(rows)
                    chunk_rows += len(rows)
                    row_count += len(rows)
                print(
                    f"{chunk_start} to {chunk_end - timedelta(days=1)}: "
                    f"{chunk_rows:,} rows"
                )
                file.flush()

    os.replace(temporary_path, output_path)
    return row_count


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", type=parse_date, default=parse_date("2020-01-01"))
    parser.add_argument("--end-date", type=parse_date, default=parse_date("2026-06-01"))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--secrets", type=Path, default=DEFAULT_SECRETS)
    args = parser.parse_args()

    if args.end_date < args.start_date:
        parser.error("--end-date must not be earlier than --start-date")

    with connect(args.secrets) as connection:
        row_count = export_csv(
            connection, args.output, args.start_date, args.end_date
        )
    print(f"Saved {row_count:,} rows to {args.output.resolve()}")


if __name__ == "__main__":
    main()
