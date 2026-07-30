"""使用 LinearRegression 学习因子权重并生成综合得分。"""

from __future__ import annotations

import argparse
import sys
import traceback
import warnings
from pathlib import Path

import pandas as pd
from sklearn.linear_model import LinearRegression

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
    evaluate_method_ic,
    load_selected_factors,
    load_splits,
    parse_csv_list,
    print_paths,
    resolve_factor_columns,
    save_method_outputs,
)

warnings.filterwarnings("ignore", message="X does not have valid feature names.*")

METHOD = "linear_regression"


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


def fit_linear_regression(train_df: pd.DataFrame, factor_columns: list[str], label_column: str) -> tuple[LinearRegression, pd.DataFrame]:
    train_xy = train_df[factor_columns + [label_column]].dropna()
    if train_xy.empty:
        raise ValueError("LinearRegression 训练样本为空；请检查因子列和 LABEL。")
    model = LinearRegression().fit(train_xy[factor_columns].values, train_xy[label_column].values)
    return model, train_xy


def score_linear_regression(splits: dict[str, pd.DataFrame], factor_columns: list[str], model: LinearRegression) -> dict[str, pd.Series]:
    scores = {}
    for split, df in splits.items():
        x = df[factor_columns].dropna()
        scores[split] = pd.Series(model.predict(x.values), index=x.index, name="linear_regression_score")
    return scores


def run_linear_regression_combination(args: argparse.Namespace) -> tuple[dict, object, pd.DataFrame, dict]:
    explicit_factor_cols = parse_csv_list(args.factor_cols, "--factor-cols")
    selected_factors = load_selected_factors(args.selected_factors_json) if explicit_factor_cols is None else None
    splits = load_splits(args.train_csv, args.valid_csv, args.test_csv, args.label_col)
    factor_columns, factor_source = resolve_factor_columns(
        splits=splits,
        label_column=args.label_col,
        explicit_factor_cols=explicit_factor_cols,
        selected_factors=selected_factors,
    )

    model, train_xy = fit_linear_regression(splits["train"], factor_columns, args.label_col)
    scores = score_linear_regression(splits, factor_columns, model)
    ic_summary, daily_ic = evaluate_method_ic(
        method=METHOD,
        scores=scores,
        splits=splits,
        label_column=args.label_col,
        min_samples=args.min_samples,
    )
    coefficients = pd.DataFrame({"factor": factor_columns, "coefficient": model.coef_}).set_index("factor")
    coefficient_records = coefficients.reset_index().to_dict(orient="records")
    diagnostics = {
        "说明": "LinearRegression 因子合成诊断。模型只在 train 样本上拟合，再对 train/valid/test 分别打分。",
        "method": METHOD,
        "train_csv": str(args.train_csv),
        "valid_csv": str(args.valid_csv),
        "test_csv": str(args.test_csv),
        "selected_factors_json": str(args.selected_factors_json) if args.selected_factors_json else None,
        "factor_source": factor_source,
        "factor_columns": factor_columns,
        "label_column": args.label_col,
        "split_shapes": {name: list(df.shape) for name, df in splits.items()},
        "score_shapes": {name: [int(len(score))] for name, score in scores.items()},
        "linear_regression_train_xy_shape": list(train_xy.shape),
        "linear_regression_intercept": float(model.intercept_),
        "linear_regression_train_r2": float(model.score(train_xy[factor_columns].values, train_xy[args.label_col].values)),
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
        score_column="linear_regression_score",
        extra_frames={"coefficients": coefficients},
        extra_json={"coefficients": coefficient_records},
    )
    return paths, ic_summary, coefficients, diagnostics


def print_summary(ic_summary, coefficients, diagnostics):
    print("\n" + "=" * 65)
    print("  因子合成（LinearRegression）")
    print("=" * 65)
    print("参与合成的因子:")
    print(f"  {diagnostics['factor_columns']}")
    train_xy_shape = tuple(diagnostics["linear_regression_train_xy_shape"])
    print(f"\n回归输入  X shape=({train_xy_shape[0]}, {len(diagnostics['factor_columns'])})（样本数 × 因子数）  y shape=({train_xy_shape[0]},)")
    print("学到的因子权重（回归系数）:")
    for fname, row in coefficients.iterrows():
        print(f"  {fname:<12} : {row['coefficient']:+.6f}")
    print(f"截距: {diagnostics['linear_regression_intercept']:.6f}")
    print(f"训练集 R²={diagnostics['linear_regression_train_r2']:.5f}")
    print("（量化信号 R² 通常 < 0.01，说明股票涨跌难以预测，属正常现象）")
    print("\n各样本段合成得分 IC:")
    print(ic_summary.to_string())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="使用 LinearRegression 学习因子权重并生成综合得分。")
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
        paths, ic_summary, coefficients, diagnostics = run_linear_regression_combination(args)
    except Exception as exc:
        print("\n[错误] LinearRegression 因子合成失败。", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        print("\n建议排查：", file=sys.stderr)
        print("  1. 确认 --train-csv、--valid-csv、--test-csv 指向因子预处理阶段输出的三段样本。", file=sys.stderr)
        print("  2. 确认 --selected-factors-json 来自 IC 分析阶段，且包含 selected_factors。", file=sys.stderr)
        print("  3. 如使用 --factor-cols，确认指定列名在三段样本中都存在。", file=sys.stderr)
        print("  4. 确认训练样本非空，且 LABEL 列可用于监督学习。", file=sys.stderr)
        print("  5. 确认 --output-dir 可创建、可写入。", file=sys.stderr)
        print("  6. 如需调试细节，请追加 --debug 查看完整 traceback。", file=sys.stderr)
        if args.debug:
            print("\n完整 traceback:", file=sys.stderr)
            traceback.print_exc()
        sys.exit(1)

    print_summary(ic_summary, coefficients, diagnostics)
    print_paths(paths)


if __name__ == "__main__":
    main()
