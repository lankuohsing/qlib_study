"""
量化投资全流程学习脚本
================================================
从原始 OHLCV 出发，手工走完完整量化链路：

  Step 1  数据加载        用 Qlib D.features 读取原始行情 .bin 文件
  Step 2  因子计算        从 OHLCV 手工推导 7 个基础因子
  Step 3  因子预处理      每日截面：去极值（MAD）→ Z-Score 标准化
  Step 4  因子有效性分析  IC / ICIR 检验（Spearman 相关）
  Step 5  因子合成        (A) 等权平均  (B) LinearRegression 学习权重
  Step 6  组合构建        每日按合成得分取 Top-K 只股票
  Step 7  回测            逐日模拟，计算含手续费收益，对比等权基准

Step 6+7 对等权（EW）和线性回归（LR）两种合成方式分别执行，最终并排对比绩效。
每个步骤均打印数据 shape，方便形象理解数据流动。
"""

import os
import warnings

warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.linear_model import LinearRegression

import qlib
from qlib.constant import REG_CN
from qlib.data import D

# ─────────────────────────────────────────────────────────
# 全局配置
# ─────────────────────────────────────────────────────────
PROVIDER_URI = "/Users/guoxing.lan/projects/github/qlib_study/datasets/cn_data"
UNIVERSE = "csi300"  # 股票池，沪深300指数成分股；（每半年更新一次）

TRAIN_START = "2015-01-01"
TRAIN_END   = "2017-12-31"
VALID_START = "2018-01-01"
VALID_END   = "2018-12-31"
TEST_START  = "2019-01-01"
TEST_END    = "2020-08-01"
DATA_START  = "2014-06-01"  # 比 TRAIN_START 早约 6 个月，为 rolling 窗口预热

TOPK             = 30
TRANSACTION_COST = 0.001

FACTOR_COLS = ["MOM_5D", "MOM_20D", "VOL_20D", "TURN_5D", "MA_DEV", "DAY_RANGE", "PRICE_POS"]

# ─────────────────────────────────────────────────────────
# 辅助函数（模块顶层，子进程重新导入时也能访问）
# ─────────────────────────────────────────────────────────
def section(title):
    print(f"\n{'=' * 65}")
    print(f"  {title}")
    print(f"{'=' * 65}")


def winsorize_cs(s, n_sigma=3):
    """MAD 法截面去极值：将极端值 clip 到 ±3×1.4826×MAD 范围内（等价于正态分布下 ±3σ）"""
    median = s.median()
    mad    = (s - median).abs().median()
    lo     = median - n_sigma * 1.4826 * mad
    hi     = median + n_sigma * 1.4826 * mad
    return s.clip(lo, hi)


def zscore_cs(s):
    """截面 Z-Score：均值 0，标准差 1"""
    return (s - s.mean()) / (s.std() + 1e-9)


def segment(df, start, end):
    d = df.index.get_level_values("datetime")
    return df[(d >= start) & (d <= end)]


def score_equal_weight(df, factors):
    return df[factors].mean(axis=1)


def score_lr(df, factors, model):
    X = df[factors].dropna()
    return pd.Series(model.predict(X.values), index=X.index)


def calc_performance(series):
    cum     = (1 + series).cumprod()# 把日收益率转成净值曲线。
    n       = len(series)# 总交易日数，用于年化换算。
    ann_ret = cum.iloc[-1] ** (252 / n) - 1# 年化收益率。
    ann_vol = series.std() * np.sqrt(252)# 年化波动率。
    sharpe  = ann_ret / (ann_vol + 1e-9)# 夏普比率 = 年化收益 ÷ 年化波动。
    max_dd  = ((cum - cum.cummax()) / cum.cummax()).min()# 最大回撤
    cum_ret = cum.iloc[-1] - 1# 累计收益率，即整个测试期的总涨跌幅。
    return {
        "年化收益": f"{ann_ret:.2%}",
        "年化波动": f"{ann_vol:.2%}",
        "夏普比率": f"{sharpe:.2f}",
        "最大回撤": f"{max_dd:.2%}",
        "累计收益": f"{cum_ret:.2%}",
    }


def run_backtest(test_score, test_ret_wide):
    """
    Step 6+7 合并：给定合成得分，走完组合构建和回测，返回日收益 DataFrame。

    test_score    : 测试期合成得分 Series，索引 = (datetime, instrument)
    test_ret_wide : 测试期日收益率宽表，shape = (交易日, 股票数)
    返回          : DataFrame，列为 gross_ret / turnover / net_ret，索引为日期
    """
    # Step 6：每天取合成得分最高的 TOPK 只股票
    holdings = {}
    for date, grp in test_score.groupby(level="datetime"):
        top_stocks = grp.xs(date, level="datetime").nlargest(TOPK).index.tolist()
        holdings[date] = top_stocks

    # Step 7：逐日模拟收益
    records  = []
    prev_set = set()  # 上一天的持仓集合，初始为空

    for date_T in sorted(holdings.keys()):
        curr_set   = set(holdings[date_T])   # T 日收盘因子选出的新持仓
        ret_stocks = list(prev_set) if prev_set else []  # 今天实际持有的是昨天选出的股票

        if date_T in test_ret_wide.index:
            valid_stocks = [s for s in ret_stocks if s in test_ret_wide.columns]
            day_rets     = test_ret_wide.loc[date_T, valid_stocks].dropna()
            # 等权持有，每只股票资金相同，组合收益 = 各股票涨跌幅的算术平均
            port_ret     = day_rets.mean() if len(day_rets) > 0 else 0.0
        else:
            port_ret = 0.0

        # 换手率 = 调仓只数 / (2 × TOPK)；第一天全仓建仓，换手率 = 1
        turnover = len(curr_set.symmetric_difference(prev_set)) / (2 * TOPK) if prev_set else 1.0
        net_ret  = port_ret - turnover * TRANSACTION_COST

        records.append({"date": date_T, "gross_ret": port_ret, "turnover": turnover, "net_ret": net_ret})
        prev_set = curr_set

    return pd.DataFrame(records).set_index("date")


# ─────────────────────────────────────────────────────────
# macOS 上 Python 使用 spawn 启动子进程，子进程会重新导入本模块。
# D.features 内部通过 joblib 并行加载数据，因此必须将所有执行代码
# 放在 if __name__ == '__main__': 保护内，防止递归 spawn。
# ─────────────────────────────────────────────────────────
if __name__ == "__main__":

    os.makedirs("outputs", exist_ok=True)

    # ─────────────────────────────────────────────────────
    # Step 1  数据加载
    # ─────────────────────────────────────────────────────
    section("Step 1  数据加载")
    # 要求已经将数据下载并解压到了PROVIDER_URI； https://github.com/microsoft/qlib#data-preparation
    qlib.init(provider_uri=PROVIDER_URI, region=REG_CN)

    # 两步加载，修复刚入选股票因子 NaN 问题：
    # 若直接传 "csi300" 字符串，Qlib 内部会把每只股票的数据裁剪到其成员资格区间，
    # 导致 shift()/rolling() 在入选前的历史窗口上产生虚假 NaN。
    # 改为先取成员列表（保留 spans 供后续过滤），再用纯列表加载完整历史。
    membership_dict = D.list_instruments(  # 记录了每只股票曾经入选 CSI 300 的时间段
        D.instruments(UNIVERSE),  # 构造"CSI 300 股票池"的描述对象，此时还没有真正查数据
        start_time=DATA_START,
        end_time=TEST_END,
        freq="day",
        as_list=False,          # 保留 {stock: [(start, end), ...]}，后续 universe 过滤用
    )
    all_stocks = list(membership_dict.keys())

    # 传入纯列表 → Qlib 内部 spans=None → 不做成员资格裁剪，返回完整历史
    raw_df = D.features(
        all_stocks,
        fields=["$open", "$high", "$low", "$close", "$volume"],
        start_time=DATA_START,
        end_time=TEST_END,
        freq="day",
    )
    raw_df.columns = ["open", "high", "low", "close", "volume"]  # 重命名列为原始特征名
    raw_df.index.names = ["instrument", "datetime"]  # 标的代码和日期
    # 将索引由 ["instrument", "datetime"] 交换为 ["datetime", "instrument"] 并排序；
    # 排序保证后续 xs()、loc[] 等标签操作的正确性（不只是性能）
    raw_df = raw_df.swaplevel().sort_index()

    n_days   = raw_df.index.get_level_values("datetime").nunique()
    n_stocks = raw_df.index.get_level_values("instrument").nunique()

    print(f"原始行情 DataFrame  shape = {raw_df.shape}")
    print(f"  索引层级: {raw_df.index.names}")
    print(f"  交易日数: {n_days}  |  股票数: {n_stocks}")
    print(f"  日期范围: {raw_df.index.get_level_values('datetime').min().date()} ~ "
          f"{raw_df.index.get_level_values('datetime').max().date()}")
    print(f"  列: {list(raw_df.columns)}")
    print(raw_df.head(3))

    # ─────────────────────────────────────────────────────
    # Step 2  因子计算
    # ─────────────────────────────────────────────────────
    section("Step 2  因子计算（从 OHLCV 手工推导 7 个基础因子）")
    # 将 raw_df 中的各列转换为宽表，其中 instrument 是列，datetime 是行；
    # 宽表上的 shift()/rolling() 可对所有股票并行计算，比 groupby+apply 快很多
    close  = raw_df["close"].unstack("instrument")
    high   = raw_df["high"].unstack("instrument")
    low    = raw_df["low"].unstack("instrument")
    volume = raw_df["volume"].unstack("instrument")

    print(f"宽表 close  shape = {close.shape}  ({close.shape[0]} 交易日 × {close.shape[1]} 股票)")
    print("（每个字段都是同样形状的宽表，以下因子计算均在宽表上进行）\n")

    daily_return = close.pct_change()  # 每只股票每天相对前一天收盘价的涨跌幅

    # 因子名        计算公式                            经济含义
    # MOM_5D        close[t] / close[t-5]  - 1          5 日动量（短期趋势）
    # MOM_20D       close[t] / close[t-20] - 1          20 日动量（中期趋势）
    # VOL_20D       Std(daily_return, 20)                20 日波动率
    # TURN_5D       volume[t] / Mean(volume, 5)          5 日量比
    # MA_DEV        close[t] / Mean(close, 20) - 1       偏离 20 日均线
    # DAY_RANGE     (high - low) / close[t-1]            当日振幅
    # PRICE_POS     (close - low) / (high - low)         收盘在高低区间位置
    MOM_5D    = close / close.shift(5)  - 1
    MOM_20D   = close / close.shift(20) - 1
    VOL_20D   = daily_return.rolling(20).std()   # 最近20个交易日（含当日）窗口内的日收益率标准差
    TURN_5D   = volume / volume.rolling(5).mean()  # 当日成交量 / 近5日平均成交量
    MA_DEV    = close / close.rolling(20).mean() - 1  # 收盘价偏离20日均线的百分比
    DAY_RANGE = (high - low) / close.shift(1)  # 当日振幅：(最高价 - 最低价) / 昨日收盘价
    PRICE_POS = (close - low) / (high - low + 1e-9)  # 收盘价在当日高低区间内的相对位置（0~1）
    LABEL     = close.shift(-1) / close - 1  # 次日收益率，作为模型的预测目标

    print("因子宽表 shape（每个均与 close 相同）:")
    for name, arr in [("MOM_5D", MOM_5D), ("MOM_20D", MOM_20D), ("VOL_20D", VOL_20D),
                      ("TURN_5D", TURN_5D), ("MA_DEV", MA_DEV), ("DAY_RANGE", DAY_RANGE),
                      ("PRICE_POS", PRICE_POS), ("LABEL", LABEL)]:
        nan_pct = arr.isna().mean().mean() * 100
        print(f"  {name:<12}  shape={arr.shape}  NaN占比={nan_pct:.1f}%")

    # 将 8 张宽表合并为长表：每行 = 一只股票在某一天的 7 个因子值 + 1 个标签
    factor_df = pd.concat({
        "MOM_5D":    MOM_5D.stack(future_stack=True),
        "MOM_20D":   MOM_20D.stack(future_stack=True),
        "VOL_20D":   VOL_20D.stack(future_stack=True),
        "TURN_5D":   TURN_5D.stack(future_stack=True),
        "MA_DEV":    MA_DEV.stack(future_stack=True),
        "DAY_RANGE": DAY_RANGE.stack(future_stack=True),
        "PRICE_POS": PRICE_POS.stack(future_stack=True),
        "LABEL":     LABEL.stack(future_stack=True),
    }, axis=1)
    factor_df.index.names = ["datetime", "instrument"]

    print(f"\n因子数据（long format）shape = {factor_df.shape}")
    print(f"  每行含义：一只股票在某个交易日的 7 个因子值 + 1 个 label")
    print(f"  总行数 ≈ {close.shape[0]} 交易日 × {close.shape[1]} 股票 = {close.shape[0] * close.shape[1]}"
          f"（含 NaN 未删除）")

    # ─────────────────────────────────────────────────────
    # Step 3  因子预处理（截面标准化）
    # ─────────────────────────────────────────────────────
    section("Step 3  因子预处理（每日截面：去极值 → Z-Score）")

    print("为什么要截面标准化？")
    print("  不同因子量纲不同（动量≈0.1，量比≈1~3），不能直接比较或加权。")
    print("  截面操作：对同一天所有股票的因子值统一处理，消除日间差异。\n")

    processed = factor_df.copy()
    processed[FACTOR_COLS] = (
        processed.groupby(level="datetime")[FACTOR_COLS].transform(winsorize_cs)
    )
    processed[FACTOR_COLS] = (
        processed.groupby(level="datetime")[FACTOR_COLS].transform(zscore_cs)
    )

    clean_df = processed.dropna()

    # 只保留"当天真正在 CSI300"的行：因子用完整历史算，但选股/回测只考虑指数成员
    dt  = clean_df.index.get_level_values("datetime")
    ins = clean_df.index.get_level_values("instrument")
    in_universe = pd.Series(False, index=clean_df.index)
    for stock, spans in membership_dict.items():
        for start, end in spans:
            in_universe |= (ins == stock) & (dt >= start) & (dt <= end)
    clean_df = clean_df[in_universe]

    print(f"处理前  shape = {factor_df.shape}  NaN 行数 = {factor_df.isna().any(axis=1).sum()}")
    print(f"处理后  shape = {clean_df.shape}   丢弃了 {len(factor_df) - len(clean_df)} 行")
    print(f"  首个有效日期: {clean_df.index.get_level_values('datetime').min().date()}")
    print(f"  末个有效日期: {clean_df.index.get_level_values('datetime').max().date()}")

    train_df = segment(clean_df, TRAIN_START, TRAIN_END)
    valid_df = segment(clean_df, VALID_START, VALID_END)
    test_df  = segment(clean_df, TEST_START,  TEST_END)

    print(f"\n数据三段切分:")
    for name, df, s, e in [("train", train_df, TRAIN_START, TRAIN_END),
                            ("valid", valid_df, VALID_START, VALID_END),
                            ("test",  test_df,  TEST_START,  TEST_END)]:
        n_d = df.index.get_level_values("datetime").nunique()
        n_s = df.index.get_level_values("instrument").nunique()
        print(f"  {name} ({s} ~ {e})  shape={df.shape}"
              f"  → {n_d} 交易日 × ~{n_s} 股票/日")

    # ─────────────────────────────────────────────────────
    # Step 4  因子有效性分析（IC / ICIR）
    # ─────────────────────────────────────────────────────
    section("Step 4  因子有效性分析（IC / ICIR）")

    print("IC  = Spearman(因子值, 次日收益率)，在每日截面计算，再对时序取统计")
    print("ICIR = IC均值 / IC标准差")
    print("经验阈值：|IC均值| > 0.03 认为有预测力；ICIR > 0.5 认为稳定\n")

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
            "ICIR":     round(s.mean() / (s.std() + 1e-9), 4),  # IC均值/IC标准差，类似夏普比率，衡量预测力的稳定性
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
        print("\n未筛选到显著有效因子（阈值 0.02），改为使用全部因子")
        valid_factors = FACTOR_COLS

    # ─────────────────────────────────────────────────────
    # Step 5  因子合成
    # ─────────────────────────────────────────────────────
    section("Step 5  因子合成（等权 vs LinearRegression）")

    print("── 方式 A：对有效因子等权平均 ──")
    print(f"  参与合成的因子: {valid_factors}")

    # 注意：这里没有按训练集 IC 符号对因子方向做校正。
    # 原因：IC 符号在不同市场 regime 下可能翻转（如训练期均值回归、测试期趋势延续），
    # 用训练集 IC 符号固定校正测试期会引入隐性的 regime 假设，反而使结果更差。
    # 更稳健的做法是滚动估计 IC 符号，但会增加复杂度，超出本脚本范围。
    train_score_ew = score_equal_weight(train_df, valid_factors)
    valid_score_ew = score_equal_weight(valid_df, valid_factors)
    test_score_ew  = score_equal_weight(test_df,  valid_factors)

    print(f"  合成得分 shape  train={train_score_ew.shape}  valid={valid_score_ew.shape}  test={test_score_ew.shape}")
    print(f"  含义：每行是一只股票在某交易日的综合得分（分越高越可能上涨）")

    ew_ic_list = []  # 等权合成在训练集上每天的 IC 值
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

    print("\n── 方式 B：LinearRegression（学习各因子对未来收益的权重）──")

    train_xy = train_df[valid_factors + ["LABEL"]].dropna()
    X_tr     = train_xy[valid_factors].values
    y_tr     = train_xy["LABEL"].values

    print(f"  回归输入  X shape={X_tr.shape}（样本数 × 因子数）  y shape={y_tr.shape}")

    lr = LinearRegression().fit(X_tr, y_tr)

    coef_df = pd.Series(dict(zip(valid_factors, lr.coef_))).round(6)
    print(f"  学到的因子权重（回归系数）:")
    for fname, coef in coef_df.items():
        print(f"    {fname:<12} : {coef:+.6f}")
    print(f"  截距: {lr.intercept_:.6f}")
    print(f"  训练集 R²={lr.score(X_tr, y_tr):.5f}")
    print("  （量化信号 R² 通常 < 0.01，说明股票涨跌难以预测，属正常现象）")

    test_score_lr = score_lr(test_df, valid_factors, lr)
    print(f"\n  LR 合成得分 shape (test): {test_score_lr.shape}")

    print("\n── 在 valid 集上对比两种合成方式的 IC ──")
    for method_name, get_score in [
        ("等权EW", lambda: valid_score_ew),
        ("LR",     lambda: score_lr(valid_df, valid_factors, lr)),
    ]:
        s = get_score()
        ic_vals = []
        for date, grp in valid_df.groupby(level="datetime"):
            if date not in s.index.get_level_values("datetime"):
                continue
            sc = s.xs(date, level="datetime")          # 当天所有股票的合成得分
            lb = grp["LABEL"].xs(date, level="datetime").dropna()  # 次日收益率
            cm = sc.index.intersection(lb.index)
            if len(cm) < 10:
                continue
            ic_val, _ = stats.spearmanr(sc.loc[cm], lb.loc[cm])
            ic_vals.append(ic_val)
        iv = pd.Series(ic_vals)
        print(f"  {method_name:<8}  IC均值={iv.mean():.4f}  ICIR={iv.mean()/(iv.std()+1e-9):.4f}")

    # ─────────────────────────────────────────────────────
    # Step 6+7  组合构建 + 回测（EW 和 LR 各走一遍）
    # ─────────────────────────────────────────────────────
    test_ret_wide = close.loc[TEST_START:TEST_END].pct_change()# 每只股票每天相对前一日收盘价的涨跌幅
    print(f"\n日收益率宽表  shape = {test_ret_wide.shape}  ({test_ret_wide.shape[0]} 交易日 × {test_ret_wide.shape[1]} 股票)")
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
    section("绩效汇总对比")

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

    # 保存净值曲线（含两种策略）
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
    nav_df.to_csv("outputs/nav_curve.csv")
    print(f"\n净值曲线已保存 → outputs/nav_curve.csv  shape={nav_df.shape}")

    ic_table.to_csv("outputs/ic_analysis.csv")
    print(f"IC 分析已保存 → outputs/ic_analysis.csv")
