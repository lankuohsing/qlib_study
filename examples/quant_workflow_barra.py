"""
量化投资全流程学习脚本（Barra 组合优化版）
================================================
在 quant_workflow_from_scratch.py 基础上，将 Step 6 的
"Top-K 等权" 替换为 "Barra 风险模型 + 均值-方差组合优化"。

改动点：
  Step 6  组合构建  ← 唯一改动处
    原版：每日取 Top-30，等权持有
    本版：用 clean_df 里的 7 个标准化因子作为风格暴露（Barra B 矩阵），
          估计因子协方差矩阵 F 和特异风险 D，
          合成协方差矩阵 Σ = B×F×B^T + D，
          用 cvxpy 做均值-方差优化，加单票上限和风格中性约束。

Step 1~5 与原脚本完全相同，Step 7 回测逻辑也完全相同（只是持仓从
list 变成了 dict{stock: weight}，收益计算改为加权求和）。

依赖：pip install cvxpy
"""

import os
import warnings

warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.linear_model import LinearRegression
import cvxpy as cp

import qlib
from qlib.constant import REG_CN
from qlib.data import D

# ─────────────────────────────────────────────────────────
# 全局配置（与原脚本完全相同）
# ─────────────────────────────────────────────────────────
PROVIDER_URI = "/Users/guoxing.lan/projects/github/qlib_study/datasets/cn_data"
UNIVERSE = "csi300"

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

# Barra 优化新增参数
LAMBDA       = 2.0   # 风险厌恶系数：越大越保守，越小越激进
MAX_WEIGHT   = 0.05  # 单票权重上限（5%）
STYLE_LIMIT  = 0.5   # 组合风格暴露上限（相对市场平均偏离不超过 ±0.5 个标准差）

# ─────────────────────────────────────────────────────────
# 辅助函数（与原脚本完全相同）
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


def calc_performance(series, ret_df_ref=None):
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


# ─────────────────────────────────────────────────────────
# Barra 新增函数
# ─────────────────────────────────────────────────────────
def estimate_factor_cov(train_df, factor_cols):
    """
    用训练集估计因子协方差矩阵 F。

    做法：对每天做截面回归，反推因子收益率时间序列，
    再对时间序列算协方差矩阵。

    截面回归：r_i = B_i × f + ε
      已知：每只股票今天的收益率 r（标签 LABEL）
      已知：每只股票今天的因子暴露 B（7 个标准化因子值）
      求解：因子今天的收益率 f（7 个数）
    """
    factor_returns = []
    for date, grp in train_df.groupby(level="datetime"):
        day = grp.xs(date, level="datetime")
        valid = day[factor_cols + ["LABEL"]].dropna()
        if len(valid) < 20:
            continue
        X = valid[factor_cols].values   # (股票数, 因子数)
        y = valid["LABEL"].values       # (股票数,)
        reg = LinearRegression(fit_intercept=False).fit(X, y)
        factor_returns.append(reg.coef_)

    factor_returns_df = pd.DataFrame(factor_returns, columns=factor_cols)
    F = factor_returns_df.cov().values  # (因子数, 因子数)
    return F


def estimate_specific_risk(train_df, factor_cols, F):
    """
    用训练集估计每只股票的特异风险（残差方差）。

    特异收益 = 实际收益 - 因子能解释的部分
    D[i,i]   = var(特异收益_i)
    """
    # 先用训练集的平均因子收益率（近似）来算特异收益
    # 更精确的做法是逐日回归，这里用简化版：
    # 特异收益方差 ≈ 总收益方差 - 因子解释的方差
    stocks = train_df.index.get_level_values("instrument").unique()
    specific_vars = {}

    for stock in stocks:
        try:
            stock_data = train_df.xs(stock, level="instrument")[factor_cols + ["LABEL"]].dropna()
            if len(stock_data) < 20:
                specific_vars[stock] = stock_data["LABEL"].var() if len(stock_data) > 1 else 1e-4
                continue
            X = stock_data[factor_cols].values
            y = stock_data["LABEL"].values
            reg = LinearRegression().fit(X, y)
            residuals = y - reg.predict(X)
            specific_vars[stock] = np.var(residuals) if len(residuals) > 1 else 1e-4
        except Exception:
            specific_vars[stock] = 1e-4

    return specific_vars


def barra_optimize(mu_series, B_df, F, specific_vars, lambda_=2.0,
                   max_weight=0.05, style_limit=0.5):
    """
    均值-方差组合优化（Barra 版）。

    参数：
      mu_series   : 每只股票的预期收益（等权合成得分），Series
      B_df        : 因子暴露矩阵，DataFrame，行=股票，列=因子
      F           : 因子协方差矩阵，ndarray (k, k)
      specific_vars: 每只股票的特异方差，dict
      lambda_     : 风险厌恶系数
      max_weight  : 单票权重上限
      style_limit : 组合风格暴露上限（±style_limit 个标准差）

    返回：
      weights_series: 每只股票的最优权重，Series
    """
    # 对齐：只保留 mu 和 B 都有数据的股票
    common = mu_series.index.intersection(B_df.index)
    if len(common) < 5:
        # 股票太少，退化为等权
        return pd.Series(1.0 / len(mu_series), index=mu_series.index)

    mu = mu_series.loc[common].values                    # (n,)
    B  = B_df.loc[common].values                         # (n, k)
    d  = np.array([specific_vars.get(s, 1e-4) for s in common])  # (n,) 特异方差
    D  = np.diag(d)                                      # (n, n)

    # 协方差矩阵 Σ = B×F×B^T + D
    Sigma = B @ F @ B.T + D

    n = len(common)
    w = cp.Variable(n)

    # 目标：最大化 预期收益 - λ×风险
    portfolio_variance = cp.quad_form(w, Sigma)
    objective = cp.Maximize(w @ mu - lambda_ * portfolio_variance)

    constraints = [
        cp.sum(w) == 1,      # 满仓
        w >= 0,              # 不做空
        w <= max_weight,     # 单票上限
        # 风格中性：组合对每个因子的总暴露不超过 ±style_limit
        cp.abs(B.T @ w) <= style_limit,
    ]

    prob = cp.Problem(objective, constraints)
    try:
        prob.solve(solver=cp.CLARABEL, verbose=False)
    except Exception:
        prob.solve(verbose=False)

    if w.value is None or prob.status not in ["optimal", "optimal_inaccurate"]:
        # 求解失败，退化为等权
        eq_w = np.ones(n) / n
        return pd.Series(eq_w, index=common)

    return pd.Series(w.value, index=common)


if __name__ == "__main__":

    os.makedirs("outputs", exist_ok=True)

    # ─────────────────────────────────────────────────────
    # Step 1~5：与原脚本完全相同
    # ─────────────────────────────────────────────────────
    section("Step 1  数据加载")
    qlib.init(provider_uri=PROVIDER_URI, region=REG_CN)

    membership_dict = D.list_instruments(
        D.instruments(UNIVERSE),
        start_time=DATA_START, end_time=TEST_END,
        freq="day", as_list=False,
    )
    all_stocks = list(membership_dict.keys())

    raw_df = D.features(
        all_stocks,
        fields=["$open", "$high", "$low", "$close", "$volume"],
        start_time=DATA_START, end_time=TEST_END,
        freq="day",
    )
    raw_df.columns = ["open", "high", "low", "close", "volume"]
    raw_df.index.names = ["instrument", "datetime"]
    raw_df = raw_df.swaplevel().sort_index()

    n_days   = raw_df.index.get_level_values("datetime").nunique()
    n_stocks = raw_df.index.get_level_values("instrument").nunique()
    print(f"原始行情 shape={raw_df.shape}  {n_days} 交易日 × {n_stocks} 股票")

    section("Step 2  因子计算")
    close  = raw_df["close"].unstack("instrument")
    high   = raw_df["high"].unstack("instrument")
    low    = raw_df["low"].unstack("instrument")
    volume = raw_df["volume"].unstack("instrument")

    daily_return = close.pct_change()
    MOM_5D    = close / close.shift(5)  - 1
    MOM_20D   = close / close.shift(20) - 1
    VOL_20D   = daily_return.rolling(20).std()
    TURN_5D   = volume / volume.rolling(5).mean()
    MA_DEV    = close / close.rolling(20).mean() - 1
    DAY_RANGE = (high - low) / close.shift(1)
    PRICE_POS = (close - low) / (high - low + 1e-9)
    LABEL     = close.shift(-1) / close - 1

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
    print(f"因子长表 shape={factor_df.shape}")

    section("Step 3  因子预处理")
    processed = factor_df.copy()
    processed[FACTOR_COLS] = processed.groupby(level="datetime")[FACTOR_COLS].transform(winsorize_cs)
    processed[FACTOR_COLS] = processed.groupby(level="datetime")[FACTOR_COLS].transform(zscore_cs)
    clean_df = processed.dropna()

    dt  = clean_df.index.get_level_values("datetime")
    ins = clean_df.index.get_level_values("instrument")
    in_universe = pd.Series(False, index=clean_df.index)
    for stock, spans in membership_dict.items():
        for start, end in spans:
            in_universe |= (ins == stock) & (dt >= start) & (dt <= end)
    clean_df = clean_df[in_universe]

    train_df = segment(clean_df, TRAIN_START, TRAIN_END)
    valid_df = segment(clean_df, VALID_START, VALID_END)
    test_df  = segment(clean_df, TEST_START,  TEST_END)
    print(f"train={train_df.shape}  valid={valid_df.shape}  test={test_df.shape}")

    section("Step 4  因子有效性分析（IC / ICIR）")
    ic_records = {}
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
            "IC均值": round(s.mean(), 4),
            "ICIR":   round(s.mean() / (s.std() + 1e-9), 4),
        }
    ic_table = pd.DataFrame(ic_records).T
    print(ic_table.to_string())

    valid_factors = ic_table[ic_table["IC均值"].abs() > 0.02].index.tolist()
    if not valid_factors:
        valid_factors = FACTOR_COLS

    section("Step 5  因子合成")
    test_score_ew = score_equal_weight(test_df, valid_factors)
    test_score    = test_score_ew
    print(f"等权合成得分 shape={test_score.shape}")

    # ─────────────────────────────────────────────────────
    # Step 6  组合构建（Barra 均值-方差优化）← 唯一改动
    # ─────────────────────────────────────────────────────
    section("Step 6  组合构建（Barra 均值-方差优化）")

    print("── Barra 风险模型估计 ──")
    print("Step 6-1：用训练集截面回归估计因子协方差矩阵 F ...")
    F = estimate_factor_cov(train_df, FACTOR_COLS)
    print(f"  因子协方差矩阵 F  shape={F.shape}")

    print("Step 6-2：估计每只股票的特异风险 D ...")
    specific_vars = estimate_specific_risk(train_df, FACTOR_COLS, F)
    print(f"  特异风险估计完成，共 {len(specific_vars)} 只股票")

    print("\n── 逐日优化 ──")
    print(f"  风险厌恶系数 λ={LAMBDA}，单票上限={MAX_WEIGHT:.0%}，风格暴露上限=±{STYLE_LIMIT}")

    # holdings_w：每天的持仓权重字典 {stock: weight}（原版是 list，这里改为 dict）
    holdings_w = {}
    test_dates = sorted(test_df.index.get_level_values("datetime").unique())

    for i, date in enumerate(test_dates):
        # 当天的预期收益（合成得分）
        if date not in test_score.index.get_level_values("datetime"):
            continue
        mu_today = test_score.xs(date, level="datetime")

        # 当天的因子暴露矩阵（7 个标准化因子值）
        B_today = test_df.xs(date, level="datetime")[FACTOR_COLS].dropna()

        # 求解最优权重
        w = barra_optimize(
            mu_series=mu_today,
            B_df=B_today,
            F=F,
            specific_vars=specific_vars,
            lambda_=LAMBDA,
            max_weight=MAX_WEIGHT,
            style_limit=STYLE_LIMIT,
        )
        holdings_w[date] = w

        if i % 60 == 0:
            n_held = (w > 1e-4).sum()
            print(f"  {date.date()}  有效持仓={n_held} 只  "
                  f"最大权重={w.max():.2%}  最小权重={w[w>1e-4].min():.2%}")

    print(f"\n  优化完成，共 {len(holdings_w)} 个交易日")

    # 简单统计：平均持仓只数（权重 > 0.1% 算持仓）
    avg_n = np.mean([(w > 0.001).sum() for w in holdings_w.values()])
    print(f"  平均有效持仓数: {avg_n:.1f} 只（权重 > 0.1%）")

    # 与原版对比打印：某天的权重分布
    sample_date = test_dates[0]
    w_sample = holdings_w[sample_date].sort_values(ascending=False)
    print(f"\n  示例 {sample_date.date()} 权重分布（Top 5）:")
    for stock, wt in w_sample.head(5).items():
        print(f"    {stock}  {wt:.2%}")

    # ─────────────────────────────────────────────────────
    # Step 7  回测（与原脚本逻辑相同，收益改为加权求和）
    # ─────────────────────────────────────────────────────
    section("Step 7  回测（含交易成本，对比等权基准）")

    test_ret_wide = close.loc[TEST_START:TEST_END].pct_change()

    print("回测逻辑：")
    print("  T 收盘 → 用 T 日因子优化权重 → 得到 holdings_w[T]")
    print("  T+1 收盘实现收益 = sum(holdings_w[T][stock] × T+1日涨跌幅)")
    print("  每次调仓按权重变化量扣除手续费\n")

    dates    = sorted(holdings_w.keys())
    records  = []
    prev_w   = pd.Series(dtype=float)  # 上一期权重（空 Series 表示第一天）

    for date_T in dates:
        curr_w = holdings_w[date_T]

        # 用上一期权重计算今天的收益（T-1 日持仓 → T 日变现）
        if len(prev_w) > 0:
            ret_w = prev_w
        else:
            ret_w = curr_w

        if date_T in test_ret_wide.index:
            valid_stocks = [s for s in ret_w.index if s in test_ret_wide.columns]
            day_rets     = test_ret_wide.loc[date_T, valid_stocks].dropna()
            # 加权收益
            common       = ret_w.index.intersection(day_rets.index)
            w_aligned    = ret_w.loc[common]
            w_aligned    = w_aligned / w_aligned.sum() if w_aligned.sum() > 0 else w_aligned
            port_ret     = (w_aligned * day_rets.loc[common]).sum()
        else:
            port_ret = 0.0

        # 换手率：权重变化量之和的一半
        all_stocks_union = curr_w.index.union(prev_w.index) if len(prev_w) > 0 else curr_w.index
        w_curr_full = curr_w.reindex(all_stocks_union).fillna(0)
        w_prev_full = prev_w.reindex(all_stocks_union).fillna(0) if len(prev_w) > 0 else pd.Series(0.0, index=all_stocks_union)
        turnover    = (w_curr_full - w_prev_full).abs().sum() / 2

        net_ret = port_ret - turnover * TRANSACTION_COST
        records.append({"date": date_T, "gross_ret": port_ret, "turnover": turnover, "net_ret": net_ret})
        prev_w = curr_w

    ret_df = pd.DataFrame(records).set_index("date")

    print(f"策略日收益序列  shape={ret_df.shape}")
    print(f"  列含义: gross_ret 税前收益 | turnover 换手率 | net_ret 扣费后收益")
    print(ret_df.head(8).round(5))

    benchmark_ret = test_ret_wide.mean(axis=1).reindex(ret_df.index).fillna(0)

    p_strat = calc_performance(ret_df["net_ret"])
    p_bench = calc_performance(benchmark_ret)

    avg_turnover = ret_df["turnover"].mean()
    print(f"\n平均换手率: {avg_turnover:.1%}")
    print(f"单边手续费: {TRANSACTION_COST:.1%}")

    print(f"\n{'─'*55}")
    print(f"  绩效对比（test 期间 {TEST_START} ~ {TEST_END}）")
    print(f"{'─'*55}")
    print(f"  {'指标':<10}  {'策略（Barra优化 + 手续费）':>24}  {'基准（等权均值）':>16}")
    print(f"{'─'*55}")
    for k in p_strat:
        print(f"  {k:<10}  {p_strat[k]:>24}  {p_bench[k]:>16}")
    print(f"{'─'*55}")

    cum_strat = (1 + ret_df["net_ret"]).cumprod()
    cum_bench = (1 + benchmark_ret).cumprod()
    nav_df = pd.DataFrame({
        "strategy_nav": cum_strat,
        "benchmark_nav": cum_bench,
        "excess_nav":    cum_strat / cum_bench,
    })
    nav_df.index = nav_df.index.strftime("%Y-%m-%d")
    nav_df.to_csv("outputs/nav_curve_barra.csv")
    print(f"\n净值曲线已保存 → outputs/nav_curve_barra.csv")

    ic_table.to_csv("outputs/ic_analysis_barra.csv")
