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

    # 将 CSV 中的 datetime 字符串统一解析为 pandas 日期时间类型；errors="raise"
    # 表示遇到无法识别的日期时立即报错，避免错误日期悄悄变成缺失值并进入因子计算。
    raw_df["datetime"] = pd.to_datetime(raw_df["datetime"], errors="raise")

    # 将 open/high/low/close/volume 五个行情字段统一转换为 float32：确保后续运算使用
    # 数值类型，同时相比默认 float64 减少约一半内存占用；非法数值会在此处直接报错。
    raw_df[REQUIRED_COLUMNS] = raw_df[REQUIRED_COLUMNS].astype("float32")

    # 把 datetime、instrument 两列设为按“日期 → 股票”排列的 MultiIndex，并按索引排序，
    # 方便后续按日期/股票切片，以及通过 unstack("instrument") 转成日期×股票宽表。
    # 【风险点/以后可改】数据契约要求 (datetime, instrument) 唯一，但当前没有在这里
    # 主动检查重复主键；若存在重复记录，后面的 unstack() 才会报错，提示不够直观。
    # 可在 set_index 前用 raw_df.duplicated(INDEX_COLUMNS) 检查并展示重复样本。
    # 还可增加 OHLCV 合法性检查，例如价格必须为正、成交量不能为负、high >= low，
    # 且正常情况下 open/close 应位于 [low, high] 内；异常记录宜报警或置为 NaN。
    raw_df = raw_df.set_index(INDEX_COLUMNS).sort_index()
    return raw_df[REQUIRED_COLUMNS]


def stack_wide_frame(df: pd.DataFrame) -> pd.Series:
    try:
        return df.stack(future_stack=True)
    except TypeError:
        return df.stack(dropna=False)


def compute_price_volume_factors(raw_df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    """计算 7 个量价因子和 T+1 开盘至 T+2 开盘收益标签。"""
    if list(raw_df.index.names) != INDEX_COLUMNS:
        raise ValueError(
            f"raw_df 索引必须是 {INDEX_COLUMNS}，当前为 {list(raw_df.index.names)}。"
        )

    open_ = raw_df["open"].unstack("instrument")
    close = raw_df["close"].unstack("instrument")
    high = raw_df["high"].unstack("instrument")
    low = raw_df["low"].unstack("instrument")
    volume = raw_df["volume"].unstack("instrument")

    if close.empty:
        raise ValueError("宽表 close 为空，无法计算因子。请检查输入 CSV 的 datetime/instrument 是否有效。")

    # 显式复现 pandas pct_change 的旧默认行为：先前向填充宽表缺口，再计算日收益。
    # 这样可以贴近原脚本中的 close.pct_change()。
    # 【风险点/以后可改】ffill 会把停牌或缺价期间视为价格不变、日收益为 0，且可能让
    # VOL_20D 在当日 close 本身缺失时仍有数值；这与没有填充的 MOM/MA_DEV 口径不同。
    # 教学上可选择更严格的 close.pct_change(fill_method=None)，完整保留缺失传播；
    # 若保留停牌按 0 收益的估值口径，建议至少在最终 VOL_20D 上用 .where(close.notna())，
    # 避免给当日没有有效收盘价的股票输出看似可用的波动率因子。
    daily_return = close.ffill().pct_change(fill_method=None)

    factors_wide = {
        "MOM_5D": close / close.shift(5) - 1,# 今日收盘价 ÷ 5个交易日前收盘价 - 1
        "MOM_20D": close / close.shift(20) - 1,# 20日收益动量
        "VOL_20D": daily_return.rolling(20).std(),# 20日收益波动率
        # 【命名提示/以后可改】该公式是“成交量比”而非真正换手率；真正换手率还需要
        # 流通股本。当前5日均量包含今日成交量；若要衡量今日相对“此前5日”的放量，
        # 可改为 volume / volume.shift(1).rolling(5).mean()，并考虑重命名 VOLUME_RATIO_5D。
        "TURN_5D": volume / volume.rolling(5).mean(),# 今日成交量 ÷ 最近5个交易日平均成交量；5日成交量比
        "MA_DEV": close / close.rolling(20).mean() - 1,# 相对20日均线的偏离程度
        "DAY_RANGE": (high - low) / close.shift(1),# 当日振幅
        # 【重要风险点/以后优先改】+1e-9 虽可避免除零，但当 high == low 或 OHLC 数据
        # 不一致时，会把极小误差放大成巨大有限值；NaN 诊断也发现不了这种异常。
        # 更严谨的做法是仅在 high > low 且 low <= close <= high 时计算，其余置为 NaN：
        # price_range = high - low
        # valid = price_range.gt(0) & close.ge(low) & close.le(high)
        # PRICE_POS = ((close - low) / price_range).where(valid)
        "PRICE_POS": (close - low) / (high - low + 1e-9),# 收盘价在当日区间中的位置
        # T 日收盘后生成信号，T+1 开盘成交，T+2 开盘调仓/退出。
        # 标签和回测持有区间必须完全一致，不能用 T 日收盘价成交。
        # 【边界说明/以后可增强】这里只按开盘价构造理论收益标签，没有检查 T+1/T+2
        # 是否可交易（例如零成交量、停牌、涨跌停）；教学流程可在后续训练样本或回测
        # 阶段结合成交量和交易规则处理，不能把“有 LABEL”直接等同于“一定能成交”。
        "LABEL": open_.shift(-2) / open_.shift(-1) - 1,
    }

    factor_df = pd.concat(
        {name: stack_wide_frame(frame) for name, frame in factors_wide.items()},
        axis=1,
    )# 主键是datetime和instrument，列是因子名称；长表格式
    factor_df.index.names = ["datetime", "instrument"]

    # 【风险点/以后可改】当前诊断主要统计 NaN，无法识别“不是 NaN 但数值明显异常”的
    # 情况，例如 inf、PRICE_POS 超出 [0, 1] 或由极小分母产生的超大值。可为每个因子
    # 增加有限值 min/max、inf_count 和业务范围越界数量，并记录公式及信息可用时点。
    wide_diagnostics = {# 为 factors_wide 中的每个因子生成一份数据质量诊断信息
        name: {
            "shape": list(frame.shape),
            "nan_pct": float(frame.isna().mean().mean() * 100),
        }
        for name, frame in factors_wide.items()
    }# 因子宽表的形状；缺失值占全部单元格的百分比。
    warnings = []
    if diagnostics_nan_pct := float(factor_df.isna().any(axis=1).mean() * 100):
        if diagnostics_nan_pct > 50:
            warnings.append(
                f"因子长表含 NaN 行占比为 {diagnostics_nan_pct:.2f}%，超过 50%。请检查数据覆盖率或交易日对齐。"
            )
    if len(raw_df) < len(close.index) * len(close.columns):
        warnings.append("原始长表不是完整交易日 × 股票矩阵；宽表中存在缺口，部分滚动因子会产生 NaN。")

    diagnostics = {
        "说明": "量价因子计算诊断。LABEL 为 T+1 开盘至 T+2 开盘收益；NaN 留给预处理阶段按用途处理。",
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
        "label_interval": "open[T+2] / open[T+1] - 1",
        "signal_time": "T 日收盘后",
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
    parser.add_argument("--raw-csv", default=r"D:\projects\github\qlib_study\datasets\exported\raw_ohlcv_csi300_20100101_20190601.csv", help="原始行情 CSV 路径，必须包含 datetime、instrument、open、high、low、close、volume。")
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
