"""
基于合成得分执行 Top-K 组合构建和简化回测。

本脚本只依赖“得分文件”这一通用接口，不关心上游得分来自等权、线性回归、
IC 加权还是其他合成方法。

输入：
    - 一个或多个测试期合成得分 CSV/CSV.GZ，必须包含 datetime、instrument 和一个 score 列
    - 原始 OHLCV CSV，用于计算测试期个股日收益率

输出：
    - 每种得分方法的每日持仓
    - 每种得分方法的日收益、换手和净收益序列
    - 多方法净值曲线和基准净值
    - 绩效指标、预览 CSV、诊断 JSON
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


INDEX_COLUMNS = ["datetime", "instrument"]
REQUIRED_OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]
DEFAULT_RAW_CSV = "datasets/exported/raw_ohlcv_csi300_20140601_20200801.csv"
DEFAULT_SCORE_CSVS = ",".join(
    [
        (
            "equal_weight="
            "for_agent/results/factor_combination/"
            "factor_combination_preprocessed_price_volume_ohlcv_factors_raw_ohlcv_csi300_20140601_20200801"
            "_equal_weight_test_score.csv.gz"
        ),
        (
            "linear_regression="
            "for_agent/results/factor_combination/"
            "factor_combination_preprocessed_price_volume_ohlcv_factors_raw_ohlcv_csi300_20140601_20200801"
            "_linear_regression_test_score.csv.gz"
        ),
    ]
)
DEFAULT_OUTPUT_DIR = "for_agent/results/portfolio_backtest"
DEFAULT_OUTPUT_PREFIX = "topk_backtest_csi300_20190101_20200801"


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


def validate_output_dir(output_dir: str | Path) -> Path:
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
    return out_dir


def build_output_prefix(output_prefix: str | None) -> str:
    prefix = output_prefix or DEFAULT_OUTPUT_PREFIX
    unsafe_chars = set('<>:"/\\|?*')
    if any(ch in unsafe_chars for ch in prefix):
        raise ValueError(
            f"输出文件名前缀包含不适合作为文件名的字符：{prefix}\n"
            "请通过 --output-prefix 传入只包含字母、数字、下划线或短横线的名称。"
        )
    return prefix


def sanitize_method_name(name: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in name.strip())
    safe = safe.strip("_")
    if not safe:
        raise ValueError(f"无法从方法名生成安全文件名：{name}")
    return safe


def infer_method_name(path: Path) -> str:
    name = path.name
    for suffix in [".csv.gz", ".csv"]:
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    if name.endswith("_test_score"):
        name = name[: -len("_test_score")]
    known_tokens = ["equal_weight", "linear_regression", "ic_weighted", "ridge", "lasso"]
    for token in known_tokens:
        if token in name:
            return token
    return name


def parse_score_csvs(score_csvs: str) -> list[tuple[str, Path]]:
    items = [item.strip() for item in score_csvs.split(",") if item.strip()]
    if not items:
        raise ValueError("--score-csvs 不能为空。")

    parsed = []
    seen = set()
    for item in items:
        if "=" in item:
            method_name, path_text = item.split("=", 1)
            method = sanitize_method_name(method_name)
            path = validate_file(path_text, "--score-csvs")
        else:
            path = validate_file(item, "--score-csvs")
            method = sanitize_method_name(infer_method_name(path))
        if method in seen:
            raise ValueError(f"--score-csvs 中存在重复方法名：{method}")
        seen.add(method)
        parsed.append((method, path))
    return parsed


def load_raw_ohlcv(raw_csv: str | Path) -> pd.DataFrame:
    path = validate_file(raw_csv, "--raw-csv")
    raw_df = pd.read_csv(path)
    if raw_df.empty:
        raise ValueError(f"原始行情 CSV 为空：{path}")

    missing = [col for col in INDEX_COLUMNS + REQUIRED_OHLCV_COLUMNS if col not in raw_df.columns]
    if missing:
        raise ValueError(
            f"原始行情 CSV 缺少必要列：{missing}\n"
            f"输入至少需要包含：{INDEX_COLUMNS + REQUIRED_OHLCV_COLUMNS}"
        )

    raw_df["datetime"] = pd.to_datetime(raw_df["datetime"], errors="raise")
    raw_df[REQUIRED_OHLCV_COLUMNS] = raw_df[REQUIRED_OHLCV_COLUMNS].astype("float32")
    return raw_df.set_index(INDEX_COLUMNS).sort_index()[REQUIRED_OHLCV_COLUMNS]


def infer_score_column(columns: list[str], explicit_score_col: str | None) -> str:
    if explicit_score_col is not None:
        if explicit_score_col not in columns:
            raise ValueError(f"--score-col 指定的列不存在：{explicit_score_col}")
        return explicit_score_col

    excluded = set(INDEX_COLUMNS)
    candidates = [col for col in columns if col not in excluded]
    if not candidates:
        raise ValueError("得分文件缺少 score 列；至少需要 datetime、instrument 和一个得分列。")
    if len(candidates) == 1:
        return candidates[0]

    score_like = [col for col in candidates if col.lower().endswith("score") or "score" in col.lower()]
    if len(score_like) == 1:
        return score_like[0]

    raise ValueError(
        f"得分文件中存在多个候选得分列：{candidates}\n"
        "请通过 --score-col 显式指定要用于回测的列。"
    )


def load_score_series(score_csv: str | Path, score_col: str | None) -> tuple[pd.Series, str]:
    path = validate_file(score_csv, "--score-csvs")
    df = pd.read_csv(path)
    if df.empty:
        raise ValueError(f"得分文件为空：{path}")

    missing = [col for col in INDEX_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"得分文件缺少必要列：{missing}；至少需要 datetime、instrument 和 score 列。")

    score_column = infer_score_column(list(df.columns), score_col)
    df["datetime"] = pd.to_datetime(df["datetime"], errors="raise")
    score = df.set_index(INDEX_COLUMNS).sort_index()[score_column].dropna()
    score.name = "score"
    if score.empty:
        raise ValueError(f"得分列 {score_column} 没有可用非空值：{path}")
    return score, score_column


def build_test_return_wide(raw_df: pd.DataFrame, start: str | None, end: str | None) -> pd.DataFrame:
    close = raw_df["close"].unstack("instrument")
    if start is not None or end is not None:
        close = close.loc[start:end]
    # 显式复现 pandas pct_change 旧默认行为：先前向填充价格缺口，再计算收益率。
    return close.ffill().pct_change(fill_method=None)


def build_holdings(score: pd.Series, topk: int) -> dict[pd.Timestamp, list[str]]:
    if topk <= 0:
        raise ValueError("--topk 必须是正整数。")
    holdings = {}
    for date, grp in score.groupby(level="datetime", sort=True):
        top_stocks = grp.xs(date, level="datetime").nlargest(topk).index.tolist()
        holdings[date] = top_stocks
    if not holdings:
        raise ValueError("得分文件没有可用于构建持仓的日期。")
    return holdings


def run_backtest(score: pd.Series, test_return_wide: pd.DataFrame, topk: int, transaction_cost: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    if transaction_cost < 0:
        raise ValueError("--transaction-cost 不能为负数。")

    holdings = build_holdings(score, topk=topk)
    records = []
    holding_rows = []
    prev_set: set[str] = set()

    for date_t in sorted(holdings):
        curr = holdings[date_t]
        curr_set = set(curr)
        ret_stocks = list(prev_set) if prev_set else []

        if date_t in test_return_wide.index:
            valid_stocks = [stock for stock in ret_stocks if stock in test_return_wide.columns]
            day_rets = test_return_wide.loc[date_t, valid_stocks].dropna()
            gross_ret = float(day_rets.mean()) if len(day_rets) > 0 else 0.0
        else:
            gross_ret = 0.0

        turnover = len(curr_set.symmetric_difference(prev_set)) / (2 * topk) if prev_set else 1.0
        net_ret = gross_ret - turnover * transaction_cost
        records.append(
            {
                "date": date_t,
                "gross_ret": gross_ret,
                "turnover": float(turnover),
                "net_ret": float(net_ret),
            }
        )

        for rank, instrument in enumerate(curr, start=1):
            holding_rows.append({"date": date_t, "rank": rank, "instrument": instrument})
        prev_set = curr_set

    ret_df = pd.DataFrame(records).set_index("date")
    holdings_df = pd.DataFrame(holding_rows)
    return ret_df, holdings_df


def calc_performance(series: pd.Series) -> dict[str, float]:
    series = pd.to_numeric(series, errors="coerce").fillna(0)
    if series.empty:
        raise ValueError("收益序列为空，无法计算绩效。")
    cumulative = (1 + series).cumprod()
    n = len(series)
    ann_ret = float(cumulative.iloc[-1] ** (252 / n) - 1)
    ann_vol = float(series.std() * np.sqrt(252))
    sharpe = float(ann_ret / (ann_vol + 1e-9))
    max_dd = float(((cumulative - cumulative.cummax()) / cumulative.cummax()).min())
    cum_ret = float(cumulative.iloc[-1] - 1)
    return {
        "年化收益": ann_ret,
        "年化波动": ann_vol,
        "夏普比率": sharpe,
        "最大回撤": max_dd,
        "累计收益": cum_ret,
    }


def format_performance(metrics: dict[str, float]) -> dict[str, str]:
    return {
        "年化收益": f"{metrics['年化收益']:.2%}",
        "年化波动": f"{metrics['年化波动']:.2%}",
        "夏普比率": f"{metrics['夏普比率']:.2f}",
        "最大回撤": f"{metrics['最大回撤']:.2%}",
        "累计收益": f"{metrics['累计收益']:.2%}",
    }


def save_outputs(
    results: dict[str, dict[str, Any]],
    benchmark_ret: pd.Series,
    output_dir: str | Path,
    output_prefix: str,
    diagnostics: dict[str, Any],
    preview_rows: int,
) -> dict[str, str]:
    if preview_rows <= 0:
        raise ValueError("--preview-rows 必须是正整数。")
    out_dir = validate_output_dir(output_dir)
    paths: dict[str, str] = {}

    nav_df = pd.DataFrame(index=benchmark_ret.index)
    nav_df["benchmark_nav"] = (1 + benchmark_ret.fillna(0)).cumprod()
    preview_parts = []
    metrics_records = {}

    for method, result in results.items():
        ret_df = result["returns"]
        holdings_df = result["holdings"]
        ret_path = out_dir / f"{output_prefix}_{method}_daily_returns.csv"
        holdings_path = out_dir / f"{output_prefix}_{method}_holdings.csv.gz"

        ret_df.to_csv(ret_path, encoding="utf-8-sig")
        holdings_df.to_csv(holdings_path, index=False, compression="gzip")
        paths[f"{method}_daily_returns_csv"] = str(ret_path)
        paths[f"{method}_holdings_csv_gz"] = str(holdings_path)

        nav = (1 + ret_df["net_ret"]).cumprod()
        nav_df[f"{method}_nav"] = nav.reindex(nav_df.index).ffill()
        nav_df[f"{method}_excess"] = nav_df[f"{method}_nav"] / nav_df["benchmark_nav"]
        metrics_records[method] = result["metrics_raw"]

        preview = ret_df.head(preview_rows).copy()
        preview["method"] = method
        preview_parts.append(preview.reset_index())

    benchmark_metrics = calc_performance(benchmark_ret)
    metrics_records["benchmark"] = benchmark_metrics
    metrics_df = pd.DataFrame(metrics_records).T
    metrics_display_df = pd.DataFrame({name: format_performance(metrics) for name, metrics in metrics_records.items()}).T

    nav_path = out_dir / f"{output_prefix}_nav_curve.csv"
    metrics_csv = out_dir / f"{output_prefix}_backtest_metrics.csv"
    metrics_json = out_dir / f"{output_prefix}_backtest_metrics.json"
    preview_csv = out_dir / f"{output_prefix}_preview.csv"
    diagnostics_json = out_dir / f"{output_prefix}_diagnostics.json"

    nav_df.to_csv(nav_path, encoding="utf-8-sig")
    metrics_display_df.to_csv(metrics_csv, encoding="utf-8-sig")
    metrics_json.write_text(
        json.dumps(
            {
                "raw": metrics_df.to_dict(orient="index"),
                "display": metrics_display_df.to_dict(orient="index"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    pd.concat(preview_parts, ignore_index=True).to_csv(preview_csv, index=False, encoding="utf-8-sig")
    diagnostics_json.write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8")

    paths["nav_curve_csv"] = str(nav_path)
    paths["backtest_metrics_csv"] = str(metrics_csv)
    paths["backtest_metrics_json"] = str(metrics_json)
    paths["preview_csv"] = str(preview_csv)
    paths["diagnostics_json"] = str(diagnostics_json)
    return paths


def print_summary(results: dict[str, dict[str, Any]], benchmark_metrics: dict[str, float], start: str, end: str) -> None:
    print("\n" + "=" * 65)
    print("  组合构建 + 回测（Top-K 等权持有）")
    print("=" * 65)
    print("回测逻辑：T 日得分 → 选出 Top-K 持仓 → T+1 日等权持有 → 扣手续费")

    for method, result in results.items():
        ret_df = result["returns"]
        avg_turnover = ret_df["turnover"].mean()
        print(f"\n方法: {method}")
        print(f"  策略日收益序列 shape={ret_df.shape}")
        print(f"  平均换手率: {avg_turnover:.1%}")
        print(ret_df.head(5).round(5).to_string())

    print(f"\n绩效对比（test 期间 {start} ~ {end}）")
    print(f"{'─' * 72}")
    method_names = list(results.keys()) + ["benchmark"]
    display = {name: format_performance(results[name]["metrics_raw"]) for name in results}
    display["benchmark"] = format_performance(benchmark_metrics)
    header = "  " + f"{'指标':<10}" + "".join(f"{name:>18}" for name in method_names)
    print(header)
    print(f"{'─' * 72}")
    for metric in ["年化收益", "年化波动", "夏普比率", "最大回撤", "累计收益"]:
        row = "  " + f"{metric:<10}" + "".join(f"{display[name][metric]:>18}" for name in method_names)
        print(row)
    print(f"{'─' * 72}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="基于任意合成得分文件执行 Top-K 组合构建和简化回测。")
    parser.add_argument("--score-csvs", default=DEFAULT_SCORE_CSVS, help="逗号分隔的得分文件。支持 path 或 method=path；默认读取因子合成阶段的 equal_weight 和 linear_regression 测试集得分。")
    parser.add_argument("--raw-csv", default=DEFAULT_RAW_CSV, help=f"原始 OHLCV CSV 路径。默认：{DEFAULT_RAW_CSV}")
    parser.add_argument("--score-col", default=None, help="得分列名；不传时自动识别唯一 score 列。")
    parser.add_argument("--topk", type=int, default=30, help="每日持仓股票数，默认 30。")
    parser.add_argument("--transaction-cost", type=float, default=0.001, help="单边换手成本，默认 0.001。")
    parser.add_argument("--test-start", default="2019-01-01", help="回测收益率开始日期。")
    parser.add_argument("--test-end", default="2020-08-01", help="回测收益率结束日期。")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="输出目录。")
    parser.add_argument("--output-prefix", default=None, help="输出文件名前缀；不传则使用项目示例默认前缀。")
    parser.add_argument("--preview-rows", type=int, default=20, help="预览 CSV 保存的行数。")
    parser.add_argument("--debug", action="store_true", help="出错时打印完整 Python traceback，便于开发调试。")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        if args.topk <= 0:
            raise ValueError("--topk 必须是正整数。")
        if args.transaction_cost < 0:
            raise ValueError("--transaction-cost 不能为负数。")
        score_specs = parse_score_csvs(args.score_csvs)
        output_prefix = build_output_prefix(args.output_prefix)
        raw_df = load_raw_ohlcv(args.raw_csv)
        test_return_wide = build_test_return_wide(raw_df, args.test_start, args.test_end)

        results: dict[str, dict[str, Any]] = {}
        score_diagnostics = {}
        for method, score_path in score_specs:
            score, score_column = load_score_series(score_path, args.score_col)
            ret_df, holdings_df = run_backtest(
                score=score,
                test_return_wide=test_return_wide,
                topk=args.topk,
                transaction_cost=args.transaction_cost,
            )
            results[method] = {
                "returns": ret_df,
                "holdings": holdings_df,
                "metrics_raw": calc_performance(ret_df["net_ret"]),
            }
            score_diagnostics[method] = {
                "score_csv": str(score_path),
                "score_column": score_column,
                "score_shape": [int(len(score))],
                "score_n_days": int(score.index.get_level_values("datetime").nunique()),
                "score_n_instruments": int(score.index.get_level_values("instrument").nunique()),
                "return_shape": list(ret_df.shape),
                "holding_rows": int(len(holdings_df)),
            }

        first_method = next(iter(results))
        benchmark_index = results[first_method]["returns"].index
        benchmark_ret = test_return_wide.mean(axis=1).reindex(benchmark_index).fillna(0)
        benchmark_metrics = calc_performance(benchmark_ret)
        diagnostics = {
            "说明": "Top-K 组合构建和简化回测诊断。T 日得分选股，T+1 日等权持有并扣除换手成本。",
            "raw_csv": str(args.raw_csv),
            "score_methods": list(results.keys()),
            "topk": int(args.topk),
            "transaction_cost": float(args.transaction_cost),
            "test_start": args.test_start,
            "test_end": args.test_end,
            "test_return_wide_shape": list(test_return_wide.shape),
            "benchmark_method": "raw_close_equal_weight",
            "score_diagnostics": score_diagnostics,
            "warnings": [],
        }
        output_paths = save_outputs(
            results=results,
            benchmark_ret=benchmark_ret,
            output_dir=args.output_dir,
            output_prefix=output_prefix,
            diagnostics=diagnostics,
            preview_rows=args.preview_rows,
        )
    except Exception as exc:
        print("\n[错误] 组合构建与回测失败。", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        print("\n建议排查：", file=sys.stderr)
        print("  1. 确认 --score-csvs 指向因子合成阶段输出的测试集得分文件。", file=sys.stderr)
        print("  2. 确认得分文件包含 datetime、instrument 和一个 score 列；多列时用 --score-col 指定。", file=sys.stderr)
        print("  3. 确认 --raw-csv 包含 datetime、instrument、open/high/low/close/volume。", file=sys.stderr)
        print("  4. 确认 --topk 为正整数，--transaction-cost 非负。", file=sys.stderr)
        print("  5. 确认 --output-dir 可创建、可写入。", file=sys.stderr)
        print("  6. 如需调试细节，请追加 --debug 查看完整 traceback。", file=sys.stderr)
        if args.debug:
            print("\n完整 traceback:", file=sys.stderr)
            traceback.print_exc()
        sys.exit(1)

    print_summary(results, benchmark_metrics, args.test_start, args.test_end)
    print("\n脚本输出文件:")
    for name, path in output_paths.items():
        print(f"  {name}: {path}")


if __name__ == "__main__":
    main()
