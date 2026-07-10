"""
量化投资全流程 —— Qlib 实现（对照 quant_workflow_from_scratch.py）
=====================================================================
与 from_scratch 版本保持完全相同的：
  - 股票池：CSI 300（两步加载，避免成员资格裁剪）
  - 时间范围：train 2015-2017 / valid 2018 / test 2019-2020
  - 7 个基础因子（相同表达式）
  - 成员资格过滤逻辑
  - IC / ICIR 分析
  - 因子合成：等权（EW）和 LinearRegression（LR）
  - 回测逻辑（run_backtest）和绩效指标（calc_performance，252 交易日年化）

与 from_scratch 的核心差异（刻意保留，体现 Qlib 的方式）：
  - 特征定义：Qlib 表达式引擎 vs pandas rolling
  - 预处理：两步 Qlib Processor vs 手写截面函数
    注：CSZScoreNorm(robust) + CSZScoreNorm() 接近但不完全等价于
        winsorize_cs + zscore_cs（clip 前的中心点略有不同，见正文注释）
  - LR 训练：LinearModel(qlib) vs sklearn LinearRegression

最终输出与 from_scratch 完全相同格式的绩效对比表，便于逐行核对。
"""

import os
import warnings

warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
from scipy import stats

import qlib
from qlib.constant import REG_CN
from qlib.data import D
from qlib.data.dataset import DatasetH
from qlib.data.dataset.handler import DataHandlerLP
from qlib.contrib.model.linear import LinearModel
from qlib.utils import init_instance_by_config

# ─────────────────────────────────────────────────────────
# 全局配置（与 from_scratch 完全一致）
# ─────────────────────────────────────────────────────────
PROVIDER_URI = "/Users/guoxing.lan/projects/github/qlib_study/datasets/cn_data"
UNIVERSE     = "csi300"

TRAIN_START = "2015-01-01"
TRAIN_END   = "2017-12-31"
VALID_START = "2018-01-01"
VALID_END   = "2018-12-31"
TEST_START  = "2019-01-01"
TEST_END    = "2020-08-01"
DATA_START  = "2014-06-01"

TOPK             = 30
TRANSACTION_COST = 0.001

FACTOR_COLS = ["MOM_5D", "MOM_20D", "VOL_20D", "TURN_5D", "MA_DEV", "DAY_RANGE", "PRICE_POS"]

# Qlib 表达式语言描述的 7 个因子（与 from_scratch pandas 公式对应）
FIELDS = [
    "$close / Ref($close, 5)  - 1",            # MOM_5D
    "$close / Ref($close, 20) - 1",            # MOM_20D
    "Std($close / Ref($close, 1) - 1, 20)",    # VOL_20D
    "$volume / Mean($volume, 5)",               # TURN_5D
    "$close / Mean($close, 20) - 1",           # MA_DEV
    "($high - $low) / Ref($close, 1)",         # DAY_RANGE
    "($close - $low) / ($high - $low + 1e-9)", # PRICE_POS
]
NAMES = FACTOR_COLS

LABEL_FIELD = ["Ref($close, -1) / $close - 1"]
LABEL_NAME  = ["LABEL"]

# ─────────────────────────────────────────────────────────
# 辅助函数（与 from_scratch 完全相同）
# ─────────────────────────────────────────────────────────
def section(title):
    print(f"\n{'=' * 65}")
    print(f"  {title}")
    print(f"{'=' * 65}")


def segment(df, start, end):
    d = df.index.get_level_values("datetime")
    return df[(d >= start) & (d <= end)]


def score_equal_weight(df, factors):
    return df[factors].mean(axis=1)


def score_lr(df, factors, model):
    X = df[factors].dropna()
    return pd.Series(model.predict(X.values), index=X.index)


def calc_performance(series):
    cum     = (1 + series).cumprod()
    n       = len(series)
    ann_ret = cum.iloc[-1] ** (252 / n) - 1
    ann_vol = series.std() * np.sqrt(252)
    sharpe  = ann_ret / (ann_vol + 1e-9)
    max_dd  = ((cum - cum.cummax()) / cum.cummax()).min()
    cum_ret = cum.iloc[-1] - 1
    return {
        "年化收益": f"{ann_ret:.2%}",
        "年化波动": f"{ann_vol:.2%}",
        "夏普比率": f"{sharpe:.2f}",
        "最大回撤": f"{max_dd:.2%}",
        "累计收益": f"{cum_ret:.2%}",
    }


def run_backtest(test_score, test_ret_wide):
    """
    与 from_scratch 完全相同的回测逻辑：
    T 日因子 → 选 Top-K 持仓 → T+1 日等权持有 → 扣手续费
    """
    holdings = {}
    for date, grp in test_score.groupby(level="datetime"):
        top_stocks = grp.xs(date, level="datetime").nlargest(TOPK).index.tolist()
        holdings[date] = top_stocks

    records  = []
    prev_set = set()

    for date_T in sorted(holdings.keys()):
        curr_set   = set(holdings[date_T])
        ret_stocks = list(prev_set) if prev_set else []

        if date_T in test_ret_wide.index:
            valid_stocks = [s for s in ret_stocks if s in test_ret_wide.columns]
            day_rets     = test_ret_wide.loc[date_T, valid_stocks].dropna()
            port_ret     = day_rets.mean() if len(day_rets) > 0 else 0.0
        else:
            port_ret = 0.0

        turnover = len(curr_set.symmetric_difference(prev_set)) / (2 * TOPK) if prev_set else 1.0
        net_ret  = port_ret - turnover * TRANSACTION_COST

        records.append({"date": date_T, "gross_ret": port_ret, "turnover": turnover, "net_ret": net_ret})
        prev_set = curr_set

    return pd.DataFrame(records).set_index("date")


# ─────────────────────────────────────────────────────────
# macOS spawn 保护
# ─────────────────────────────────────────────────────────
if __name__ == "__main__":

    os.makedirs("outputs", exist_ok=True)

    # ─────────────────────────────────────────────────────
    # Step 1  初始化
    # ─────────────────────────────────────────────────────
    section("Step 1  初始化")
    qlib.init(provider_uri=PROVIDER_URI, region=REG_CN)

    # ─────────────────────────────────────────────────────
    # Step 2  数据加载（两步法，与 from_scratch 完全一致）
    # ─────────────────────────────────────────────────────
    section("Step 2  数据加载（两步法）")

    # 第一步：取成员列表，保留 spans 供后续成员资格过滤
    membership_dict = D.list_instruments(
        D.instruments(UNIVERSE),
        start_time=DATA_START,
        end_time=TEST_END,
        freq="day",
        as_list=False,
    )
    all_stocks = list(membership_dict.keys())
    print(f"CSI 300 历史成分股数量: {len(all_stocks)}")

    # 第二步：用 Qlib DataHandlerLP 加载特征
    # 传入纯列表（all_stocks），Qlib 不做成员资格裁剪，返回完整历史
    # 预处理：
    #   Step A - CSZScoreNorm(method="robust")：截面 MAD 去极值并 clip 到 [-3, 3]
    #            等价于 from_scratch 的 winsorize_cs，但实现细节略有不同：
    #            Qlib 先减 median 再除 MAD 再 clip，from_scratch 先 clip 再 zscore
    #   Step B - CSZScoreNorm()：截面 Z-Score（均值 0，标准差 1）
    #            等价于 from_scratch 的 zscore_cs
    handler_config = {
        "class": "DataHandlerLP",
        "module_path": "qlib.data.dataset.handler",
        "kwargs": {
            "instruments": all_stocks,
            "start_time":  DATA_START,
            "end_time":    TEST_END,
            "data_loader": {
                "class": "QlibDataLoader",
                "kwargs": {
                    "config": {
                        "feature": (FIELDS, NAMES),
                        "label":   (LABEL_FIELD, LABEL_NAME),
                    },
                    "freq": "day",
                },
            },
            "infer_processors": [
                {
                    "class": "CSZScoreNorm",
                    "module_path": "qlib.data.dataset.processor",
                    "kwargs": {"fields_group": "feature", "method": "robust"},
                },
                {
                    "class": "CSZScoreNorm",
                    "module_path": "qlib.data.dataset.processor",
                    "kwargs": {"fields_group": "feature"},
                },
            ],
            "learn_processors": [
                {"class": "DropnaLabel"},
            ],
        },
    }

    handler = init_instance_by_config(handler_config)

    # 取出全量数据（infer 预处理已应用）
    full_df = handler.fetch(col_set=["feature", "label"], data_key=DataHandlerLP.DK_I)
    # 将多级列压平为单级，列名格式 "(feature, MOM_5D)" → "MOM_5D"
    full_df.columns = [col[1] for col in full_df.columns]
    # Qlib handler 返回 (instrument, datetime) 顺序，reorder_levels 按名字显式调整为 (datetime, instrument)
    full_df = full_df.reorder_levels(["datetime", "instrument"]).sort_index()

    print(f"Handler 全量数据  shape = {full_df.shape}")
    print(f"  索引层级: {full_df.index.names}")
    print(f"  列: {list(full_df.columns)}")

    # ─────────────────────────────────────────────────────
    # Step 3  成员资格过滤 + 三段切分（与 from_scratch Step 3 完全一致）
    # ─────────────────────────────────────────────────────
    section("Step 3  成员资格过滤 + 三段切分")

    clean_df = full_df.dropna()

    dt  = pd.to_datetime(clean_df.index.get_level_values("datetime"))
    ins = clean_df.index.get_level_values("instrument")
    in_universe = pd.Series(False, index=clean_df.index)
    for stock, spans in membership_dict.items():
        for start, end in spans:
            # handler.fetch() 的 datetime 索引是 Timestamp，spans 也可能是字符串，统一转换
            in_universe |= (ins == stock) & (dt >= pd.Timestamp(start)) & (dt <= pd.Timestamp(end))
    clean_df = clean_df[in_universe]

    train_df = segment(clean_df, TRAIN_START, TRAIN_END)
    valid_df = segment(clean_df, VALID_START, VALID_END)
    test_df  = segment(clean_df, TEST_START,  TEST_END)

    print(f"处理后  shape = {clean_df.shape}")
    for name, df, s, e in [("train", train_df, TRAIN_START, TRAIN_END),
                            ("valid", valid_df, VALID_START, VALID_END),
                            ("test",  test_df,  TEST_START,  TEST_END)]:
        n_d = df.index.get_level_values("datetime").nunique()
        n_s = df.index.get_level_values("instrument").nunique()
        print(f"  {name} ({s} ~ {e})  shape={df.shape}  → {n_d} 交易日 × ~{n_s} 股票/日")

    # ─────────────────────────────────────────────────────
    # Step 4  IC / ICIR 分析（与 from_scratch Step 4 完全一致）
    # ─────────────────────────────────────────────────────
    section("Step 4  因子有效性分析（IC / ICIR）")

    print("IC  = Spearman(因子值, 次日收益率)，在每日截面计算，再对时序取统计")
    print("ICIR = IC均值 / IC标准差\n")

    n_train_days = train_df.index.get_level_values("datetime").nunique()
    ic_records   = {}

    for factor in FACTOR_COLS:
        ic_list = []
        for date, grp in train_df.groupby(level="datetime"):
            grp_flat = grp.xs(date, level="datetime")
            valid = grp_flat[[factor, "LABEL"]].dropna()
            if len(valid) < 10:
                continue
            ic_val, _ = stats.spearmanr(valid[factor], valid["LABEL"])
            ic_list.append(ic_val)
        s = pd.Series(ic_list)
        ic_records[factor] = {
            "IC均值":   round(s.mean(), 4),
            "IC标准差": round(s.std(),  4),
            "ICIR":     round(s.mean() / (s.std() + 1e-9), 4),
            "IC>0占比": round((s > 0).mean(), 3),
            "计算日数": len(s),
        }

    ic_table = pd.DataFrame(ic_records).T
    print(f"IC 分析结果（基于 train 段 {n_train_days} 个交易日）:")
    print(ic_table.to_string())

    valid_factors = ic_table[ic_table["IC均值"].abs() > 0.02].index.tolist()
    if valid_factors:
        print(f"\n筛选出有效因子（|IC均值| > 0.02）: {valid_factors}")
    else:
        print("\n未筛选到显著有效因子，改为使用全部因子")
        valid_factors = FACTOR_COLS

    # ─────────────────────────────────────────────────────
    # Step 5  因子合成（EW + LR，与 from_scratch Step 5 对齐）
    # ─────────────────────────────────────────────────────
    section("Step 5  因子合成（等权 vs LinearModel）")

    # ── 方式 A：等权平均 ──
    print("── 方式 A：等权平均 ──")
    print(f"  参与合成的因子: {valid_factors}")

    # 注意：这里没有按训练集 IC 符号对因子方向做校正。
    # 原因：IC 符号在不同市场 regime 下可能翻转（如训练期均值回归、测试期趋势延续），
    # 用训练集 IC 符号固定校正测试期会引入隐性的 regime 假设，反而使结果更差。
    train_score_ew = score_equal_weight(train_df, valid_factors)
    valid_score_ew = score_equal_weight(valid_df, valid_factors)
    test_score_ew  = score_equal_weight(test_df,  valid_factors)

    ew_ic_list = []
    for date, grp in train_df.groupby(level="datetime"):
        if date not in train_score_ew.index.get_level_values("datetime"):
            continue
        sc = train_score_ew.xs(date, level="datetime")
        lb = grp["LABEL"].xs(date, level="datetime").dropna()
        cm = sc.index.intersection(lb.index)
        if len(cm) < 10:
            continue
        ic_val, _ = stats.spearmanr(sc.loc[cm], lb.loc[cm])
        ew_ic_list.append(ic_val)
    ew_ic_s = pd.Series(ew_ic_list)
    print(f"  等权合成 train 段  IC均值={ew_ic_s.mean():.4f}  ICIR={ew_ic_s.mean()/(ew_ic_s.std()+1e-9):.4f}")

    # ── 方式 B：Qlib LinearModel（对应 from_scratch 的 sklearn LinearRegression）──
    print("\n── 方式 B：LinearModel（Qlib，estimator='ols'）──")

    # 构建 DatasetH 供 LinearModel 使用
    dataset = DatasetH(
        handler=handler,
        segments={
            "train": (TRAIN_START, TRAIN_END),
            "valid": (VALID_START, VALID_END),
            "test":  (TEST_START,  TEST_END),
        },
    )

    lr_model = LinearModel(
        estimator="ols",
        fit_intercept=True,  # 与 from_scratch sklearn LinearRegression 默认一致
        include_valid=False, # 只用 train 段训练，与 from_scratch 一致
    )
    lr_model.fit(dataset)

    # 打印学到的因子权重（与 from_scratch Step 5 对比）
    if hasattr(lr_model, "model") and hasattr(lr_model.model, "coef_"):
        coef_df = pd.Series(dict(zip(NAMES, lr_model.model.coef_))).round(6)
        print(f"  学到的因子权重（Qlib LinearModel）:")
        for fname, coef in coef_df.items():
            print(f"    {fname:<12} : {coef:+.6f}")
        print(f"  截距: {lr_model.model.intercept_:.6f}")

    # 在 test_df 上生成 LR 预测得分
    # predict() 返回 Series，索引已是 (datetime, instrument)，直接切片
    pred = lr_model.predict(dataset)
    test_score_lr = pred.loc[TEST_START:TEST_END]
    test_score_lr = test_score_lr.reindex(test_df.index).dropna()

    print(f"\n  LR 合成得分 shape (test): {test_score_lr.shape}")

    print("\n── 在 valid 集上对比两种合成方式的 IC ──")
    # predict() 默认预测 test 段；需传 segment="valid" 取验证集预测
    valid_score_lr = lr_model.predict(dataset, segment="valid")
    for method_name, s in [("等权EW", valid_score_ew), ("LR", valid_score_lr)]:
        ic_vals = []
        for date, grp in valid_df.groupby(level="datetime"):
            if date not in s.index.get_level_values("datetime"):
                continue
            dt_vals = s.xs(date, level="datetime")
            lb = grp["LABEL"].xs(date, level="datetime").dropna()
            cm = dt_vals.index.intersection(lb.index)
            if len(cm) < 10:
                continue
            ic_val, _ = stats.spearmanr(dt_vals.loc[cm], lb.loc[cm])
            ic_vals.append(ic_val)
        iv = pd.Series(ic_vals)
        print(f"  {method_name:<8}  IC均值={iv.mean():.4f}  ICIR={iv.mean()/(iv.std()+1e-9):.4f}")

    # ─────────────────────────────────────────────────────
    # Step 6+7  组合构建 + 回测（与 from_scratch 完全相同）
    # ─────────────────────────────────────────────────────
    # 加载原始收盘价宽表：从 TEST_START 前一天开始，保证第一个交易日 pct_change() 不为 NaN
    raw_close = D.features(
        all_stocks,
        fields=["$close"],
        start_time=str((pd.Timestamp(TEST_START) - pd.Timedelta(days=5)).date()),
        end_time=TEST_END,
        freq="day",
    )
    raw_close.columns = ["close"]
    raw_close.index.names = ["instrument", "datetime"]
    raw_close = raw_close.swaplevel().sort_index()
    test_ret_wide = raw_close["close"].unstack("instrument").pct_change()

    print(f"\n日收益率宽表  shape = {test_ret_wide.shape}")
    print("回测逻辑：T 日因子 → 选出 Top-K 持仓 → T+1 日等权持有 → 扣手续费")

    ret_results = {}
    for method_name, test_score in [("等权EW", test_score_ew), ("LR", test_score_lr)]:
        section(f"Step 6+7  组合构建 + 回测（{method_name}）")
        ret_df = run_backtest(test_score, test_ret_wide)
        ret_results[method_name] = ret_df

        avg_turnover = ret_df["turnover"].mean()
        print(f"  策略日收益序列  shape={ret_df.shape}")
        print(f"  平均换手率: {avg_turnover:.1%}（每次调仓约换 {avg_turnover*TOPK:.1f} 只）")
        print(ret_df.head(5).round(5))

    # ─────────────────────────────────────────────────────
    # 绩效汇总对比
    # ─────────────────────────────────────────────────────
    section("绩效汇总对比（Qlib 版本）")

    benchmark_ret = test_ret_wide.mean(axis=1).reindex(
        ret_results["等权EW"].index).fillna(0)

    perf = {
        "等权EW": calc_performance(ret_results["等权EW"]["net_ret"]),
        "LR":     calc_performance(ret_results["LR"]["net_ret"]),
        "基准":   calc_performance(benchmark_ret),
    }

    print(f"\n  绩效对比（test 期间 {TEST_START} ~ {TEST_END}）")
    print(f"{'─'*60}")
    print(f"  {'指标':<10}  {'等权EW':>14}  {'LR':>14}  {'基准（等权）':>14}")
    print(f"{'─'*60}")
    for k in perf["等权EW"]:
        print(f"  {k:<10}  {perf['等权EW'][k]:>14}  {perf['LR'][k]:>14}  {perf['基准'][k]:>14}")
    print(f"{'─'*60}")

    # 保存结果
    cum_ew    = (1 + ret_results["等权EW"]["net_ret"]).cumprod()
    cum_lr    = (1 + ret_results["LR"]["net_ret"]).cumprod()
    cum_bench = (1 + benchmark_ret).cumprod()
    nav_df = pd.DataFrame({
        "ew_nav":        cum_ew,
        "lr_nav":        cum_lr,
        "benchmark_nav": cum_bench,
        "ew_excess":     cum_ew   / cum_bench,
        "lr_excess":     cum_lr   / cum_bench,
    })
    nav_df.index = nav_df.index.strftime("%Y-%m-%d")
    nav_df.to_csv("outputs/nav_curve_qlib.csv")
    ic_table.to_csv("outputs/ic_analysis_qlib.csv")
    print(f"\n净值曲线已保存 → outputs/nav_curve_qlib.csv")
    print(f"IC 分析已保存 → outputs/ic_analysis_qlib.csv")

    # ─────────────────────────────────────────────────────
    # 附：两种实现的核心差异对照
    # ─────────────────────────────────────────────────────
    section("附：两种实现方式核心差异对照")
    print("""
  环节              from_scratch 版本                    Qlib 版本
  ─────────────────────────────────────────────────────────────────
  特征定义          pandas rolling 手写                  Qlib 表达式引擎
                    close / close.shift(5) - 1           $close / Ref($close,5) - 1

  预处理            手写 winsorize_cs + zscore_cs         CSZScoreNorm(robust) + CSZScoreNorm()
                    两步显式可见                          两步 Processor，接近但不完全等价
                                                         （clip 前中心点略有差异）

  模型训练          sklearn LinearRegression.fit(X, y)   LinearModel(estimator='ols').fit(dataset)
                    显式传矩阵                            dataset 内部 prepare

  IC / ICIR 分析    手写 spearmanr 循环                   手写 spearmanr 循环（完全相同）

  回测              手写 run_backtest()                   手写 run_backtest()（完全相同）

  绩效指标          calc_performance()，252 日年化         calc_performance()，252 日年化（完全相同）

  结果存储          outputs/nav_curve.csv                outputs/nav_curve_qlib.csv
  ─────────────────────────────────────────────────────────────────
  预期残余差异：
    - IC 值：预处理差异（MAD clip 前中心点）可能导致因子值略有不同，IC 接近但不完全一致
    - LR 系数：Qlib LinearModel 与 sklearn LR 的数值实现相同，差异来自预处理输入不同
    - 回测结果：若 IC 分析筛选的 valid_factors 相同，回测结果应完全一致
    """)
