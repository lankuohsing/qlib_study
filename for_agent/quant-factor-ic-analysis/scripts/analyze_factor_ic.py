"""
对预处理后的因子样本计算 IC / ICIR。

本脚本承接因子预处理阶段的输出，评估每个因子与未来收益标签
LABEL 在每日截面上的 Spearman 相关性，并输出候选有效因子清单。

输入：
    预处理后的因子 CSV/CSV.GZ，必须包含 datetime、instrument、LABEL
    以及至少一个因子列。

输出：
    - 因子 IC/ICIR 汇总表（CSV）
    - 每日 IC 明细（CSV.GZ）
    - 因子相关矩阵（CSV）
    - 候选因子清单（JSON）
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

warnings.filterwarnings("ignore", message="An input array is constant.*")


INDEX_COLUMNS = ["datetime", "instrument"]
DEFAULT_LABEL_COLUMN = "LABEL"
DEFAULT_INPUT_CSV = (
    "for_agent/results/factor_preprocessing/"
    "preprocessed_price_volume_ohlcv_factors_raw_ohlcv_csi300_20140601_20200801_train.csv.gz"
)
DEFAULT_OUTPUT_DIR = "for_agent/results/factor_ic_analysis"


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


def infer_factor_columns(
    columns: list[str],
    label_column: str,
    explicit_factor_cols: list[str] | None,
) -> list[str]:
    """确定需要分析的因子列。默认使用除索引列和 LABEL 外的所有列。"""
    if explicit_factor_cols is not None:
        missing = [col for col in explicit_factor_cols if col not in columns]
        if missing:
            raise ValueError(f"--factor-cols 中包含输入 CSV 不存在的列：{missing}")
        return explicit_factor_cols

    excluded = set(INDEX_COLUMNS + [label_column])
    inferred = [col for col in columns if col not in excluded]
    if not inferred:
        raise ValueError(
            "未能从输入 CSV 自动识别因子列。\n"
            f"输入至少需要包含 {INDEX_COLUMNS + [label_column]}，以及至少一个因子列；"
            "也可以通过 --factor-cols 手动指定。"
        )
    return inferred


def load_factor_sample(
    input_csv: str | Path,
    label_column: str,
    explicit_factor_cols: list[str] | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    """读取预处理后的因子样本，并恢复为 (datetime, instrument) MultiIndex。"""
    input_path = validate_file(input_csv, "--input-csv")
    df = pd.read_csv(input_path)
    if df.empty:
        raise ValueError(f"输入 CSV 为空：{input_path}")

    required = INDEX_COLUMNS + [label_column]
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(
            f"输入 CSV 缺少必要列：{missing}\n"
            f"输入至少需要包含：{required}，以及至少一个因子列。"
        )

    factor_columns = infer_factor_columns(list(df.columns), label_column, explicit_factor_cols)
    df["datetime"] = pd.to_datetime(df["datetime"], errors="raise")
    df = df.set_index(INDEX_COLUMNS).sort_index()
    return df[factor_columns + [label_column]], factor_columns


def compute_daily_ic(
    df: pd.DataFrame,
    factor_columns: list[str],
    label_column: str,
    min_samples: int,
) -> tuple[pd.DataFrame, dict[str, int]]:
    """按交易日计算每个因子的截面 Spearman IC。"""
    if min_samples <= 0:
        raise ValueError("--min-samples 必须是正整数。")

    daily_records: list[dict[str, Any]] = []
    skipped_by_factor = {factor: 0 for factor in factor_columns}
    nan_ic_by_factor = {factor: 0 for factor in factor_columns}

    for date, grp in df.groupby(level="datetime", sort=True):
        row: dict[str, Any] = {"datetime": date}
        grp_flat = grp.xs(date, level="datetime")
        for factor in factor_columns:
            valid = grp_flat[[factor, label_column]].dropna()
            if len(valid) < min_samples:
                skipped_by_factor[factor] += 1
                row[factor] = pd.NA
                continue
            ic_val = valid[factor].corr(valid[label_column], method="spearman")
            if pd.isna(ic_val):
                nan_ic_by_factor[factor] += 1
                row[factor] = pd.NA
            else:
                row[factor] = float(ic_val)
        daily_records.append(row)

    daily_ic = pd.DataFrame(daily_records).set_index("datetime").sort_index()
    diagnostics = {
        "skipped_by_factor": skipped_by_factor,
        "nan_ic_by_factor": nan_ic_by_factor,
    }
    return daily_ic, diagnostics


def summarize_ic(
    daily_ic: pd.DataFrame,
    factor_columns: list[str],
    ic_mean_threshold: float,
) -> tuple[pd.DataFrame, list[str], bool]:
    """汇总 IC 统计，并按阈值筛选候选因子。"""
    if ic_mean_threshold < 0:
        raise ValueError("--ic-mean-threshold 不能为负数。")

    records: dict[str, dict[str, Any]] = {}
    for factor in factor_columns:
        s = pd.to_numeric(daily_ic[factor], errors="coerce").dropna()
        records[factor] = {
            "IC均值": round(float(s.mean()), 4) if len(s) else pd.NA,
            "IC标准差": round(float(s.std()), 4) if len(s) else pd.NA,
            "ICIR": round(float(s.mean() / (s.std() + 1e-9)), 4) if len(s) else pd.NA,
            "IC>0占比": round(float((s > 0).mean()), 3) if len(s) else pd.NA,
            "计算日数": int(len(s)),
        }

    ic_table = pd.DataFrame(records).T
    ic_table["计算日数"] = pd.to_numeric(ic_table["计算日数"], errors="coerce").fillna(0).astype(int)
    if int(ic_table["计算日数"].sum()) == 0:
        raise ValueError("没有任何因子能计算出有效 IC，请检查样本数量、因子列和 LABEL。")

    mean_abs = pd.to_numeric(ic_table["IC均值"], errors="coerce").abs()
    selected = ic_table.index[mean_abs > ic_mean_threshold].tolist()
    used_fallback = False
    if not selected:
        selected = list(factor_columns)
        used_fallback = True

    return ic_table, selected, used_fallback


def compute_factor_corr(df: pd.DataFrame, factor_columns: list[str]) -> pd.DataFrame:
    """计算因子样本相关矩阵，用于后续识别冗余因子。"""
    return df[factor_columns].corr(method="spearman")


def build_output_prefix(input_csv: str | Path, output_prefix: str | None) -> str:
    prefix = output_prefix or f"factor_ic_{Path(input_csv).name}"
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
    ic_table: pd.DataFrame,
    daily_ic: pd.DataFrame,
    factor_corr: pd.DataFrame,
    selected_factors: list[str],
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

    ic_analysis_csv = out_dir / f"{output_prefix}_ic_analysis.csv"
    daily_ic_csv_gz = out_dir / f"{output_prefix}_daily_ic.csv.gz"
    factor_corr_csv = out_dir / f"{output_prefix}_factor_corr.csv"
    selected_json = out_dir / f"{output_prefix}_selected_factors.json"
    preview_csv = out_dir / f"{output_prefix}_preview.csv"
    diagnostics_json = out_dir / f"{output_prefix}_diagnostics.json"

    ic_table.to_csv(ic_analysis_csv, encoding="utf-8-sig")
    daily_ic.to_csv(daily_ic_csv_gz, compression="gzip")
    factor_corr.to_csv(factor_corr_csv, encoding="utf-8-sig")
    selected_json.write_text(
        json.dumps(
            {
                "selected_factors": selected_factors,
                # 等权合成必须只依据训练期 IC 确定方向，并固定用于 valid/test。
                "factor_directions": {
                    factor: (1 if float(ic_table.loc[factor, "IC均值"]) >= 0 else -1)
                    for factor in selected_factors
                },
                "selected_factor_ic_mean": {
                    factor: float(ic_table.loc[factor, "IC均值"])
                    for factor in selected_factors
                },
                "ic_mean_threshold": diagnostics.get("ic_mean_threshold"),
                "used_fallback_all_factors": diagnostics.get("used_fallback_all_factors"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    ic_table.head(preview_rows).to_csv(preview_csv, encoding="utf-8-sig")
    diagnostics_json.write_text(
        json.dumps(diagnostics, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return {
        "ic_analysis_csv": str(ic_analysis_csv),
        "daily_ic_csv_gz": str(daily_ic_csv_gz),
        "factor_corr_csv": str(factor_corr_csv),
        "selected_factors_json": str(selected_json),
        "preview_csv": str(preview_csv),
        "diagnostics_json": str(diagnostics_json),
    }


def print_ic_summary(
    ic_table: pd.DataFrame,
    selected_factors: list[str],
    used_fallback: bool,
    diagnostics: dict[str, Any],
) -> None:
    print("\n" + "=" * 65)
    print("  因子有效性分析（IC / ICIR）")
    print("=" * 65)
    print("IC  = Spearman(因子值, T+1 开盘至 T+2 开盘收益率)，在每日截面计算，再对时序取统计")
    print("ICIR = IC均值 / IC标准差")
    print("经验阈值：|IC均值| > 0.03 认为有预测力；ICIR > 0.5 认为稳定\n")

    print(
        f"IC 分析结果（基于样本段 {diagnostics['n_days']} 个交易日，"
        f"{diagnostics['n_instruments']} 只股票）:"
    )
    print(ic_table.to_string())

    threshold = diagnostics["ic_mean_threshold"]
    if used_fallback:
        print(f"\n未筛选到显著有效因子（阈值 {threshold}），改为使用全部因子")
    else:
        print(f"\n筛选出有效因子（|IC均值| > {threshold}）: {selected_factors}")

    warnings = diagnostics.get("warnings", [])
    if warnings:
        print("\n提醒:")
        for item in warnings:
            print(f"  - {item}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="对预处理后的因子样本计算每日截面 IC、ICIR 并筛选候选因子。",
    )
    parser.add_argument(
        "--input-csv",
        default=DEFAULT_INPUT_CSV,
        help=f"预处理后的因子样本 CSV/CSV.GZ 路径。默认：{DEFAULT_INPUT_CSV}",
    )
    parser.add_argument(
        "--factor-cols",
        default=None,
        help="可选，逗号分隔的因子列名；不传时自动使用除 datetime、instrument、LABEL 外的所有列。",
    )
    parser.add_argument("--label-col", default=DEFAULT_LABEL_COLUMN, help="标签列名，默认 LABEL。")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="输出目录。")
    parser.add_argument("--output-prefix", default=None, help="输出文件名前缀；不传则根据 --input-csv 文件名自动生成。")
    parser.add_argument("--min-samples", type=int, default=10, help="每日截面计算 IC 所需最小样本数，默认 10。")
    parser.add_argument("--ic-mean-threshold", type=float, default=0.02, help="按 |IC均值| 筛选有效因子的阈值，默认 0.02。")
    parser.add_argument("--preview-rows", type=int, default=20, help="预览 CSV 保存的行数。")
    parser.add_argument("--debug", action="store_true", help="出错时打印完整 Python traceback，便于开发调试。")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        output_prefix = build_output_prefix(args.input_csv, args.output_prefix)
        explicit_factor_cols = parse_factor_columns(args.factor_cols)
        df, factor_columns = load_factor_sample(
            input_csv=args.input_csv,
            label_column=args.label_col,
            explicit_factor_cols=explicit_factor_cols,
        )
        daily_ic, ic_diagnostics = compute_daily_ic(
            df=df,
            factor_columns=factor_columns,
            label_column=args.label_col,
            min_samples=args.min_samples,
        )
        ic_table, selected_factors, used_fallback = summarize_ic(
            daily_ic=daily_ic,
            factor_columns=factor_columns,
            ic_mean_threshold=args.ic_mean_threshold,
        )
        factor_corr = compute_factor_corr(df, factor_columns)

        warnings = []
        low_coverage = [
            factor
            for factor in factor_columns
            if ic_table.loc[factor, "计算日数"] < df.index.get_level_values("datetime").nunique() * 0.8
        ]
        if low_coverage:
            warnings.append(f"以下因子有效 IC 计算日覆盖率低于 80%：{low_coverage}")
        if used_fallback:
            warnings.append("没有因子超过 IC 均值阈值，已回退为保留全部因子。")

        diagnostics = {
            "说明": "仅用训练期样本计算每日截面 IC；输出方向供等权合成固定应用到 valid/test。",
            "input_csv": str(args.input_csv),
            "input_shape": list(df.shape),
            "factor_columns": factor_columns,
            "label_column": args.label_col,
            "n_days": int(df.index.get_level_values("datetime").nunique()),
            "n_instruments": int(df.index.get_level_values("instrument").nunique()),
            "date_min": str(df.index.get_level_values("datetime").min().date()),
            "date_max": str(df.index.get_level_values("datetime").max().date()),
            "min_samples": int(args.min_samples),
            "ic_mean_threshold": float(args.ic_mean_threshold),
            "selected_factors": selected_factors,
            "used_fallback_all_factors": bool(used_fallback),
            **ic_diagnostics,
            "warnings": warnings,
        }

        output_paths = save_outputs(
            ic_table=ic_table,
            daily_ic=daily_ic,
            factor_corr=factor_corr,
            selected_factors=selected_factors,
            diagnostics=diagnostics,
            output_dir=args.output_dir,
            output_prefix=output_prefix,
            preview_rows=args.preview_rows,
        )
    except Exception as exc:
        print("\n[错误] 因子有效性分析失败。", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        print("\n建议排查：", file=sys.stderr)
        print("  1. 确认 --input-csv 指向预处理阶段输出的 train/valid/test CSV 或 CSV.GZ。", file=sys.stderr)
        print("  2. 确认输入表包含 datetime、instrument、LABEL，以及至少一个因子列。", file=sys.stderr)
        print("  3. 如使用 --factor-cols，确认指定列名都存在于输入 CSV。", file=sys.stderr)
        print("  4. 确认每日截面样本数不少于 --min-samples。", file=sys.stderr)
        print("  5. 确认 --output-dir 可创建、可写入。", file=sys.stderr)
        print("  6. 如需调试细节，请追加 --debug 查看完整 traceback。", file=sys.stderr)
        if args.debug:
            print("\n完整 traceback:", file=sys.stderr)
            traceback.print_exc()
        sys.exit(1)

    print_ic_summary(
        ic_table=ic_table,
        selected_factors=selected_factors,
        used_fallback=used_fallback,
        diagnostics=diagnostics,
    )

    print("\n脚本输出文件:")
    for name, path in output_paths.items():
        print(f"  {name}: {path}")


if __name__ == "__main__":
    main()
