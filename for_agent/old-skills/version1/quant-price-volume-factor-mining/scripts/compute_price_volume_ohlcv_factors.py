"""
从固定原始行情 CSV 计算量价/行情类基础因子。

本脚本从标准长表行情 CSV 开始，不依赖具体数据提供方，方便 Agent
在后续流程中稳定、可重复地生成基础因子文件。

输入：
    raw_df CSV，必须包含以下列：
      datetime, instrument, open, high, low, close, volume

输出：
    - 完整长表因子文件（CSV.GZ）
    - 少量预览行（CSV）
    - 中文诊断摘要（JSON）
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path
from typing import Any

import pandas as pd


REQUIRED_COLUMNS = ["open", "high", "low", "close", "volume"]
FACTOR_COLUMNS = [
    "MOM_5D",
    "MOM_20D",
    "VOL_20D",
    "TURN_5D",
    "MA_DEV",
    "DAY_RANGE",
    "PRICE_POS",
]
LABEL_COLUMN = "LABEL"
INDEX_COLUMNS = ["datetime", "instrument"]


DEFAULT_OUTPUT_DIR = "for_agent/results/price_volume_ohlcv_factors"


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


def load_raw_ohlcv(raw_csv: str | Path) -> pd.DataFrame:
    """读取固定原始行情 CSV，并恢复为 (datetime, instrument) MultiIndex。"""
    raw_path = Path(raw_csv)
    if not raw_path.exists():
        raise FileNotFoundError(
            f"找不到原始行情 CSV：{raw_path}\n"
            "请确认 --raw-csv 路径是否正确，或先运行数据导出/准备脚本生成该文件。"
        )
    if not raw_path.is_file():
        raise ValueError(f"--raw-csv 不是文件：{raw_path}")

    raw_df = pd.read_csv(raw_path)
    if raw_df.empty:
        raise ValueError(f"原始行情 CSV 为空：{raw_path}")

    missing = [col for col in INDEX_COLUMNS + REQUIRED_COLUMNS if col not in raw_df.columns]
    if missing:
        raise ValueError(
            f"缺少必要列：{missing}\n"
            f"输入 CSV 至少需要包含：{INDEX_COLUMNS + REQUIRED_COLUMNS}"
        )

    raw_df["datetime"] = pd.to_datetime(raw_df["datetime"], errors="raise")
    raw_df[REQUIRED_COLUMNS] = raw_df[REQUIRED_COLUMNS].astype("float32")
    raw_df = raw_df.set_index(INDEX_COLUMNS).sort_index()
    return raw_df[REQUIRED_COLUMNS]


def stack_wide_frame(df: pd.DataFrame) -> pd.Series:
    try:
        return df.stack(future_stack=True)
    except TypeError:
        return df.stack(dropna=False)


def compute_price_volume_factors(raw_df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    """计算 7 个量价/行情类基础因子和 1 个次日收益标签。"""
    if list(raw_df.index.names) != INDEX_COLUMNS:
        raise ValueError(
            f"raw_df 索引必须是 {INDEX_COLUMNS}，当前为 {list(raw_df.index.names)}。"
        )

    close = raw_df["close"].unstack("instrument")
    high = raw_df["high"].unstack("instrument")
    low = raw_df["low"].unstack("instrument")
    volume = raw_df["volume"].unstack("instrument")

    if close.empty:
        raise ValueError("宽表 close 为空，无法计算因子。请检查输入 CSV 的 datetime/instrument 是否有效。")

    # 显式复现 pandas pct_change 的旧默认行为：先前向填充宽表缺口，再计算日收益。
    # 这样可以贴近原脚本中的 close.pct_change()。
    daily_return = close.ffill().pct_change(fill_method=None)

    factors_wide = {
        "MOM_5D": close / close.shift(5) - 1,
        "MOM_20D": close / close.shift(20) - 1,
        "VOL_20D": daily_return.rolling(20).std(),
        "TURN_5D": volume / volume.rolling(5).mean(),
        "MA_DEV": close / close.rolling(20).mean() - 1,
        "DAY_RANGE": (high - low) / close.shift(1),
        "PRICE_POS": (close - low) / (high - low + 1e-9),
        "LABEL": close.shift(-1) / close - 1,
    }

    factor_df = pd.concat(
        {name: stack_wide_frame(frame) for name, frame in factors_wide.items()},
        axis=1,
    )
    factor_df.index.names = ["datetime", "instrument"]

    wide_diagnostics = {
        name: {
            "shape": list(frame.shape),
            "nan_pct": float(frame.isna().mean().mean() * 100),
        }
        for name, frame in factors_wide.items()
    }
    warnings = []
    if diagnostics_nan_pct := float(factor_df.isna().any(axis=1).mean() * 100):
        if diagnostics_nan_pct > 50:
            warnings.append(
                f"因子长表含 NaN 行占比为 {diagnostics_nan_pct:.2f}%，超过 50%。请检查数据覆盖率或交易日对齐。"
            )
    if len(raw_df) < len(close.index) * len(close.columns):
        warnings.append("原始长表不是完整交易日 × 股票矩阵；宽表中存在缺口，部分滚动因子会产生 NaN。")

    diagnostics = {
        "说明": "量价/行情类基础因子计算诊断。长表行数为交易日数 × 股票数，NaN 将在后续预处理阶段处理。",
        "raw_shape": list(raw_df.shape),
        "raw_index_names": list(raw_df.index.names),
        "date_min": str(raw_df.index.get_level_values("datetime").min().date()),
        "date_max": str(raw_df.index.get_level_values("datetime").max().date()),
        "n_days": int(raw_df.index.get_level_values("datetime").nunique()),
        "n_instruments": int(raw_df.index.get_level_values("instrument").nunique()),
        "wide_shape": list(close.shape),
        "factor_shape": list(factor_df.shape),
        "factor_columns": FACTOR_COLUMNS,
        "label_column": LABEL_COLUMN,
        "wide_factor_diagnostics": wide_diagnostics,
        "long_table_nan_rows": int(factor_df.isna().any(axis=1).sum()),
        "long_table_nan_row_pct": diagnostics_nan_pct,
        "warnings": warnings,
    }
    return factor_df, diagnostics


def save_outputs(
    factor_df: pd.DataFrame,
    diagnostics: dict[str, Any],
    output_dir: str | Path,
    output_prefix: str,
    preview_rows: int,
) -> dict[str, str]:
    out_dir = Path(output_dir)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OSError(
            f"无法创建输出目录：{out_dir}\n"
            "请检查 --output-dir 路径、磁盘权限或目录是否被其他程序占用。"
        ) from exc

    if not out_dir.is_dir():
        raise ValueError(f"输出路径已存在但不是目录：{out_dir}")
    if preview_rows <= 0:
        raise ValueError("--preview-rows 必须是正整数。")

    factor_csv_gz = out_dir / f"{output_prefix}.csv.gz"
    preview_csv = out_dir / f"{output_prefix}_preview.csv"
    diagnostics_json = out_dir / f"{output_prefix}_diagnostics.json"

    factor_df.to_csv(factor_csv_gz, compression="gzip")
    factor_df.head(preview_rows).to_csv(preview_csv)
    diagnostics_json.write_text(
        json.dumps(diagnostics, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return {
        "factor_csv_gz": str(factor_csv_gz),
        "preview_csv": str(preview_csv),
        "diagnostics_json": str(diagnostics_json),
    }


def build_output_prefix(raw_csv: str | Path, output_prefix: str | None) -> str:
    """根据输入数据文件名生成默认输出前缀。"""
    prefix = output_prefix or f"price_volume_ohlcv_factors_{Path(raw_csv).stem}"
    unsafe_chars = set('<>:"/\\|?*')
    if any(ch in unsafe_chars for ch in prefix):
        raise ValueError(
            f"输出文件名前缀包含不适合作为文件名的字符：{prefix}\n"
            "请通过 --output-prefix 传入只包含字母、数字、下划线或短横线的名称。"
        )
    return prefix


def print_step2_like_summary(diagnostics: dict[str, Any]) -> None:
    """按因子计算阶段的屏幕输出风格打印诊断信息。"""
    wide_shape = tuple(diagnostics["wide_shape"])
    factor_shape = tuple(diagnostics["factor_shape"])
    n_days = diagnostics["n_days"]
    n_instruments = diagnostics["n_instruments"]

    print("\n" + "=" * 65)
    print("  因子计算（从 OHLCV 手工推导 7 个基础因子）")
    print("=" * 65)
    print(
        f"宽表 close  shape = {wide_shape}  "
        f"({wide_shape[0]} 交易日 × {wide_shape[1]} 股票)"
    )
    print("（每个字段都是同样形状的宽表，以下因子计算均在宽表上进行）\n")

    print("因子宽表 shape（每个均与 close 相同）:")
    for name in FACTOR_COLUMNS + [LABEL_COLUMN]:
        item = diagnostics["wide_factor_diagnostics"][name]
        shape = tuple(item["shape"])
        nan_pct = item["nan_pct"]
        print(f"  {name:<12}  shape={shape}  NaN占比={nan_pct:.1f}%")

    print(f"\n因子数据（long format）shape = {factor_shape}")
    print("  每行含义：一只股票在某个交易日的 7 个因子值 + 1 个 label")
    print(
        f"  总行数 ≈ {n_days} 交易日 × {n_instruments} 股票 = "
        f"{n_days * n_instruments}（含 NaN 未删除）"
    )

    warnings = diagnostics.get("warnings", [])
    if warnings:
        print("\n提醒:")
        for item in warnings:
            print(f"  - {item}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="从固定原始行情 CSV 计算量价/行情类基础因子。",
    )
    parser.add_argument("--raw-csv", required=True, help="原始行情 CSV 路径，必须包含 datetime、instrument、open、high、low、close、volume。")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="输出目录。")
    parser.add_argument("--output-prefix", default=None, help="输出文件名前缀；不传则根据 --raw-csv 文件名自动生成。")
    parser.add_argument("--preview-rows", type=int, default=20, help="预览 CSV 保存的行数。")
    parser.add_argument("--debug", action="store_true", help="出错时打印完整 Python traceback，便于开发调试。")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        output_prefix = build_output_prefix(args.raw_csv, args.output_prefix)
        raw_df = load_raw_ohlcv(args.raw_csv)
        factor_df, diagnostics = compute_price_volume_factors(raw_df)
        output_paths = save_outputs(
            factor_df=factor_df,
            diagnostics=diagnostics,
            output_dir=args.output_dir,
            output_prefix=output_prefix,
            preview_rows=args.preview_rows,
        )
    except Exception as exc:
        print("\n[错误] 量价/行情类基础因子计算失败。", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        print("\n建议排查：", file=sys.stderr)
        print("  1. 确认 --raw-csv 指向存在的 CSV 文件。", file=sys.stderr)
        print("  2. 确认 CSV 包含 datetime、instrument、open、high、low、close、volume 列。", file=sys.stderr)
        print("  3. 确认 --output-dir 可创建、可写入。", file=sys.stderr)
        print("  4. 如需调试细节，请追加 --debug 查看完整 traceback。", file=sys.stderr)
        if args.debug:
            print("\n完整 traceback:", file=sys.stderr)
            traceback.print_exc()
        sys.exit(1)

    print_step2_like_summary(diagnostics)

    print("\n脚本输出文件:")
    for name, path in output_paths.items():
        print(f"  {name}: {path}")


if __name__ == "__main__":
    main()
