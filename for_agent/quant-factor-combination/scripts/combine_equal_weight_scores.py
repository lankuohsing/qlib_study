"""使用候选因子等权平均生成综合得分。"""

from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from factor_combination_common import (  # noqa: E402
    DEFAULT_LABEL_COLUMN,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_SELECTED_FACTORS_JSON,
    DEFAULT_TEST_CSV,
    DEFAULT_TRAIN_CSV,
    DEFAULT_VALID_CSV,
    build_output_prefix,
    compute_daily_score_ic,
    evaluate_method_ic,
    load_factor_directions,
    load_selected_factors,
    load_splits,
    parse_csv_list,
    print_paths,
    resolve_factor_columns,
    save_method_outputs,
)


METHOD = "equal_weight"


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


def resolve_directions(splits, factor_columns, selected_json, label_col, min_samples):
    """只用训练期证据确定方向；绝不从 valid/test 反推。"""
    saved = load_factor_directions(selected_json)
    directions = {}
    sources = {}
    for factor in factor_columns:
        if factor in saved:
            directions[factor] = saved[factor]
            sources[factor] = "selected_factors_json"
            continue
        daily_ic = compute_daily_score_ic(
            splits["train"][factor], splits["train"], label_col, min_samples
        )
        if daily_ic.empty or pd.isna(daily_ic.mean()):
            raise ValueError(f"因子 {factor} 无法从训练期计算方向。")
        directions[factor] = 1.0 if daily_ic.mean() >= 0 else -1.0
        sources[factor] = "train_ic_recomputed"
    return pd.Series(directions, dtype=float), sources


def score_equal_weight(splits, factor_columns, directions):
    return {
        split: df[factor_columns].mul(directions, axis="columns").mean(axis=1)
        for split, df in splits.items()
    }


def run_equal_weight_combination(args: argparse.Namespace) -> tuple[dict, object, dict]:
    explicit_factor_cols = parse_csv_list(args.factor_cols, "--factor-cols")
    selected_factors = load_selected_factors(args.selected_factors_json) if explicit_factor_cols is None else None
    splits = load_splits(args.train_csv, args.valid_csv, args.test_csv, args.label_col)
    factor_columns, factor_source = resolve_factor_columns(
        splits=splits,
        label_column=args.label_col,
        explicit_factor_cols=explicit_factor_cols,
        selected_factors=selected_factors,
    )

    directions, direction_sources = resolve_directions(
        splits, factor_columns, args.selected_factors_json, args.label_col, args.min_samples
    )
    scores = score_equal_weight(splits, factor_columns, directions)
    ic_summary, daily_ic = evaluate_method_ic(
        method=METHOD,
        scores=scores,
        splits=splits,
        label_column=args.label_col,
        min_samples=args.min_samples,
    )
    diagnostics = {
        "说明": "按训练期 IC 方向统一因子含义后等权平均；方向固定应用于 train/valid/test。",
        "method": METHOD,
        "train_csv": str(args.train_csv),
        "valid_csv": str(args.valid_csv),
        "test_csv": str(args.test_csv),
        "selected_factors_json": str(args.selected_factors_json) if args.selected_factors_json else None,
        "factor_source": factor_source,
        "factor_columns": factor_columns,
        "factor_directions": {key: float(value) for key, value in directions.items()},
        "direction_sources": direction_sources,
        "label_column": args.label_col,
        "split_shapes": {name: list(df.shape) for name, df in splits.items()},
        "score_shapes": {name: [int(len(score))] for name, score in scores.items()},
        "min_samples": int(args.min_samples),
        "warnings": [],
    }
    paths = save_method_outputs(
        method=METHOD,
        scores=scores,
        ic_summary=ic_summary,
        daily_ic=daily_ic,
        diagnostics=diagnostics,
        output_dir=args.output_dir,
        output_prefix=build_output_prefix(args.output_prefix),
        preview_rows=args.preview_rows,
        score_column="equal_weight_score",
        extra_frames={"factor_directions": directions.rename("direction").to_frame()},
    )
    return paths, ic_summary, diagnostics


def print_summary(ic_summary, diagnostics):
    print("\n" + "=" * 65)
    print("  因子合成（等权平均）")
    print("=" * 65)
    print("参与合成的因子:")
    print(f"  {diagnostics['factor_columns']}")
    print("训练期 IC 决定的因子方向:")
    for factor, direction in diagnostics["factor_directions"].items():
        print(f"  {factor:<16} {direction:+g}")
    print("\n合成得分 shape:")
    for split, shape in diagnostics["score_shapes"].items():
        print(f"  {split}: {tuple(shape)}")
    train_ic = ic_summary.loc[(METHOD, "train")]
    print(f"\n等权合成 train 段  IC均值={train_ic['IC均值']:.4f}  ICIR={train_ic['ICIR']:.4f}")
    print("\n各样本段合成得分 IC:")
    print(ic_summary.to_string())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="对候选因子执行等权平均合成。")
    parser.add_argument("--train-csv", default=DEFAULT_TRAIN_CSV, help=f"训练集样本 CSV/CSV.GZ 路径。默认：{DEFAULT_TRAIN_CSV}")
    parser.add_argument("--valid-csv", default=DEFAULT_VALID_CSV, help=f"验证集样本 CSV/CSV.GZ 路径。默认：{DEFAULT_VALID_CSV}")
    parser.add_argument("--test-csv", default=DEFAULT_TEST_CSV, help=f"测试集样本 CSV/CSV.GZ 路径。默认：{DEFAULT_TEST_CSV}")
    parser.add_argument("--selected-factors-json", default=DEFAULT_SELECTED_FACTORS_JSON, help=f"IC 分析阶段输出的候选因子 JSON。默认：{DEFAULT_SELECTED_FACTORS_JSON}")
    parser.add_argument("--factor-cols", default=None, help="可选，逗号分隔的因子列名；优先级高于 --selected-factors-json。")
    parser.add_argument("--label-col", default=DEFAULT_LABEL_COLUMN, help="标签列名，默认 LABEL。")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="输出目录。")
    parser.add_argument("--output-prefix", default=None, help="输出文件名前缀；不传则使用项目示例默认前缀。")
    parser.add_argument("--min-samples", type=int, default=10, help="每日截面计算合成得分 IC 所需最小样本数，默认 10。")
    parser.add_argument("--preview-rows", type=int, default=20, help="预览 CSV 保存的行数。")
    parser.add_argument("--debug", action="store_true", help="出错时打印完整 Python traceback，便于开发调试。")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        paths, ic_summary, diagnostics = run_equal_weight_combination(args)
    except Exception as exc:
        print("\n[错误] 等权因子合成失败。", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        print("\n建议排查：", file=sys.stderr)
        print("  1. 确认 --train-csv、--valid-csv、--test-csv 指向因子预处理阶段输出的三段样本。", file=sys.stderr)
        print("  2. 确认 --selected-factors-json 来自 IC 分析阶段，且包含 selected_factors。", file=sys.stderr)
        print("  3. 如使用 --factor-cols，确认指定列名在三段样本中都存在。", file=sys.stderr)
        print("  4. 确认 --output-dir 可创建、可写入。", file=sys.stderr)
        print("  5. 如需调试细节，请追加 --debug 查看完整 traceback。", file=sys.stderr)
        if args.debug:
            print("\n完整 traceback:", file=sys.stderr)
            traceback.print_exc()
        sys.exit(1)

    print_summary(ic_summary, diagnostics)
    print_paths(paths)


if __name__ == "__main__":
    main()
