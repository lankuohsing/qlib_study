"""按 T 日信号、T+1 开盘成交、T+2 开盘结算执行状态化 Top-K 回测。"""

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
RAW_COLUMNS = ["open", "high", "low", "close", "volume"]
DEFAULT_RAW_CSV = "datasets/exported/raw_ohlcv_csi300_20140601_20200801.csv"
DEFAULT_MEMBERSHIP_CSV = "datasets/exported/raw_ohlcv_csi300_20140601_20200801_membership.csv"
DEFAULT_SCORE_CSVS = ",".join([
    "equal_weight=for_agent/results/factor_combination/factor_combination_preprocessed_price_volume_ohlcv_factors_raw_ohlcv_csi300_20140601_20200801_equal_weight_test_score.csv.gz",
    "linear_regression=for_agent/results/factor_combination/factor_combination_preprocessed_price_volume_ohlcv_factors_raw_ohlcv_csi300_20140601_20200801_linear_regression_test_score.csv.gz",
])
DEFAULT_OUTPUT_DIR = "for_agent/results/portfolio_backtest"
DEFAULT_OUTPUT_PREFIX = "stateful_topk_backtest_csi300_20180101_20190601"


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


def validate_file(value: str | Path, arg_name: str) -> Path:
    path = Path(value)
    if not path.exists():
        raise FileNotFoundError(f"找不到 {arg_name} 指向的文件：{path}")
    if not path.is_file():
        raise ValueError(f"{arg_name} 指向的路径不是文件：{path}")
    return path


def validate_output_dir(value: str | Path) -> Path:
    path = Path(value)
    path.mkdir(parents=True, exist_ok=True)
    if not path.is_dir():
        raise ValueError(f"输出路径不是目录：{path}")
    return path


def build_output_prefix(value: str | None) -> str:
    prefix = value or DEFAULT_OUTPUT_PREFIX
    if any(ch in set('<>:"/\\|?*') for ch in prefix):
        raise ValueError("--output-prefix 包含不适合作为文件名的字符。")
    return prefix


def sanitize_method_name(name: str) -> str:
    cleaned = name.strip().replace("-", "_").replace(" ", "_")
    if not cleaned or not all(ch.isalnum() or ch == "_" for ch in cleaned):
        raise ValueError(f"方法名只能包含字母、数字、下划线或短横线：{name}")
    return cleaned


def infer_method_name(path: Path) -> str:
    stem = path.name[:-7] if path.name.endswith(".csv.gz") else path.stem
    for suffix in ["_test_score", "_score"]:
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
    return sanitize_method_name(stem)


def parse_score_csvs(value: str) -> list[tuple[str, Path]]:
    items = [item.strip() for item in value.split(",") if item.strip()]
    if not items:
        raise ValueError("--score-csvs 不能为空。")
    parsed = []
    seen = set()
    for item in items:
        if "=" in item:
            method, path_text = item.split("=", 1)
            method = sanitize_method_name(method)
        else:
            path_text = item
            method = infer_method_name(Path(path_text))
        path = validate_file(path_text, "--score-csvs")
        if method in seen:
            raise ValueError(f"--score-csvs 中的方法名重复：{method}")
        seen.add(method)
        parsed.append((method, path))
    return parsed


def load_raw_ohlcv(value: str | Path) -> pd.DataFrame:
    path = validate_file(value, "--raw-csv")
    df = pd.read_csv(path)
    required = INDEX_COLUMNS + RAW_COLUMNS
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(f"原始行情缺少必要列：{missing}")
    if df.empty:
        raise ValueError(f"原始行情为空：{path}")
    df["datetime"] = pd.to_datetime(df["datetime"], errors="raise")
    if df.duplicated(INDEX_COLUMNS).any():
        raise ValueError("原始行情存在重复的 datetime/instrument。")
    return df.set_index(INDEX_COLUMNS).sort_index()[RAW_COLUMNS]


def load_membership(value: str | Path) -> pd.DataFrame:
    path = validate_file(value, "--membership-csv")
    df = pd.read_csv(path)
    required = ["instrument", "start_time", "end_time"]
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(f"成员资格表缺少必要列：{missing}")
    df["start_time"] = pd.to_datetime(df["start_time"], errors="raise")
    df["end_time"] = pd.to_datetime(df["end_time"], errors="raise")
    if (df["start_time"] > df["end_time"]).any():
        raise ValueError("成员资格表存在 start_time 晚于 end_time 的记录。")
    return df


def build_membership_by_date(membership: pd.DataFrame, dates: pd.DatetimeIndex) -> dict[pd.Timestamp, set[str]]:
    dates = pd.DatetimeIndex(dates).sort_values().unique()
    result = {date: set() for date in dates}
    for row in membership.itertuples(index=False):
        active = dates[(dates >= row.start_time) & (dates <= row.end_time)]
        for date in active:
            result[date].add(row.instrument)
    return result


def load_score_series(value: str | Path, score_col: str | None) -> tuple[pd.Series, str]:
    path = validate_file(value, "--score-csvs")
    df = pd.read_csv(path)
    missing = [col for col in INDEX_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"得分文件 {path} 缺少索引列：{missing}")
    candidates = [col for col in df.columns if col not in INDEX_COLUMNS]
    if score_col is not None:
        if score_col not in candidates:
            raise ValueError(f"得分文件 {path} 不含 --score-col={score_col}")
        chosen = score_col
    elif len(candidates) == 1:
        chosen = candidates[0]
    else:
        raise ValueError(f"得分文件 {path} 有多个候选列 {candidates}；请用 --score-col 指定。")
    df["datetime"] = pd.to_datetime(df["datetime"], errors="raise")
    if df.duplicated(INDEX_COLUMNS).any():
        raise ValueError(f"得分文件存在重复索引：{path}")
    series = pd.to_numeric(df[chosen], errors="coerce")
    series.index = pd.MultiIndex.from_frame(df[INDEX_COLUMNS], names=INDEX_COLUMNS)
    return series.sort_index(), chosen


def load_method_configs(
    value: str | None,
    methods: list[str],
    rank_buffer: int,
    transaction_cost: float,
    cost_safety_margin: float,
) -> dict[str, dict[str, float | int | None]]:
    if rank_buffer < 0:
        raise ValueError("--rank-buffer 不能为负数。")
    configs: dict[str, dict[str, float | int | None]] = {
        method: {"rank_buffer": rank_buffer, "min_buy_score": None, "min_hold_score": None}
        for method in methods
    }
    # 内置 LR 得分与 LABEL 同为收益率单位，默认要求覆盖一买一卖成本和安全余量。
    if "linear_regression" in configs:
        configs["linear_regression"].update({
            "min_buy_score": 2 * transaction_cost + cost_safety_margin,
            "min_hold_score": 0.0,
        })
    if value is None:
        return configs
    path = validate_file(value, "--method-config-json")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("--method-config-json 顶层必须是对象。")
    unknown = sorted(set(raw) - set(methods))
    if unknown:
        raise ValueError(f"方法配置包含未传入的得分方法：{unknown}")
    allowed = {"rank_buffer", "min_buy_score", "min_hold_score"}
    for method, override in raw.items():
        if not isinstance(override, dict) or set(override) - allowed:
            raise ValueError(f"方法 {method} 的配置只允许 {sorted(allowed)}。")
        configs[method].update(override)
    for method, config in configs.items():
        config["rank_buffer"] = int(config["rank_buffer"] or 0)
        if config["rank_buffer"] < 0:
            raise ValueError(f"方法 {method} 的 rank_buffer 不能为负数。")
        for key in ["min_buy_score", "min_hold_score"]:
            if config[key] is not None:
                config[key] = float(config[key])
    return configs


def run_backtest(
    score: pd.Series,
    open_wide: pd.DataFrame,
    volume_wide: pd.DataFrame,
    transaction_cost: float,
    topk: int | None,
    membership_by_date: dict[pd.Timestamp, set[str]],
    rank_buffer: int = 0,
    min_buy_score: float | None = None,
    min_hold_score: float | None = None,
    simulation_end: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """复刻最新版主流程的现金、持仓、冻结仓位和实际成交状态。"""
    trading_dates = pd.DatetimeIndex(open_wide.index).sort_values()
    date_pos = {date: pos for pos, date in enumerate(trading_dates)}
    execution_schedule = {}
    for date, group in score.groupby(level="datetime"):
        signal_date = pd.Timestamp(date)
        pos = date_pos.get(signal_date)
        if pos is None or pos + 2 >= len(trading_dates):
            continue
        entry_date = trading_dates[pos + 1]
        scores = group.xs(date, level="datetime")
        scores = scores[scores.index.isin(membership_by_date.get(entry_date, set()))]
        execution_schedule[entry_date] = (signal_date, scores.dropna().sort_values(ascending=False))
    if not execution_schedule:
        raise ValueError("没有可执行信号；请检查得分日期、行情日期和成员资格是否对齐。")

    actual_holdings: dict[str, float] = {}
    last_valid_prices: dict[str, float] = {}
    cash = 1.0
    records = []
    active_interval = None

    def select_targets(scores: pd.Series) -> tuple[list[str], dict[str, int]]:
        scores = scores.dropna().sort_values(ascending=False)
        if topk is None:
            return scores.index.tolist(), {
                "score_count": len(scores), "buy_qualified_count": len(scores),
                "selected_count": len(scores), "retained_count": 0,
                "threshold_rejected_count": 0,
            }
        buy_qualified = scores if min_buy_score is None else scores[scores >= min_buy_score]
        ranks = pd.Series(np.arange(1, len(scores) + 1), index=scores.index)
        retained = []
        for stock in actual_holdings:
            if stock not in ranks or ranks[stock] > topk + rank_buffer:
                continue
            if min_hold_score is not None and scores[stock] < min_hold_score:
                continue
            retained.append(stock)
        retained.sort(key=lambda stock: ranks[stock])
        targets = retained[:topk]
        target_set = set(targets)
        for stock in buy_qualified.index:
            if len(targets) >= topk:
                break
            if stock not in target_set:
                targets.append(stock)
                target_set.add(stock)
        return targets, {
            "score_count": len(scores), "buy_qualified_count": len(buy_qualified),
            "selected_count": len(targets), "retained_count": len(retained[:topk]),
            "threshold_rejected_count": len(scores) - len(buy_qualified),
        }

    def market_state(date: pd.Timestamp) -> tuple[pd.Series, pd.Series]:
        open_row = open_wide.loc[date]
        volume_row = volume_wide.loc[date]
        valid_price = open_row.notna() & np.isfinite(open_row) & (open_row > 0)
        tradable = valid_price & volume_row.notna() & np.isfinite(volume_row) & (volume_row > 0)
        last_valid_prices.update(open_row[valid_price].to_dict())
        return open_row, tradable

    def account_value() -> float:
        stock_value = sum(
            shares * last_valid_prices[stock]
            for stock, shares in actual_holdings.items() if stock in last_valid_prices
        )
        return cash + stock_value

    def rebalance(targets: list[str], open_row: pd.Series, tradable: pd.Series, target_slots: int | None = None) -> dict[str, Any]:
        nonlocal cash
        targets = list(dict.fromkeys(targets))
        nav_before = account_value()
        slot_count = target_slots if target_slots is not None else len(targets)
        target_value = nav_before / slot_count if slot_count else 0.0
        target_set = set(targets)
        gross_sell = gross_buy = 0.0
        unfilled_sells, unfilled_buys = set(), set()

        for stock, shares in list(actual_holdings.items()):
            price = last_valid_prices.get(stock)
            if price is None:
                continue
            desired = target_value if stock in target_set else 0.0
            sell_value = max(shares * price - desired, 0.0)
            if sell_value <= 1e-14:
                continue
            if not bool(tradable.get(stock, False)):
                unfilled_sells.add(stock)
                continue
            trade_price = float(open_row[stock])
            sell_shares = min(shares, sell_value / trade_price)
            filled = sell_shares * trade_price
            actual_holdings[stock] = shares - sell_shares
            if actual_holdings[stock] <= 1e-14:
                del actual_holdings[stock]
            gross_sell += filled
            cash += filled * (1 - transaction_cost)

        requested = {}
        for stock in targets:
            price = last_valid_prices.get(stock)
            current = actual_holdings.get(stock, 0.0) * price if price is not None else 0.0
            buy_value = max(target_value - current, 0.0)
            if buy_value <= 1e-14:
                continue
            if not bool(tradable.get(stock, False)):
                unfilled_buys.add(stock)
                continue
            requested[stock] = buy_value
        requested_total = sum(requested.values())
        affordable = cash / (1 + transaction_cost)
        scale = min(1.0, affordable / requested_total) if requested_total > 0 else 0.0
        for stock, requested_value in requested.items():
            filled = requested_value * scale
            if filled <= 1e-14:
                continue
            price = float(open_row[stock])
            actual_holdings[stock] = actual_holdings.get(stock, 0.0) + filled / price
            gross_buy += filled
            cash -= filled * (1 + transaction_cost)
        if -1e-12 < cash < 0:
            cash = 0.0

        nav_after = account_value()
        frozen = [stock for stock in actual_holdings if not bool(tradable.get(stock, False))]
        frozen_value = sum(actual_holdings[s] * last_valid_prices[s] for s in frozen if s in last_valid_prices)
        base = nav_before if nav_before > 0 else np.nan
        return {
            "nav_before": nav_before, "nav_after": nav_after,
            "gross_buy": gross_buy, "gross_sell": gross_sell,
            "buy_turnover": gross_buy / base, "sell_turnover": gross_sell / base,
            "turnover": max(gross_buy, gross_sell) / base,
            "transaction_cost": (gross_buy + gross_sell) * transaction_cost / base,
            "cash_weight": cash / nav_after if nav_after > 0 else np.nan,
            "frozen_count": len(frozen),
            "frozen_weight": frozen_value / nav_after if nav_after > 0 else np.nan,
            "unfilled_buy_count": len(unfilled_buys), "unfilled_sell_count": len(unfilled_sells),
        }

    first_entry = min(execution_schedule)
    minimum_settlement = trading_dates[date_pos[max(execution_schedule)] + 1]
    final_settlement = minimum_settlement if simulation_end is None else pd.Timestamp(simulation_end)
    if final_settlement not in date_pos or final_settlement < minimum_settlement:
        raise ValueError("统一结算日不在行情交易日历中，或早于最后信号所需 T+2。")
    simulation_dates = trading_dates[(trading_dates >= first_entry) & (trading_dates <= final_settlement)]

    for date in simulation_dates:
        open_row, tradable = market_state(date)
        nav_at_open = account_value()
        if active_interval is not None:
            records.append({
                "date": date, "signal_date": active_interval["signal_date"],
                "entry_date": active_interval["entry_date"],
                "gross_ret": (nav_at_open - active_interval["nav_after"]) / active_interval["nav_before"],
                "net_ret": (nav_at_open - active_interval["nav_before"]) / active_interval["nav_before"],
                "liquidation_turnover": 0.0,
                "holdings": "|".join(active_interval["holdings"]),
                **active_interval["trade_stats"],
            })

        if date in execution_schedule:
            signal_date, scores = execution_schedule[date]
            targets, selection = select_targets(scores)
            stats = rebalance(targets, open_row, tradable, target_slots=topk)
            stats.update(selection)
            active_interval = {
                "signal_date": signal_date, "entry_date": date,
                "nav_before": stats["nav_before"], "nav_after": stats["nav_after"],
                "trade_stats": {k: v for k, v in stats.items() if k not in {"nav_before", "nav_after", "gross_buy", "gross_sell"}},
                "holdings": sorted(actual_holdings),
            }
        elif date < final_settlement:
            nav = account_value()
            frozen = [stock for stock in actual_holdings if not bool(tradable.get(stock, False))]
            frozen_value = sum(actual_holdings[s] * last_valid_prices[s] for s in frozen if s in last_valid_prices)
            active_interval = {
                "signal_date": pd.NaT, "entry_date": date,
                "nav_before": nav, "nav_after": nav, "holdings": sorted(actual_holdings),
                "trade_stats": {
                    "buy_turnover": 0.0, "sell_turnover": 0.0, "turnover": 0.0,
                    "transaction_cost": 0.0, "cash_weight": cash / nav if nav > 0 else np.nan,
                    "frozen_count": len(frozen), "frozen_weight": frozen_value / nav if nav > 0 else np.nan,
                    "unfilled_buy_count": 0, "unfilled_sell_count": 0,
                    "score_count": np.nan, "buy_qualified_count": np.nan,
                    "selected_count": len(actual_holdings), "retained_count": len(actual_holdings),
                    "threshold_rejected_count": np.nan,
                },
            }

    if not records:
        raise ValueError("回测没有生成任何完整的 T+1 至 T+2 收益区间。")
    final_open, final_tradable = market_state(final_settlement)
    nav_before_liquidation = account_value()
    liquidation = rebalance([], final_open, final_tradable)
    last = records[-1]
    base = active_interval["nav_before"]
    last["liquidation_turnover"] = liquidation["gross_sell"] / nav_before_liquidation if nav_before_liquidation > 0 else np.nan
    last["sell_turnover"] += liquidation["gross_sell"] / base
    last["turnover"] = max(last["buy_turnover"], last["sell_turnover"])
    last["transaction_cost"] += (liquidation["gross_buy"] + liquidation["gross_sell"]) * transaction_cost / base
    last["net_ret"] = (liquidation["nav_after"] - base) / base
    last["cash_weight"] = liquidation["cash_weight"]
    last["frozen_count"] = liquidation["frozen_count"]
    last["frozen_weight"] = liquidation["frozen_weight"]
    last["unfilled_sell_count"] += liquidation["unfilled_sell_count"]
    last["ending_holdings"] = "|".join(sorted(actual_holdings))
    return pd.DataFrame(records).set_index("date")


def calc_performance(series: pd.Series) -> dict[str, float]:
    series = pd.to_numeric(series, errors="coerce").dropna()
    if series.empty:
        raise ValueError("收益序列为空。")
    cumulative = (1 + series).cumprod()
    ann_ret = cumulative.iloc[-1] ** (252 / len(series)) - 1
    ann_vol = series.std() * np.sqrt(252)
    return {
        "annual_return": float(ann_ret), "annual_volatility": float(ann_vol),
        "sharpe": float(ann_ret / (ann_vol + 1e-9)),
        "max_drawdown": float(((cumulative - cumulative.cummax()) / cumulative.cummax()).min()),
        "cumulative_return": float(cumulative.iloc[-1] - 1),
    }


def save_outputs(results: dict[str, pd.DataFrame], metrics: dict[str, dict[str, float]], diagnostics: dict[str, Any], output_dir: str | Path, prefix: str) -> dict[str, str]:
    out = validate_output_dir(output_dir)
    paths = {}
    nav = {}
    for method, frame in results.items():
        path = out / f"{prefix}_{method}_daily_returns.csv"
        frame.to_csv(path, encoding="utf-8-sig")
        paths[f"{method}_daily_returns"] = str(path)
        nav[f"{method}_nav"] = (1 + frame["net_ret"]).cumprod()
    nav_df = pd.DataFrame(nav)
    if "benchmark_nav" in nav_df:
        for method in results:
            if method != "benchmark":
                nav_df[f"{method}_excess"] = nav_df[f"{method}_nav"] / nav_df["benchmark_nav"]
    nav_path = out / f"{prefix}_nav_curve.csv"
    metrics_path = out / f"{prefix}_backtest_metrics.csv"
    metrics_json = out / f"{prefix}_backtest_metrics.json"
    diagnostics_path = out / f"{prefix}_diagnostics.json"
    nav_df.to_csv(nav_path, encoding="utf-8-sig")
    pd.DataFrame(metrics).T.to_csv(metrics_path, encoding="utf-8-sig")
    metrics_json.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    diagnostics_path.write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8")
    paths.update({"nav_curve": str(nav_path), "metrics_csv": str(metrics_path), "metrics_json": str(metrics_json), "diagnostics_json": str(diagnostics_path)})
    return paths


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="以开盘成交、动态股票池和不可成交约束执行状态化 Top-K 回测。")
    parser.add_argument("--score-csvs", default=DEFAULT_SCORE_CSVS, help="逗号分隔的 method=score.csv[.gz]。")
    parser.add_argument("--raw-csv", default=DEFAULT_RAW_CSV)
    parser.add_argument("--membership-csv", default=DEFAULT_MEMBERSHIP_CSV)
    parser.add_argument("--score-col", default=None)
    parser.add_argument("--topk", type=int, default=30)
    parser.add_argument("--transaction-cost", type=float, default=0.001)
    parser.add_argument("--rank-buffer", type=int, default=5)
    parser.add_argument("--cost-safety-margin", type=float, default=0.001)
    parser.add_argument("--method-config-json", default=None, help="可选：按方法覆盖 rank_buffer/min_buy_score/min_hold_score。")
    parser.add_argument("--test-start", default="2018-01-01")
    parser.add_argument("--test-end", default="2019-06-01")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--output-prefix", default=None)
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        if args.topk <= 0 or args.transaction_cost < 0 or args.cost_safety_margin < 0:
            raise ValueError("--topk 必须为正数，成本和安全余量不能为负数。")
        start, end = pd.Timestamp(args.test_start), pd.Timestamp(args.test_end)
        if start > end:
            raise ValueError("--test-start 不能晚于 --test-end。")
        score_specs = parse_score_csvs(args.score_csvs)
        raw = load_raw_ohlcv(args.raw_csv)
        membership = load_membership(args.membership_csv)
        open_all = raw["open"].unstack("instrument")
        volume_all = raw["volume"].unstack("instrument")
        future_dates = open_all.index[open_all.index > end]
        if len(future_dates) < 2:
            raise ValueError("TEST_END 之后不足两个行情交易日，无法结算最后信号。")
        settlement_end = future_dates[1]
        open_wide = open_all.loc[start:settlement_end]
        volume_wide = volume_all.loc[start:settlement_end]
        membership_by_date = build_membership_by_date(membership, open_wide.index)
        methods = [method for method, _ in score_specs]
        configs = load_method_configs(args.method_config_json, methods, args.rank_buffer, args.transaction_cost, args.cost_safety_margin)

        results: dict[str, pd.DataFrame] = {}
        score_columns = {}
        for method, path in score_specs:
            score, score_column = load_score_series(path, args.score_col)
            dates = score.index.get_level_values("datetime")
            score = score[(dates >= start) & (dates <= end)]
            config = configs[method]
            results[method] = run_backtest(
                score, open_wide, volume_wide, args.transaction_cost, args.topk,
                membership_by_date, simulation_end=settlement_end, **config,
            )
            score_columns[method] = score_column

        signal_dates = open_wide.index[(open_wide.index >= start) & (open_wide.index <= end)]
        benchmark_index = pd.MultiIndex.from_product([signal_dates, open_wide.columns], names=INDEX_COLUMNS)
        benchmark_score = pd.Series(0.0, index=benchmark_index)
        results["benchmark"] = run_backtest(
            benchmark_score, open_wide, volume_wide, args.transaction_cost, None,
            membership_by_date, simulation_end=settlement_end,
        )
        metrics = {method: calc_performance(frame["net_ret"]) for method, frame in results.items()}
        diagnostics = {
            "说明": "T 日信号、T+1 开盘成交、T+2 开盘结算；按实际成交额收费并统一期末清仓。",
            "raw_csv": str(args.raw_csv), "membership_csv": str(args.membership_csv),
            "test_start": str(start.date()), "test_end": str(end.date()),
            "settlement_end": str(settlement_end.date()), "topk": args.topk,
            "transaction_cost": args.transaction_cost, "method_configs": configs,
            "score_columns": score_columns,
            "methods": {
                method: {
                    "rows": len(frame), "date_min": str(frame.index.min().date()), "date_max": str(frame.index.max().date()),
                    "mean_turnover": float(frame["turnover"].mean()),
                    "mean_cash_weight": float(frame["cash_weight"].mean()),
                    "unfilled_buys": int(frame["unfilled_buy_count"].sum()),
                    "unfilled_sells": int(frame["unfilled_sell_count"].sum()),
                } for method, frame in results.items()
            },
        }
        paths = save_outputs(results, metrics, diagnostics, args.output_dir, build_output_prefix(args.output_prefix))
    except Exception as exc:
        print("\n[错误] 状态化组合回测失败。", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        if args.debug:
            traceback.print_exc()
        sys.exit(1)

    print("\n绩效指标:")
    print(pd.DataFrame(metrics).T.to_string(float_format=lambda value: f"{value:.6f}"))
    print("\n脚本输出文件:")
    for name, path in paths.items():
        print(f"  {name}: {path}")


if __name__ == "__main__":
    main()
