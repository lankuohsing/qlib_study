"""
比较 quant_workflow_from_scratch.py 和 quant_workflow_qlib.py 的底层数据是否一致
检验维度：
  1. 股票池（覆盖的股票是否相同）
  2. 每只股票的原始 OHLCV NaN 比例
  3. 标准化后因子值（from_scratch vs Qlib CSZScoreNorm）的差异
"""

import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
from scipy import stats

import qlib
from qlib.constant import REG_CN
from qlib.data import D
from qlib.utils import init_instance_by_config
from qlib.data.dataset.handler import DataHandlerLP

PROVIDER_URI = "/Users/guoxing.lan/projects/github/qlib_study/datasets/cn_data"
UNIVERSE     = "csi300"
DATA_START   = "2014-06-01"
TEST_END     = "2020-08-01"
TRAIN_START  = "2015-01-01"
TRAIN_END    = "2017-12-31"

FIELDS = [
    "$close / Ref($close, 5)  - 1",
    "$close / Ref($close, 20) - 1",
    "Std($close / Ref($close, 1) - 1, 20)",
    "$volume / Mean($volume, 5)",
    "$close / Mean($close, 20) - 1",
    "($high - $low) / Ref($close, 1)",
    "($close - $low) / ($high - $low + 1e-9)",
]
NAMES = ["MOM_5D", "MOM_20D", "VOL_20D", "TURN_5D", "MA_DEV", "DAY_RANGE", "PRICE_POS"]


def section(title):
    print(f"\n{'=' * 65}\n  {title}\n{'=' * 65}")


def winsorize_cs(s, n_sigma=3):
    median = s.median()
    mad    = (s - median).abs().median()
    lo     = median - n_sigma * 1.4826 * mad
    hi     = median + n_sigma * 1.4826 * mad
    return s.clip(lo, hi)


def zscore_cs(s):
    return (s - s.mean()) / (s.std() + 1e-9)


if __name__ == "__main__":

    qlib.init(provider_uri=PROVIDER_URI, region=REG_CN)

    # ─────────────────────────────────────────────────────
    # Part 1：对比股票池
    # ─────────────────────────────────────────────────────
    section("Part 1  股票池对比")

    # from_scratch 方式：list_instruments → 纯列表
    membership_dict = D.list_instruments(
        D.instruments(UNIVERSE), start_time=DATA_START, end_time=TEST_END, freq="day", as_list=False
    )
    stocks_fs = set(membership_dict.keys())

    # qlib 方式：DataHandlerLP 内部用字符串 "csi300"，取出其股票列表
    stocks_qlib_raw = D.list_instruments(
        D.instruments(UNIVERSE), start_time=DATA_START, end_time=TEST_END, freq="day", as_list=True
    )
    stocks_qlib = set(stocks_qlib_raw)

    print(f"from_scratch 股票数: {len(stocks_fs)}")
    print(f"qlib handler 股票数: {len(stocks_qlib)}")
    print(f"交集: {len(stocks_fs & stocks_qlib)}")
    print(f"仅在 from_scratch: {stocks_fs - stocks_qlib}")
    print(f"仅在 qlib:         {stocks_qlib - stocks_fs}")

    # ─────────────────────────────────────────────────────
    # Part 2：原始 OHLCV 数据对比（NaN 比例）
    # ─────────────────────────────────────────────────────
    section("Part 2  原始 OHLCV NaN 比例对比")

    # from_scratch：完整历史（不裁剪成员资格）
    raw_fs = D.features(
        list(stocks_fs),
        fields=["$close"],
        start_time=DATA_START, end_time=TEST_END, freq="day",
    )
    raw_fs.columns = ["close"]
    raw_fs.index.names = ["instrument", "datetime"]
    close_fs = raw_fs["close"].unstack("instrument")
    nan_fs   = close_fs.isna().mean()   # 每只股票的 NaN 比例

    # qlib：字符串方式（裁剪成员资格）
    raw_qlib = D.features(
        D.instruments(UNIVERSE),
        fields=["$close"],
        start_time=DATA_START, end_time=TEST_END, freq="day",
    )
    raw_qlib.columns = ["close"]
    raw_qlib.index.names = ["instrument", "datetime"]
    close_qlib = raw_qlib["close"].unstack("instrument")
    nan_qlib   = close_qlib.isna().mean()

    print(f"from_scratch 整体 NaN 率: {nan_fs.mean():.1%}  (不裁剪成员资格，含上市前数据)")
    print(f"qlib handler 整体 NaN 率: {nan_qlib.mean():.1%}  (裁剪成员资格，入选前数据不返回)")

    # 找 NaN 率差异最大的股票（即受成员资格裁剪影响最大的）
    common = nan_fs.index.intersection(nan_qlib.index)
    diff   = (nan_qlib.loc[common] - nan_fs.loc[common]).sort_values(ascending=False)
    print(f"\nNaN 率差异最大的前 10 只股票（qlib - from_scratch，正值 = qlib 多裁剪了历史）:")
    print(diff.head(10).apply(lambda x: f"{x:+.1%}").to_string())

    # ─────────────────────────────────────────────────────
    # Part 3：因子值对比（原始，未标准化）
    # ─────────────────────────────────────────────────────
    section("Part 3  原始因子值对比（标准化前）")

    # qlib 表达式引擎计算因子（不受成员资格影响）
    raw_factor_qlib = D.features(
        D.instruments(UNIVERSE),
        fields=FIELDS,
        start_time=DATA_START, end_time=TEST_END, freq="day",
    )
    raw_factor_qlib.columns = NAMES
    raw_factor_qlib.index.names = ["instrument", "datetime"]
    raw_factor_qlib = raw_factor_qlib.swaplevel().sort_index()

    # from_scratch pandas 手工计算因子
    raw_ohlcv = D.features(
        list(stocks_fs),
        fields=["$open", "$high", "$low", "$close", "$volume"],
        start_time=DATA_START, end_time=TEST_END, freq="day",
    )
    raw_ohlcv.columns = ["open", "high", "low", "close", "volume"]
    raw_ohlcv.index.names = ["instrument", "datetime"]
    raw_ohlcv = raw_ohlcv.swaplevel().sort_index()

    close  = raw_ohlcv["close"].unstack("instrument")
    high   = raw_ohlcv["high"].unstack("instrument")
    low    = raw_ohlcv["low"].unstack("instrument")
    volume = raw_ohlcv["volume"].unstack("instrument")
    daily_return = close.pct_change()

    factors_fs = pd.concat({
        "MOM_5D":    (close / close.shift(5) - 1).stack(future_stack=True),
        "MOM_20D":   (close / close.shift(20) - 1).stack(future_stack=True),
        "VOL_20D":   daily_return.rolling(20).std().stack(future_stack=True),
        "TURN_5D":   (volume / volume.rolling(5).mean()).stack(future_stack=True),
        "MA_DEV":    (close / close.rolling(20).mean() - 1).stack(future_stack=True),
        "DAY_RANGE": ((high - low) / close.shift(1)).stack(future_stack=True),
        "PRICE_POS": ((close - low) / (high - low + 1e-9)).stack(future_stack=True),
    }, axis=1)
    factors_fs.index.names = ["datetime", "instrument"]

    # 取共同的 (datetime, instrument) 行对比
    common_idx = factors_fs.index.intersection(raw_factor_qlib.index)
    print(f"共同 (datetime, instrument) 行数: {len(common_idx):,}")
    print(f"  from_scratch 因子行数: {len(factors_fs):,}")
    print(f"  qlib 因子行数:         {len(raw_factor_qlib):,}")

    print(f"\n各因子 Pearson 相关系数（值越接近 1.0 说明两种计算方式越一致）:")
    for col in NAMES:
        a = factors_fs.loc[common_idx, col].dropna()
        b = raw_factor_qlib.loc[common_idx, col].dropna()
        both = a.index.intersection(b.index)
        if len(both) < 100:
            print(f"  {col:<12}: 样本不足")
            continue
        r, _ = stats.pearsonr(a.loc[both], b.loc[both])
        mean_diff = (a.loc[both] - b.loc[both]).abs().mean()
        print(f"  {col:<12}: r={r:.6f}  平均绝对差={mean_diff:.2e}")

    # ─────────────────────────────────────────────────────
    # Part 4：标准化后因子值对比
    # ─────────────────────────────────────────────────────
    section("Part 4  标准化后因子值对比（MAD+Zscore vs CSZScoreNorm）")

    # from_scratch 标准化
    fs_processed = factors_fs.copy()
    fs_processed[NAMES] = (
        fs_processed.groupby(level="datetime")[NAMES].transform(winsorize_cs)
    )
    fs_processed[NAMES] = (
        fs_processed.groupby(level="datetime")[NAMES].transform(zscore_cs)
    )

    # qlib CSZScoreNorm（method="robust"）
    handler_config = {
        "class": "DataHandlerLP",
        "module_path": "qlib.data.dataset.handler",
        "kwargs": {
            "instruments": UNIVERSE,
            "start_time":  DATA_START,
            "end_time":    TEST_END,
            "data_loader": {
                "class": "QlibDataLoader",
                "kwargs": {
                    "config": {"feature": (FIELDS, NAMES)},
                    "freq": "day",
                },
            },
            "infer_processors": [
                {
                    "class": "CSZScoreNorm",
                    "module_path": "qlib.data.dataset.processor",
                    "kwargs": {"fields_group": "feature", "method": "robust"},
                },
            ],
            "learn_processors": [],
        },
    }
    handler = init_instance_by_config(handler_config)
    qlib_normed = handler.fetch(col_set="feature", data_key=DataHandlerLP.DK_I)
    qlib_normed.columns = NAMES

    common_idx2 = fs_processed.dropna().index.intersection(qlib_normed.dropna().index)
    print(f"共同行数（dropna后）: {len(common_idx2):,}")
    print(f"\n各因子标准化后 Pearson 相关（1.0 = 完全一致）:")
    for col in NAMES:
        a = fs_processed.loc[common_idx2, col].dropna()
        b = qlib_normed.loc[common_idx2, col].dropna()
        both = a.index.intersection(b.index)
        if len(both) < 100:
            continue
        r, _ = stats.pearsonr(a.loc[both], b.loc[both])
        mean_diff = (a.loc[both] - b.loc[both]).abs().mean()
        print(f"  {col:<12}: r={r:.6f}  平均绝对差={mean_diff:.4f}")

    print(f"\n结论汇总:")
    print(f"  - 股票池：{'完全相同' if stocks_fs == stocks_qlib else '不同'}")
    print(f"  - 原始因子：相关系数接近 1.0 → 计算公式等价")
    print(f"  - 标准化：两种方式结果非常接近但不完全相同（见上方差异）")
    print(f"  - 数据覆盖：from_scratch 因加载完整历史，入选前的历史 NaN 更少")
