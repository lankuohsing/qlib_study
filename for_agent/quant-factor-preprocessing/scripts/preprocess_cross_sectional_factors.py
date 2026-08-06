"""
对因子长表执行截面预处理和股票池成员资格过滤。

本脚本承接基础因子计算阶段的输出，将未清洗的因子长表转换为
可直接用于 IC 分析、因子合成、模型训练和回测的干净样本表。

输入：
    - 因子 CSV/CSV.GZ，必须包含 datetime、instrument、至少一个因子列和 LABEL
    - 成员资格 CSV，必须包含 instrument、start_time、end_time

输出：
    - 完整清洗样本（CSV.GZ）
    - train/valid/test 三段样本（CSV.GZ）
    - 少量预览行（CSV）
    - 中文诊断摘要（JSON）
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
import warnings
from pathlib import Path
from typing import Any

import pandas as pd

warnings.filterwarnings("ignore", category=RuntimeWarning, message="Mean of empty slice")


EXAMPLE_FACTOR_COLUMNS = [
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
MEMBERSHIP_COLUMNS = ["instrument", "start_time", "end_time"]

DEFAULT_OUTPUT_DIR = "for_agent/results/factor_preprocessing"
DEFAULT_FACTOR_CSV = (
    "for_agent/results/price_volume_ohlcv_factors/"
    "price_volume_ohlcv_factors_raw_ohlcv_csi300_20100101_20190601.csv.gz"
)
DEFAULT_MEMBERSHIP_CSV = (
    "datasets/exported/raw_ohlcv_csi300_20100101_20190601_membership.csv"
)
DEFAULT_TRAIN_START = "2010-01-01"
DEFAULT_TRAIN_END = "2014-12-31"
DEFAULT_VALID_START = "2015-01-01"
DEFAULT_VALID_END = "2017-12-31"
DEFAULT_TEST_START = "2018-01-01"
DEFAULT_TEST_END = "2019-06-01"


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


def validate_file(path_value: str | Path, arg_name: str) -> Path:
    path = Path(path_value)
    if not path.exists():
        raise FileNotFoundError(f"找不到 {arg_name} 指向的文件：{path}")
    if not path.is_file():
        raise ValueError(f"{arg_name} 指向的路径不是文件：{path}")
    return path


def parse_factor_columns(factor_cols: str | None) -> list[str] | None:
    """解析手动指定的因子列；未指定时返回 None，后续从 CSV 自动识别。"""
    if factor_cols is None:
        return None
    columns = [col.strip() for col in factor_cols.split(",") if col.strip()]
    if not columns:
        raise ValueError("--factor-cols 不能为空；请传入逗号分隔的因子列名，或删除该参数使用自动识别。")
    return columns


def infer_factor_columns(columns: list[str], explicit_factor_cols: list[str] | None) -> list[str]:
    """确定需要预处理的因子列。默认使用除索引列和 LABEL 外的所有列。"""
    if explicit_factor_cols is not None:
        missing = [col for col in explicit_factor_cols if col not in columns]
        if missing:
            raise ValueError(f"--factor-cols 中包含输入 CSV 不存在的列：{missing}")
        return explicit_factor_cols

    excluded = set(INDEX_COLUMNS + [LABEL_COLUMN])
    inferred = [col for col in columns if col not in excluded]
    if not inferred:
        raise ValueError(
            "未能从因子 CSV 自动识别因子列。\n"
            "输入至少需要包含 datetime、instrument、LABEL，以及至少一个因子列；"
            "也可以通过 --factor-cols 手动指定。"
        )
    return inferred


def load_factor_table(
    factor_csv: str | Path,
    explicit_factor_cols: list[str] | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    """读取因子长表，并恢复为 (datetime, instrument) MultiIndex。"""
    factor_path = validate_file(factor_csv, "--factor-csv")
    factor_df = pd.read_csv(factor_path)
    if factor_df.empty:
        raise ValueError(f"因子 CSV 为空：{factor_path}")

    required = INDEX_COLUMNS + [LABEL_COLUMN]
    missing = [col for col in required if col not in factor_df.columns]
    if missing:
        raise ValueError(
            f"因子 CSV 缺少必要列：{missing}\n"
            f"输入至少需要包含：{required}，以及至少一个因子列。"
        )

    factor_columns = infer_factor_columns(list(factor_df.columns), explicit_factor_cols)
    factor_df["datetime"] = pd.to_datetime(factor_df["datetime"], errors="raise")
    factor_df = factor_df.set_index(INDEX_COLUMNS).sort_index()
    return factor_df[factor_columns + [LABEL_COLUMN]], factor_columns


def load_membership_table(membership_csv: str | Path) -> pd.DataFrame:
    """读取股票池成员资格表。"""
    membership_path = validate_file(membership_csv, "--membership-csv")
    membership_df = pd.read_csv(membership_path)
    if membership_df.empty:
        raise ValueError(f"成员资格 CSV 为空：{membership_path}")

    missing = [col for col in MEMBERSHIP_COLUMNS if col not in membership_df.columns]
    if missing:
        raise ValueError(
            f"成员资格 CSV 缺少必要列：{missing}\n"
            f"输入至少需要包含：{MEMBERSHIP_COLUMNS}"
        )

    membership_df = membership_df[MEMBERSHIP_COLUMNS].copy()
    membership_df["start_time"] = pd.to_datetime(membership_df["start_time"], errors="raise")
    membership_df["end_time"] = pd.to_datetime(membership_df["end_time"], errors="raise")
    if (membership_df["start_time"] > membership_df["end_time"]).any():
        raise ValueError("成员资格 CSV 中存在 start_time 晚于 end_time 的区间。")
    return membership_df


def winsorize_cs(s: pd.Series, n_sigma: float = 3.0) -> pd.Series:
    """MAD 法截面去极值。对“当日股票池内的因子值”执行每日截面去极值处理"""
    median = s.median()# 同一天，同一个因子的股票截面的中位数
    mad = (s - median).abs().median()# 同一天，每个股票的因子值与中位数的绝对差值的中位数
    lo = median - n_sigma * 1.4826 * mad
    hi = median + n_sigma * 1.4826 * mad
    return s.clip(lo, hi)


def zscore_cs(s: pd.Series) -> pd.Series:
    """截面 Z-Score 标准化。"""
    return (s - s.mean()) / (s.std() + 1e-9)


def segment(df: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    dt = df.index.get_level_values("datetime")
    return df[(dt >= pd.Timestamp(start)) & (dt <= pd.Timestamp(end))]


def parse_date_arg(value: str, arg_name: str) -> pd.Timestamp:
    """解析 CLI 日期参数，并给出面向使用者的错误提示。"""
    try:
        return pd.Timestamp(value)
    except Exception as exc:
        raise ValueError(f"{arg_name} 不是合法日期：{value}，请使用 YYYY-MM-DD 格式。") from exc


def validate_split_ranges(
    clean_df: pd.DataFrame,
    train_start: str,
    train_end: str,
    valid_start: str,
    valid_end: str,
    test_start: str,
    test_end: str,
) -> dict[str, tuple[pd.Timestamp, pd.Timestamp]]:
    """校验 train/valid/test 时间范围是否合理。"""
    ranges = {
        "train": (
            parse_date_arg(train_start, "--train-start"),
            parse_date_arg(train_end, "--train-end"),
        ),
        "valid": (
            parse_date_arg(valid_start, "--valid-start"),
            parse_date_arg(valid_end, "--valid-end"),
        ),
        "test": (
            parse_date_arg(test_start, "--test-start"),
            parse_date_arg(test_end, "--test-end"),
        ),
    }

    for name, (start, end) in ranges.items():
        if start > end:
            raise ValueError(
                f"{name} 时间范围不合法：开始日期 {start.date()} 晚于结束日期 {end.date()}。"
            )

    if ranges["train"][1] >= ranges["valid"][0]:
        raise ValueError(
            "train/valid 时间范围不合法："
            f"train 结束日期 {ranges['train'][1].date()} 必须早于 valid 开始日期 {ranges['valid'][0].date()}。"
        )
    if ranges["valid"][1] >= ranges["test"][0]:
        raise ValueError(
            "valid/test 时间范围不合法："
            f"valid 结束日期 {ranges['valid'][1].date()} 必须早于 test 开始日期 {ranges['test'][0].date()}。"
        )

    data_min = clean_df.index.get_level_values("datetime").min()
    data_max = clean_df.index.get_level_values("datetime").max()
    for name, (start, end) in ranges.items():
        if end < data_min or start > data_max:
            raise ValueError(
                f"{name} 时间范围 {start.date()} ~ {end.date()} 不在清洗后数据范围内。"
                f"当前可用数据范围为 {data_min.date()} ~ {data_max.date()}，请重新输入。"
            )

    return ranges


def filter_by_membership(clean_df: pd.DataFrame, membership_df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """只保留当天处于成员资格区间内的股票样本。"""
    dt = clean_df.index.get_level_values("datetime")#取每行日期
    ins = clean_df.index.get_level_values("instrument")#取每行股票
    in_universe = pd.Series(False, index=clean_df.index)#全False

    for stock, spans in membership_df.groupby("instrument", sort=False):#股票，成员资格区间
        for row in spans.itertuples(index=False):#逐个区间
            in_universe |= (
                (ins == stock)
                & (dt >= row.start_time)
                & (dt <= row.end_time)
            )

    before = len(clean_df)
    filtered = clean_df[in_universe]#只包含“该股票在该日期属于目标股票池”的记录。
    return filtered, before - len(filtered)


def preprocess_factors(
    factor_df: pd.DataFrame,
    membership_df: pd.DataFrame,
    factor_columns: list[str],
    winsor_n_sigma: float,
    train_start: str,
    train_end: str,
    valid_start: str,
    valid_end: str,
    test_start: str,
    test_end: str,
) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    """按动态股票池做截面处理，并严格分离打分样本与有标签样本。"""
    if winsor_n_sigma <= 0:
        raise ValueError("--winsor-n-sigma 必须大于 0。")
    if not factor_columns:
        raise ValueError("factor_columns 不能为空。")

    # rolling 因子可利用入选前历史预热，但每日截面统计只能包含当日真实成员。
    # 因此成员资格过滤必须早于 MAD 和 Z-Score，避免非成分股改变截面分布。
    universe_df, dropped_by_membership = filter_by_membership(factor_df, membership_df)
    if universe_df.empty:
        raise ValueError("成员资格过滤后没有剩余样本，请检查 instrument 命名和成员资格日期区间。")

    processed = universe_df.copy()
    processed[factor_columns] = (# 截面处理：比较同一天不同股票，而不是比较一只股票不同日期。
        processed.groupby(level="datetime")[factor_columns].transform(
            lambda s: winsorize_cs(s, n_sigma=winsor_n_sigma)# 对每天的每个因子分别调用 winsorize_cs()。
        )
    )
    processed[factor_columns] = (
        processed.groupby(level="datetime")[factor_columns].transform(zscore_cs)
    )

    nan_rows_before = int(factor_df.isna().any(axis=1).sum())
    # 生成信号时未来 LABEL 尚不可知，绝不能用 LABEL 是否缺失筛选候选股票。
    scoring_df = processed.dropna(subset=factor_columns)
    if scoring_df.empty:
        raise ValueError("因子列去除缺失值后没有可打分样本，请检查因子计算和数据覆盖。")
    # 训练与 IC 才需要正确答案；它是 scoring_df 的严格子集。
    labeled_df = scoring_df.dropna(subset=[LABEL_COLUMN])
    if labeled_df.empty:
        raise ValueError("没有同时具备因子和 LABEL 的训练/评估样本。")

    split_ranges = validate_split_ranges(
        clean_df=scoring_df,
        train_start=train_start,
        train_end=train_end,
        valid_start=valid_start,
        valid_end=valid_end,
        test_start=test_start,
        test_end=test_end,
    )
    splits = {
        "train": segment(labeled_df, str(split_ranges["train"][0].date()), str(split_ranges["train"][1].date())),
        "valid": segment(scoring_df, str(split_ranges["valid"][0].date()), str(split_ranges["valid"][1].date())),
        "test": segment(scoring_df, str(split_ranges["test"][0].date()), str(split_ranges["test"][1].date())),
    }

    warnings = []
    for name, split_df in splits.items():
        if split_df.empty:
            start, end = split_ranges[name]
            raise ValueError(
                f"{name} 切分结果为空：{start.date()} ~ {end.date()}。"
                "该区间虽然与清洗后数据日期范围有交集，但没有可用样本，请重新输入。"
            )

    diagnostics = {
        "说明": "处理顺序为动态成员过滤、每日截面 MAD、Z-Score；打分只要求因子非空，训练再要求 LABEL 非空。",
        "factor_columns": factor_columns,
        "label_column": LABEL_COLUMN,
        "input_shape": list(factor_df.shape),
        "input_nan_rows": nan_rows_before,
        "dropped_by_membership": int(dropped_by_membership),
        "scoring_shape": list(scoring_df.shape),
        "labeled_shape": list(labeled_df.shape),
        "factor_nan_rows_dropped": int(len(processed) - len(scoring_df)),
        "scoring_rows_with_missing_label": int(scoring_df[LABEL_COLUMN].isna().sum()),
        "scoring_date_min": str(scoring_df.index.get_level_values("datetime").min().date()),
        "scoring_date_max": str(scoring_df.index.get_level_values("datetime").max().date()),
        "scoring_n_days": int(scoring_df.index.get_level_values("datetime").nunique()),
        "scoring_n_instruments": int(scoring_df.index.get_level_values("instrument").nunique()),
        "winsor_n_sigma": float(winsor_n_sigma),
        "membership_rows": int(len(membership_df)),
        "membership_instruments": int(membership_df["instrument"].nunique()),
        "splits": {
            name: {
                "start": start,
                "end": end,
                "shape": list(split_df.shape),
                "n_days": int(split_df.index.get_level_values("datetime").nunique()) if not split_df.empty else 0,
                "n_instruments": int(split_df.index.get_level_values("instrument").nunique()) if not split_df.empty else 0,
            }
            for name, split_df, start, end in [
                (
                    "train",
                    splits["train"],
                    str(split_ranges["train"][0].date()),
                    str(split_ranges["train"][1].date()),
                ),
                (
                    "valid",
                    splits["valid"],
                    str(split_ranges["valid"][0].date()),
                    str(split_ranges["valid"][1].date()),
                ),
                (
                    "test",
                    splits["test"],
                    str(split_ranges["test"][0].date()),
                    str(split_ranges["test"][1].date()),
                ),
            ]
        },
        "warnings": warnings,
    }
    datasets = {"scoring": scoring_df, "labeled": labeled_df, **splits}
    return datasets, diagnostics


def build_output_prefix(factor_csv: str | Path, output_prefix: str | None) -> str:
    prefix = output_prefix or f"preprocessed_{Path(factor_csv).name}"
    if prefix.endswith(".csv.gz"):
        prefix = prefix[:-7]
    elif prefix.endswith(".csv"):
        prefix = prefix[:-4]

    unsafe_chars = set('<>:"/\\|?*')
    if any(ch in unsafe_chars for ch in prefix):
        raise ValueError(
            f"输出文件名前缀包含不适合作为文件名的字符：{prefix}\n"
            "请通过 --output-prefix 传入只包含字母、数字、下划线或短横线的名称。"
        )
    return prefix


def save_outputs(
    datasets: dict[str, pd.DataFrame],
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

    scoring_csv_gz = out_dir / f"{output_prefix}_scoring.csv.gz"
    labeled_csv_gz = out_dir / f"{output_prefix}_labeled.csv.gz"
    train_csv_gz = out_dir / f"{output_prefix}_train.csv.gz"
    valid_csv_gz = out_dir / f"{output_prefix}_valid.csv.gz"
    test_csv_gz = out_dir / f"{output_prefix}_test.csv.gz"
    preview_csv = out_dir / f"{output_prefix}_preview.csv"
    diagnostics_json = out_dir / f"{output_prefix}_diagnostics.json"

    datasets["scoring"].to_csv(scoring_csv_gz, compression="gzip")
    datasets["labeled"].to_csv(labeled_csv_gz, compression="gzip")
    datasets["train"].to_csv(train_csv_gz, compression="gzip")
    datasets["valid"].to_csv(valid_csv_gz, compression="gzip")
    datasets["test"].to_csv(test_csv_gz, compression="gzip")
    datasets["scoring"].head(preview_rows).to_csv(preview_csv)
    diagnostics_json.write_text(
        json.dumps(diagnostics, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return {
        "scoring_csv_gz": str(scoring_csv_gz),
        "labeled_csv_gz": str(labeled_csv_gz),
        "train_csv_gz": str(train_csv_gz),
        "valid_csv_gz": str(valid_csv_gz),
        "test_csv_gz": str(test_csv_gz),
        "preview_csv": str(preview_csv),
        "diagnostics_json": str(diagnostics_json),
    }


def print_preprocessing_summary(diagnostics: dict[str, Any]) -> None:
    print("\n" + "=" * 65)
    print("  因子预处理（每日截面：去极值 → Z-Score）")
    print("=" * 65)
    print("为什么要截面标准化？")
    print("  不同因子量纲不同（动量≈0.1，量比≈1~3），不能直接比较或加权。")
    print("  截面操作：对同一天所有股票的因子值统一处理，消除日间差异。\n")

    input_shape = tuple(diagnostics["input_shape"])
    scoring_shape = tuple(diagnostics["scoring_shape"])
    labeled_shape = tuple(diagnostics["labeled_shape"])
    print(
        f"处理前  shape = {input_shape}  "
        f"NaN 行数 = {diagnostics['input_nan_rows']}"
    )
    print(
        f"可打分  shape = {scoring_shape}  只要求因子非空\n"
        f"可训练  shape = {labeled_shape}  在可打分数据上再要求 LABEL 非空"
    )
    print(f"  保留 {diagnostics['scoring_rows_with_missing_label']} 行因子可用但未来标签缺失的样本")
    print(f"  可打分日期: {diagnostics['scoring_date_min']} ~ {diagnostics['scoring_date_max']}")

    print("\n数据三段切分:")
    for name in ["train", "valid", "test"]:
        item = diagnostics["splits"][name]
        shape = tuple(item["shape"])
        print(
            f"  {name} ({item['start']} ~ {item['end']})  "
            f"shape={shape}  → {item['n_days']} 交易日 × ~{item['n_instruments']} 股票/日"
        )

    warnings = diagnostics.get("warnings", [])
    if warnings:
        print("\n提醒:")
        for item in warnings:
            print(f"  - {item}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="对因子长表执行每日截面去极值、标准化、成员资格过滤和时间切分。",
    )
    parser.add_argument(
        "--factor-csv",
        default=DEFAULT_FACTOR_CSV,
        help=f"因子长表 CSV/CSV.GZ 路径。默认：{DEFAULT_FACTOR_CSV}",
    )
    parser.add_argument(
        "--membership-csv",
        default=DEFAULT_MEMBERSHIP_CSV,
        help=f"股票池成员资格 CSV 路径。默认：{DEFAULT_MEMBERSHIP_CSV}",
    )
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="输出目录。")
    parser.add_argument("--output-prefix", default=None, help="输出文件名前缀；不传则根据 --factor-csv 文件名自动生成。")
    parser.add_argument(
        "--factor-cols",
        default=None,
        help="可选，逗号分隔的因子列名；不传时自动使用除 datetime、instrument、LABEL 外的所有列。",
    )
    parser.add_argument("--winsor-n-sigma", type=float, default=3.0, help="MAD 去极值倍数，默认 3。")
    parser.add_argument("--train-start", default=DEFAULT_TRAIN_START, help="训练集开始日期。")
    parser.add_argument("--train-end", default=DEFAULT_TRAIN_END, help="训练集结束日期。")
    parser.add_argument("--valid-start", default=DEFAULT_VALID_START, help="验证集开始日期。")
    parser.add_argument("--valid-end", default=DEFAULT_VALID_END, help="验证集结束日期。")
    parser.add_argument("--test-start", default=DEFAULT_TEST_START, help="测试集开始日期。")
    parser.add_argument("--test-end", default=DEFAULT_TEST_END, help="测试集结束日期。")
    parser.add_argument("--preview-rows", type=int, default=20, help="预览 CSV 保存的行数。")
    parser.add_argument("--debug", action="store_true", help="出错时打印完整 Python traceback，便于开发调试。")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        output_prefix = build_output_prefix(args.factor_csv, args.output_prefix)
        explicit_factor_cols = parse_factor_columns(args.factor_cols)
        factor_df, factor_columns = load_factor_table(args.factor_csv, explicit_factor_cols)
        membership_df = load_membership_table(args.membership_csv)
        datasets, diagnostics = preprocess_factors(
            factor_df=factor_df,
            membership_df=membership_df,
            factor_columns=factor_columns,
            winsor_n_sigma=args.winsor_n_sigma,
            train_start=args.train_start,
            train_end=args.train_end,
            valid_start=args.valid_start,
            valid_end=args.valid_end,
            test_start=args.test_start,
            test_end=args.test_end,
        )
        output_paths = save_outputs(
            datasets=datasets,
            diagnostics=diagnostics,
            output_dir=args.output_dir,
            output_prefix=output_prefix,
            preview_rows=args.preview_rows,
        )
    except Exception as exc:
        print("\n[错误] 因子预处理失败。", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        print("\n建议排查：", file=sys.stderr)
        print("  1. 确认 --factor-csv 指向上游因子计算脚本输出的 CSV 或 CSV.GZ。", file=sys.stderr)
        print("  2. 确认因子表包含 datetime、instrument、LABEL，以及至少一个因子列。", file=sys.stderr)
        print("  3. 确认 --membership-csv 包含 instrument、start_time、end_time。", file=sys.stderr)
        print("  4. 确认因子表和成员资格表中的 instrument 命名一致。", file=sys.stderr)
        print("  5. 确认 train/valid/test 日期范围合法、按时间递增，且落在清洗后数据范围内。", file=sys.stderr)
        print("  6. 确认 --output-dir 可创建、可写入。", file=sys.stderr)
        print("  7. 如需调试细节，请追加 --debug 查看完整 traceback。", file=sys.stderr)
        if args.debug:
            print("\n完整 traceback:", file=sys.stderr)
            traceback.print_exc()
        sys.exit(1)

    print_preprocessing_summary(diagnostics)

    print("\n脚本输出文件:")
    for name, path in output_paths.items():
        print(f"  {name}: {path}")


if __name__ == "__main__":
    main()
