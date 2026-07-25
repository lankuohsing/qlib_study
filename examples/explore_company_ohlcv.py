"""Inspect the company Oracle daily-stock tables without exposing credentials."""

from __future__ import annotations

from pathlib import Path

import oracledb
import yaml


SECRETS_PATH = Path(
    r"D:\projects\gitlab\quantitative_trade_agent\query_embase_database\secrets.yml"
)
START_DATE = "2020-01-01"
END_DATE = "2026-06-01"


def connect() -> oracledb.Connection:
    with SECRETS_PATH.open(encoding="utf-8") as file:
        secrets = yaml.safe_load(file)
    dsn = f"{secrets['host']}:11521/EMBASE"
    return oracledb.connect(
        user=secrets["user"], password=secrets["password"], dsn=dsn
    )


def print_query(
    cursor: oracledb.Cursor, title: str, sql: str, binds: dict | None = None
) -> None:
    print(f"\n--- {title} ---")
    cursor.execute(
        sql,
        binds if binds is not None else {"start_date": START_DATE, "end_date": END_DATE},
    )
    print([item[0] for item in cursor.description])
    for row in cursor:
        print(row)


def main() -> None:
    date_filter = """
        TDATE >= TO_DATE(:start_date, 'YYYY-MM-DD')
        AND TDATE < TO_DATE(:end_date, 'YYYY-MM-DD') + 1
    """

    with connect() as connection, connection.cursor() as cursor:
        print_query(
            cursor,
            "coverage",
            f"""
            SELECT MIN(TDATE) AS MIN_DATE,
                   MAX(TDATE) AS MAX_DATE,
                   COUNT(*) AS ROW_COUNT,
                   COUNT(DISTINCT TDATE) AS DATE_COUNT,
                   COUNT(DISTINCT SECUCODE) AS SECURITY_COUNT
            FROM NEWSADMIN.TRAD_SK_DAILY_JC
            WHERE {date_filter}
            """,
        )

        print_query(
            cursor,
            "exchange values",
            f"""
            SELECT TEXCH, COUNT(*) AS ROW_COUNT,
                   COUNT(DISTINCT SECUCODE) AS SECURITY_COUNT
            FROM NEWSADMIN.TRAD_SK_DAILY_JC
            WHERE {date_filter}
            GROUP BY TEXCH
            ORDER BY ROW_COUNT DESC
            """,
        )

        print_query(
            cursor,
            "sample latest rows",
            f"""
            SELECT * FROM (
                SELECT SECUCODE, SNAME, TDATE, TEXCH,
                       OPEN, HIGH, LOW, NEW, TVOL, SECURITYVARIETYCODE
                FROM NEWSADMIN.TRAD_SK_DAILY_JC
                WHERE {date_filter}
                ORDER BY TDATE DESC, SECUCODE
            ) WHERE ROWNUM <= 20
            """,
        )

        print_query(
            cursor,
            "duplicate code-date keys",
            f"""
            SELECT COUNT(*) AS DUPLICATE_KEY_COUNT,
                   COALESCE(SUM(N_ROWS - 1), 0) AS EXTRA_ROW_COUNT
            FROM (
                SELECT SECUCODE, TDATE, COUNT(*) AS N_ROWS
                FROM NEWSADMIN.TRAD_SK_DAILY_JC
                WHERE {date_filter}
                GROUP BY SECUCODE, TDATE
                HAVING COUNT(*) > 1
            )
            """,
        )

        print_query(
            cursor,
            "null and invalid OHLCV",
            f"""
            SELECT SUM(CASE WHEN OPEN IS NULL THEN 1 ELSE 0 END) AS NULL_OPEN,
                   SUM(CASE WHEN HIGH IS NULL THEN 1 ELSE 0 END) AS NULL_HIGH,
                   SUM(CASE WHEN LOW IS NULL THEN 1 ELSE 0 END) AS NULL_LOW,
                   SUM(CASE WHEN NEW IS NULL THEN 1 ELSE 0 END) AS NULL_CLOSE,
                   SUM(CASE WHEN TVOL IS NULL THEN 1 ELSE 0 END) AS NULL_VOLUME,
                   SUM(CASE WHEN NEW <= 0 THEN 1 ELSE 0 END) AS NONPOS_CLOSE,
                   SUM(CASE WHEN HIGH < LOW THEN 1 ELSE 0 END) AS HIGH_LT_LOW
            FROM NEWSADMIN.TRAD_SK_DAILY_JC
            WHERE {date_filter}
            """,
        )

        print_query(
            cursor,
            "price and adjustment-factor samples",
            """
            SELECT d.SECUCODE, d.TDATE, d.OPEN, d.HIGH, d.LOW, d.NEW, d.TVOL,
                   f.AFACTOR, f.TAFACTOR, f.TBFACTOR
            FROM NEWSADMIN.TRAD_SK_DAILY_JC d
            LEFT JOIN NEWSADMIN.TRAD_SK_FACTOR1 f
              ON f.SECURITYVARIETYCODE = d.SECURITYVARIETYCODE
             AND f.TRADEDATE = d.TDATE
            WHERE d.SECUCODE IN ('600000', '000001')
              AND d.TDATE IN (DATE '2014-06-03', DATE '2020-01-02', DATE '2026-06-01')
            ORDER BY d.SECUCODE, d.TDATE
            """,
            {},
        )

        print_query(
            cursor,
            "eligible Shanghai/Shenzhen A-share coverage and factor quality",
            f"""
            SELECT MIN(d.TDATE) AS MIN_DATE,
                   MAX(d.TDATE) AS MAX_DATE,
                   COUNT(*) AS ROW_COUNT,
                   COUNT(DISTINCT d.TDATE) AS DATE_COUNT,
                   COUNT(DISTINCT d.SECUCODE) AS SECURITY_COUNT,
                   SUM(CASE WHEN d.TVOL IS NULL THEN 1 ELSE 0 END) AS NULL_VOLUME,
                   SUM(CASE WHEN f.TBFACTOR IS NULL THEN 1 ELSE 0 END) AS NULL_FACTOR,
                   SUM(CASE WHEN f.TBFACTOR <= 0 THEN 1 ELSE 0 END) AS NONPOS_FACTOR
            FROM NEWSADMIN.TRAD_SK_DAILY_JC d
            LEFT JOIN NEWSADMIN.TRAD_SK_FACTOR1 f
              ON f.SECURITYVARIETYCODE = d.SECURITYVARIETYCODE
             AND f.TRADEDATE = d.TDATE
            WHERE d.TDATE >= TO_DATE(:start_date, 'YYYY-MM-DD')
              AND d.TDATE < TO_DATE(:end_date, 'YYYY-MM-DD') + 1
              AND d.TEXCH IN ('上交所', '深交所')
              AND EXISTS (
                  SELECT 1
                  FROM NEWSADMIN.CDSY_SECUCODE s
                  WHERE s.SECURITYVARIETYCODE = d.SECURITYVARIETYCODE
                    AND s.SECURITYTYPECODE = '058001001'
                    AND d.TDATE >= s.LISTINGDATE
                    AND (s.ENDDATE IS NULL OR d.TDATE < s.ENDDATE)
              )
            """,
        )


if __name__ == "__main__":
    main()
