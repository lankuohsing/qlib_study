"""
两种 Qlib 数据加载方式的对比
============================

问题背景
--------
用 D.features 加载 CSI 300 数据时，有两种写法：
  方式 A（直接传字符串）—— 简单，但会产生虚假 NaN
  方式 B（两步加载）   —— 稍复杂，但结果正确

本脚本并排展示两种方式，并打印 NaN 占比供对比。
"""

import warnings
warnings.filterwarnings("ignore")

import pandas as pd
import qlib
from qlib.constant import REG_CN
from qlib.data import D

PROVIDER_URI = "/Users/guoxing.lan/projects/github/qlib_study/datasets/cn_data"
DATA_START   = "2014-06-01"   # 比训练集早 6 个月，为 rolling 窗口预热
TRAIN_START  = "2015-01-01"
TEST_END     = "2020-08-01"

qlib.init(provider_uri=PROVIDER_URI, region=REG_CN)

# ─────────────────────────────────────────────────────────
# 方式 A：直接传字符串 "csi300"（❌ 有问题）
# ─────────────────────────────────────────────────────────
#
# Qlib 内部会把每只股票的数据裁剪到其"成员资格区间"：
#   某股票 2016-06 才入选 CSI 300，则其数据只从 2016-06 开始。
#   但 rolling(20)/shift(5) 需要更早的历史作为预热窗口——
#   在入选后的头几十个交易日，rolling 窗口覆盖不到足够样本，
#   产生 NaN，这些 NaN 并非真实缺失，而是被 Qlib 人为截断导致的。
#
raw_A = D.features(
    D.instruments("csi300"),            # ← 直接传字符串
    fields=["$close"],
    start_time=DATA_START,
    end_time=TEST_END,
    freq="day",
)
raw_A.columns = ["close"]
raw_A.index.names = ["instrument", "datetime"]
raw_A = raw_A.swaplevel().sort_index()

close_A  = raw_A["close"].unstack("instrument")
mom20_A  = close_A / close_A.shift(20) - 1         # 20 日动量因子

# ─────────────────────────────────────────────────────────
# 方式 B：两步加载（✅ 正确）
# ─────────────────────────────────────────────────────────
#
# Step B-1：先取成员列表，保留每只股票的入选时间段
#           此时 Qlib 只返回 meta 信息，不加载行情
membership_dict = D.list_instruments(
    D.instruments("csi300"),
    start_time=DATA_START,
    end_time=TEST_END,
    freq="day",
    as_list=False,          # 保留 {stock: [(start, end), ...]}
)
all_stocks = list(membership_dict.keys())

# Step B-2：传纯列表 → Qlib 内部 spans=None → 不做成员资格裁剪
#           每只股票都返回从 DATA_START 开始的完整历史，
#           rolling 窗口有足够数据预热，不再产生虚假 NaN。
raw_B = D.features(
    all_stocks,                         # ← 传列表，不传字符串
    fields=["$close"],
    start_time=DATA_START,
    end_time=TEST_END,
    freq="day",
)
raw_B.columns = ["close"]
raw_B.index.names = ["instrument", "datetime"]
raw_B = raw_B.swaplevel().sort_index()

close_B  = raw_B["close"].unstack("instrument")
mom20_B  = close_B / close_B.shift(20) - 1

# ─────────────────────────────────────────────────────────
# 对比：在训练期内，NaN 占比差异
# ─────────────────────────────────────────────────────────
def nan_pct_in_train(df):
    """只看训练期（2015 年起）的 NaN 占比"""
    sub = df.loc[TRAIN_START:]
    return sub.isna().mean().mean() * 100

print("=" * 55)
print("  MOM_20D 因子  NaN 占比对比（训练期 2015-01-01 起）")
print("=" * 55)
print(f"  方式 A（直接传字符串）: {nan_pct_in_train(mom20_A):.2f}%  ← 偏高，含虚假 NaN")
print(f"  方式 B（两步加载）    : {nan_pct_in_train(mom20_B):.2f}%  ← 正常，仅窗口预热导致")
print()

# 进一步找一只"入选时间较晚"的股票，具体展示差异
# 找在 2015 年后才入选的股票（第一个 span 的 start > TRAIN_START）
late_entries = {
    k: v for k, v in membership_dict.items()
    if v[0][0] > pd.Timestamp(TRAIN_START)
}
if late_entries:
    sample_stock = sorted(late_entries, key=lambda k: late_entries[k][0][0])[0]
    entry_date   = late_entries[sample_stock][0][0]

    row_A = mom20_A[sample_stock].dropna().index.min() if sample_stock in mom20_A.columns else None
    row_B = mom20_B[sample_stock].dropna().index.min() if sample_stock in mom20_B.columns else None

    print(f"  示例股票: {sample_stock}  入选 CSI300 日期: {pd.Timestamp(entry_date).date()}")
    print(f"    方式 A  MOM_20D 首个有效日: {row_A.date() if row_A else 'N/A'}"
          f"  （入选后约 20 个交易日才能算出因子）")
    print(f"    方式 B  MOM_20D 首个有效日: {row_B.date() if row_B else 'N/A'}"
          f"  （DATA_START + 20 个交易日即可，早于入选日）")
    print()
    print("  结论：方式 A 中该股票入选初期的因子值全部是 NaN，")
    print("        这不是真实缺失，而是 Qlib 截断历史导致的。")
    print("        用这些行训练/回测会低估股票池覆盖率，影响结果准确性。")
print("=" * 55)
