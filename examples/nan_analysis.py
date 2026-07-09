"""
原始量价特征 NaN 分析脚本
================================================
加载与 quant_workflow_from_scratch.py 相同的数据集，
将原始行情保存为 CSV，并逐股票分析 NaN 出现的位置与原因。

NaN 来源分类：
  Type A  上市前（Pre-listing）：某只股票尚未 IPO，整段历史均无数据
  Type B  停牌（Suspension）  ：股票已上市，但某段连续交易日无成交（停牌/退市过渡）
"""

import os
import warnings

warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd

import qlib
from qlib.constant import REG_CN
from qlib.data import D

# ─────────────────────────────────────────────────────────
# 配置（与 quant_workflow_from_scratch.py 保持一致）
# ─────────────────────────────────────────────────────────
PROVIDER_URI = "/Users/guoxing.lan/projects/github/qlib_study/datasets/cn_data"
UNIVERSE     = "csi300"
DATA_START   = "2014-06-01"
TEST_END     = "2020-08-01"
OUTPUT_DIR   = "outputs/nan_analysis"

if __name__ == "__main__":

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # ─────────────────────────────────────────────────────
    # 1. 加载数据（与修复后的 from_scratch 方式相同）
    # ─────────────────────────────────────────────────────
    print("初始化 Qlib ...")
    qlib.init(provider_uri=PROVIDER_URI, region=REG_CN)

    membership_dict = D.list_instruments(
        D.instruments(UNIVERSE),
        start_time=DATA_START,
        end_time=TEST_END,
        freq="day",
        as_list=False,
    )
    all_stocks = list(membership_dict.keys())

    print(f"CSI300 历史成员数: {len(all_stocks)} 只  |  数据区间: {DATA_START} ~ {TEST_END}")

    raw_df = D.features(
        all_stocks,
        fields=["$open", "$high", "$low", "$close", "$volume"],
        start_time=DATA_START,
        end_time=TEST_END,
        freq="day",
    )
    raw_df.columns = ["open", "high", "low", "close", "volume"]
    raw_df.index.names = ["instrument", "datetime"]
    raw_df = raw_df.swaplevel().sort_index()

    n_days   = raw_df.index.get_level_values("datetime").nunique()
    n_stocks = raw_df.index.get_level_values("instrument").nunique()
    print(f"raw_df shape={raw_df.shape}  ({n_days} 交易日 × {n_stocks} 股票)\n")

    # ─────────────────────────────────────────────────────
    # 2. 保存原始行情到 CSV
    # ─────────────────────────────────────────────────────
    csv_path = f"{OUTPUT_DIR}/raw_ohlcv.csv"
    raw_df.to_csv(csv_path)
    print(f"原始行情已保存 → {csv_path}  ({os.path.getsize(csv_path)//1024//1024} MB)\n")

    # ─────────────────────────────────────────────────────
    # 3. 构建宽表，找出所有 NaN 位置
    # ─────────────────────────────────────────────────────
    close  = raw_df["close"].unstack("instrument")   # shape: (交易日, 股票)
    all_dates = close.index

    # any_nan[date, stock] = True  → 该行存在 NaN 字段
    #                      = False → 该行数据完整
    #                      = NaN   → 该 (date, stock) 组合完全不在 raw_df 里（未上市/不在数据集）
    any_nan = raw_df.isna().any(axis=1).unstack("instrument")  # (交易日, 股票)

    total_cells   = any_nan.size                               # 日 × 股票 理论总格数
    absent_cells  = any_nan.isna().sum().sum()                 # 完全缺失（不在 raw_df）
    present_nan   = (any_nan == True).sum().sum()              # 在 raw_df 但字段有 NaN
    valid_cells   = (any_nan == False).sum().sum()             # 数据完整
    total_nan     = absent_cells + present_nan                 # 真正意义上的 NaN

    print(f"NaN 整体概况:")
    print(f"  总格子数:        {total_cells:,}  ({n_days} 日 × {n_stocks} 股票)")
    print(f"  完整数据格子:    {valid_cells:,}  ({valid_cells/total_cells:.1%})")
    print(f"  NaN 格子（合计）:{total_nan:,}  ({total_nan/total_cells:.1%})")
    print(f"    其中 完全缺失（未上市等）: {absent_cells:,}  ({absent_cells/total_cells:.1%})")
    print(f"    其中 行存在但字段为NaN:   {present_nan:,}  ({present_nan/total_cells:.1%})")
    print(f"  无任何 NaN 的股票数: {(any_nan.isna().sum() + (any_nan==True).sum() == 0).sum()}\n")

    nan_cells = total_nan  # 后续贡献比例用真实总 NaN

    # ─────────────────────────────────────────────────────
    # 4. 逐股票分类 NaN 原因
    # ─────────────────────────────────────────────────────
    records = []

    for stock in all_stocks:
        if stock not in close.columns:
            continue

        s = close[stock]                        # 该股票的收盘价序列
        is_nan = s.isna()

        if not is_nan.any():
            records.append({
                "instrument":       stock,
                "first_valid_date": s.first_valid_index().date(),
                "last_valid_date":  s.last_valid_index().date(),
                "total_nan_days":   0,
                "pre_listing_days": 0,
                "suspension_days":  0,
                "nan_ratio":        0.0,
                "nan_type":         "no_nan",
            })
            continue

        first_valid = s.first_valid_index()
        last_valid  = s.last_valid_index()

        # Type A：第一个有效日之前（上市前）
        pre_listing = is_nan[is_nan.index < first_valid].sum() if first_valid else len(s)

        # Type B：有效区间内的 NaN（停牌）
        if first_valid and last_valid:
            in_range    = is_nan[(is_nan.index >= first_valid) & (is_nan.index <= last_valid)]
            suspension  = in_range.sum()
        else:
            suspension  = 0

        total_nan = is_nan.sum()

        records.append({
            "instrument":       stock,
            "first_valid_date": first_valid.date() if first_valid else None,
            "last_valid_date":  last_valid.date()  if last_valid  else None,
            "total_nan_days":   int(total_nan),
            "pre_listing_days": int(pre_listing),
            "suspension_days":  int(suspension),
            "nan_ratio":        round(total_nan / n_days, 4),
            "nan_type": (
                "no_nan"     if total_nan == 0 else
                "pre_listing_only" if suspension == 0 and pre_listing > 0 else
                "suspension_only"  if pre_listing == 0 and suspension > 0 else
                "both"             if pre_listing > 0 and suspension > 0 else
                "other"
            ),
        })

    stock_df = pd.DataFrame(records).set_index("instrument")

    # ─────────────────────────────────────────────────────
    # 5. 汇总统计
    # ─────────────────────────────────────────────────────
    print("=" * 60)
    print("  NaN 类型分布（按股票数统计）")
    print("=" * 60)
    type_counts = stock_df["nan_type"].value_counts()
    for t, cnt in type_counts.items():
        desc = {
            "no_nan":            "无 NaN",
            "pre_listing_only":  "仅上市前 NaN（IPO 后数据完整）",
            "suspension_only":   "仅停牌 NaN（上市前数据完整）",
            "both":              "上市前 + 停牌均有 NaN",
            "other":             "其他",
        }.get(t, t)
        print(f"  {desc:<35} {cnt:>4} 只")

    print(f"\n{'─'*60}")
    print("  上市前 NaN（Type A）统计")
    print(f"{'─'*60}")
    pre = stock_df[stock_df["pre_listing_days"] > 0]["pre_listing_days"]
    print(f"  受影响股票数:     {len(pre)}")
    print(f"  平均上市前缺失:   {pre.mean():.1f} 天")
    print(f"  最多缺失:         {pre.max()} 天  ({stock_df.loc[pre.idxmax(), 'first_valid_date']} 才有数据)")
    print(f"  对总 NaN 的贡献:  {stock_df['pre_listing_days'].sum():,} 格  "
          f"({stock_df['pre_listing_days'].sum()/nan_cells:.1%})")

    print(f"\n{'─'*60}")
    print("  停牌 NaN（Type B）统计")
    print(f"{'─'*60}")
    sus = stock_df[stock_df["suspension_days"] > 0]["suspension_days"]
    print(f"  受影响股票数:     {len(sus)}")
    print(f"  平均停牌缺失:     {sus.mean():.1f} 天")
    print(f"  最多停牌:         {sus.max()} 天  (股票: {sus.idxmax()})")
    print(f"  对总 NaN 的贡献:  {stock_df['suspension_days'].sum():,} 格  "
          f"({stock_df['suspension_days'].sum()/nan_cells:.1%})")

    # ─────────────────────────────────────────────────────
    # 6. NaN 最严重的前 20 只股票
    # ─────────────────────────────────────────────────────
    print(f"\n{'─'*60}")
    print("  NaN 最多的前 20 只股票")
    print(f"{'─'*60}")
    top20 = stock_df.nlargest(20, "total_nan_days")[
        ["first_valid_date", "total_nan_days", "pre_listing_days", "suspension_days", "nan_ratio", "nan_type"]
    ]
    print(top20.to_string())

    # ─────────────────────────────────────────────────────
    # 7. 按日期统计：哪些交易日 NaN 最多（可能是系统性停牌/节假日）
    # ─────────────────────────────────────────────────────
    # 按日期统计：每天有多少只股票缺数据（absent + present_nan）
    daily_nan = (any_nan != False).sum(axis=1).astype(int)
    print(f"\n{'─'*60}")
    print("  每日 NaN 股票数 Top 10（可能是系统性停牌日）")
    print(f"{'─'*60}")
    print(daily_nan.nlargest(10).to_string())

    # ─────────────────────────────────────────────────────
    # 8. 保存详细分析结果
    # ─────────────────────────────────────────────────────
    stock_csv = f"{OUTPUT_DIR}/nan_by_stock.csv"
    stock_df.to_csv(stock_csv)
    print(f"\n逐股票 NaN 分析已保存 → {stock_csv}")

    # 保存所有 NaN 位置的明细（date × stock 的 bool 表）
    nan_detail_csv = f"{OUTPUT_DIR}/nan_detail_wide.csv"
    any_nan.fillna(-1).astype(int).to_csv(nan_detail_csv)
    print(f"NaN 明细宽表已保存   → {nan_detail_csv}")

    print("\n分析完成。")
