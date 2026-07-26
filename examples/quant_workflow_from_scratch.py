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
PROVIDER_URI = r"/Users/guoxing.lan/projects/github/qlib_study/datasets/cn_data"
UNIVERSE = "csi300"  # 股票池，沪深300指数成分股；（每半年更新一次）

TRAIN_START = "2010-01-01"
TRAIN_END   = "2014-12-31"
VALID_START = "2015-01-01"
VALID_END   = "2017-12-31"
TEST_START  = "2018-01-01"
TEST_END    = "2019-06-01"
DATA_START  = "2009-06-01"  # 比 TRAIN_START 早约 6 个月，为 rolling 窗口预热

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


def build_membership_by_date(membership_dict, dates):
    """将 Qlib 的 {stock: [(start, end), ...]} 转为 {date: set(stocks)}。

    因子预处理需要按信号日过滤成分股，而交易模拟还需要在
    T+1 开盘真正下单时再校验一次当日成分资格。
    """
    dates = pd.DatetimeIndex(dates).sort_values().unique()
    result = {date: set() for date in dates}
    for stock, spans in membership_dict.items():
        for start, end in spans:
            active_dates = dates[(dates >= pd.Timestamp(start)) & (dates <= pd.Timestamp(end))]
            for date in active_dates:
                result[date].add(stock)
    return result


def score_equal_weight(df, factors, directions):
    """按训练期 IC 方向统一因子含义后等权合成；+1 保持原值，-1 反向。"""
    aligned_directions = pd.Series(
        [directions[factor] for factor in factors], index=factors, dtype=float
    )
    return df[factors].mul(aligned_directions, axis="columns").mean(axis=1)


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


def run_backtest(
    test_score,
    test_open_wide,
    test_volume_wide,
    topk=TOPK,
    membership_by_date=None,
):
    """
    Step 6+7 合并：按开盘价逐日维护实际持仓、现金和冻结持仓。

    test_score    : 测试期合成得分 Series，索引 = (datetime, instrument)
    test_open_wide: 测试期开盘价宽表，shape = (交易日, 股票数)
    test_volume_wide: 测试期成交量宽表，用于识别零成交量的不可交易日
    topk         : 选取得分最高的股票数；None 表示选取当日全部候选股
    membership_by_date: {date: set(stocks)}，在实际成交日再校验成分资格
    返回          : DataFrame，每行是一个开盘到次日开盘的账户收益区间

    简化成交规则：
    - 有限且大于 0 的开盘价可用于估值；否则沿用最后一个有效价。
    - 只有开盘价有效且成交量 > 0 时才允许交易。
    - 买入失败时资金留在现金；卖出失败时仓位继续持有并冻结。
    - 手续费只对实际成交金额收取。
    """
    # Step 6+7：将 T 日信号安排到 T+1 开盘执行。
    # T 日完整行情收盘后才能生成信号，因此最早在 T+1 开盘成交；普通 A 股当天
    # 买入后不能当天卖出，所以在 T+2 开盘退出或调仓。收益区间必须与 LABEL 一致：
    # open[T+2] / open[T+1] - 1。
    trading_dates = pd.DatetimeIndex(test_open_wide.index).sort_values()
    date_pos = {date: pos for pos, date in enumerate(trading_dates)}
    execution_schedule = {}
    for date, grp in test_score.groupby(level="datetime"):
        signal_date = pd.Timestamp(date)
        pos = date_pos.get(signal_date)
        if pos is None or pos + 2 >= len(trading_dates):
            continue
        entry_date = trading_dates[pos + 1]
        scores = grp.xs(date, level="datetime")

        # 策略得分在信号日 T 已按当日成分股过滤；这里在 T+1
        # 真正成交前再校验一次，避免成分调整生效日买入已退出股票。
        # 成分资格只决定“允许买什么”，当日能否成交仍由价格
        # 和成交量判断。
        if membership_by_date is not None:
            current_members = membership_by_date.get(entry_date, set())
            scores = scores[scores.index.isin(current_members)]

        if topk is None:
            target_stocks = scores.index.tolist()
        else:
            target_stocks = scores.nlargest(topk).index.tolist()
        execution_schedule[entry_date] = (signal_date, target_stocks)

    if not execution_schedule:
        return pd.DataFrame()

    records = []
    actual_holdings = {}       # instrument -> 实际持有的股数（允许小数股）
    last_valid_prices = {}     # 用于缺价时估值，不代表当天可成交
    cash = 1.0                 # 从 1 元初始净值开始，股数按比例计算
    active_interval = None

    def market_state(date):
        """返回当日估值价、可交易状态，并更新最后有效估值价。"""
        open_row = test_open_wide.loc[date]
        volume_row = test_volume_wide.loc[date]
        valid_price = open_row.notna() & np.isfinite(open_row) & (open_row > 0)
        tradable = valid_price & volume_row.notna() & np.isfinite(volume_row) & (volume_row > 0)
        last_valid_prices.update(open_row[valid_price].to_dict())
        return open_row, tradable

    def account_value():
        """按最后有效价格计算现金 + 持仓市值。"""
        stock_value = sum(
            shares * last_valid_prices[stock]
            for stock, shares in actual_holdings.items()
            if stock in last_valid_prices
        )
        return cash + stock_value

    def rebalance(target_stocks, open_row, tradable):
        """卖出优先、再按可用现金比例买入；返回实际成交和账户状态。"""
        nonlocal cash
        target_stocks = list(dict.fromkeys(target_stocks))
        nav_before = account_value()
        target_value = nav_before / len(target_stocks) if target_stocks else 0.0
        target_set = set(target_stocks)
        gross_sell = 0.0
        gross_buy = 0.0
        unfilled_sells = set()
        unfilled_buys = set()

        # 先减少超过目标的持仓。当日不可交易时，股数原样保留。
        for stock, shares in list(actual_holdings.items()):
            price = last_valid_prices.get(stock)
            if price is None:
                continue
            desired_value = target_value if stock in target_set else 0.0
            current_value = shares * price
            sell_value = max(current_value - desired_value, 0.0)
            if sell_value <= 1e-14:
                continue
            if not bool(tradable.get(stock, False)):
                unfilled_sells.add(stock)
                continue
            trade_price = float(open_row[stock])
            sell_shares = min(shares, sell_value / trade_price)
            filled_value = sell_shares * trade_price
            actual_holdings[stock] = shares - sell_shares
            if actual_holdings[stock] <= 1e-14:
                del actual_holdings[stock]
            gross_sell += filled_value
            cash += filled_value * (1.0 - TRANSACTION_COST)

        # 再汇总所有买入需求。若现金不足，对可成交买单等比例缩放，
        # 避免由遍历顺序决定哪只股票先获得资金。
        requested_buys = {}
        for stock in target_stocks:
            price = last_valid_prices.get(stock)
            current_value = actual_holdings.get(stock, 0.0) * price if price is not None else 0.0
            buy_value = max(target_value - current_value, 0.0)
            if buy_value <= 1e-14:
                continue
            if not bool(tradable.get(stock, False)):
                unfilled_buys.add(stock)
                continue
            requested_buys[stock] = buy_value

        requested_total = sum(requested_buys.values())
        affordable_total = cash / (1.0 + TRANSACTION_COST)
        buy_scale = min(1.0, affordable_total / requested_total) if requested_total > 0 else 0.0
        # 现金不足时按比例成交，其中包括为手续费预留的微小缩放。
        # unfilled_buy_count 只统计因当日不可交易而完全无法下单的股票，
        # 避免把正常的资金约束误报成停牌或缺价。
        for stock, requested_value in requested_buys.items():
            filled_value = requested_value * buy_scale
            if filled_value <= 1e-14:
                continue
            trade_price = float(open_row[stock])
            actual_holdings[stock] = actual_holdings.get(stock, 0.0) + filled_value / trade_price
            gross_buy += filled_value
            cash -= filled_value * (1.0 + TRANSACTION_COST)

        # 浮点误差可能产生极小负现金，但不允许它演变成隐式融资。
        if -1e-12 < cash < 0:
            cash = 0.0

        nav_after = account_value()
        frozen = [
            stock for stock in actual_holdings
            if not bool(tradable.get(stock, False))
        ]
        frozen_value = sum(
            actual_holdings[stock] * last_valid_prices[stock]
            for stock in frozen
            if stock in last_valid_prices
        )
        base = nav_before if nav_before > 0 else np.nan
        transaction_cost_amount = (gross_buy + gross_sell) * TRANSACTION_COST
        return {
            "nav_before": nav_before,
            "nav_after": nav_after,
            "gross_buy": gross_buy,
            "gross_sell": gross_sell,
            "buy_turnover": gross_buy / base,
            "sell_turnover": gross_sell / base,
            # 保留“单边换手”的直观口径：纯建仓或纯清仓为 100%，
            # 整仓换股也约为 100%。成本则仍按买卖实际成交额分别收取。
            "turnover": max(gross_buy, gross_sell) / base,
            # 输出的成本是相对调仓前净值的比例，可与当日收益率直接对照。
            "transaction_cost": transaction_cost_amount / base,
            "cash_weight": cash / nav_after if nav_after > 0 else np.nan,
            "frozen_count": len(frozen),
            "frozen_weight": frozen_value / nav_after if nav_after > 0 else np.nan,
            "unfilled_buy_count": len(unfilled_buys),
            "unfilled_sell_count": len(unfilled_sells),
        }

    first_entry = min(execution_schedule)
    # 最后一次目标在 entry 开盘调仓，再持有到下一个开盘结算。
    last_entry_pos = date_pos[max(execution_schedule)]
    final_settlement = trading_dates[last_entry_pos + 1]
    simulation_dates = trading_dates[
        (trading_dates >= first_entry) & (trading_dates <= final_settlement)
    ]

    for date in simulation_dates:
        open_row, tradable = market_state(date)
        nav_at_open = account_value()

        # 先用当日开盘价结束上一个 open-to-open 区间，再执行新信号。
        # 这保证 T 日信号仍然只计算 T+1 开盘到 T+2 开盘的收益。
        if active_interval is not None:
            gross_pnl = nav_at_open - active_interval["nav_after"]
            record = {
                "date": date,
                "signal_date": active_interval["signal_date"],
                "entry_date": active_interval["entry_date"],
                "gross_ret": gross_pnl / active_interval["nav_before"],
                "net_ret": (nav_at_open - active_interval["nav_before"]) / active_interval["nav_before"],
                "liquidation_turnover": 0.0,
                **active_interval["trade_stats"],
            }
            records.append(record)

        if date in execution_schedule:
            signal_date, target_stocks = execution_schedule[date]
            trade_stats = rebalance(target_stocks, open_row, tradable)
            active_interval = {
                "signal_date": signal_date,
                "entry_date": date,
                "nav_before": trade_stats["nav_before"],
                "nav_after": trade_stats["nav_after"],
                "trade_stats": {
                    key: value for key, value in trade_stats.items()
                    if key not in {"nav_before", "nav_after", "gross_buy", "gross_sell"}
                },
                "gross_buy": trade_stats["gross_buy"],
                "gross_sell": trade_stats["gross_sell"],
            }
        elif date < final_settlement:
            # 若某个交易日因数据不足没有新信号，不能让账户收益从
            # 时间序列中消失；继续持有原仓位，并开启下一日收益区间。
            nav = account_value()
            frozen = [stock for stock in actual_holdings if not bool(tradable.get(stock, False))]
            frozen_value = sum(
                actual_holdings[stock] * last_valid_prices[stock]
                for stock in frozen if stock in last_valid_prices
            )
            active_interval = {
                "signal_date": pd.NaT,
                "entry_date": date,
                "nav_before": nav,
                "nav_after": nav,
                "trade_stats": {
                    "buy_turnover": 0.0,
                    "sell_turnover": 0.0,
                    "turnover": 0.0,
                    "transaction_cost": 0.0,
                    "cash_weight": cash / nav if nav > 0 else np.nan,
                    "frozen_count": len(frozen),
                    "frozen_weight": frozen_value / nav if nav > 0 else np.nan,
                    "unfilled_buy_count": 0,
                    "unfilled_sell_count": 0,
                },
                "gross_buy": 0.0,
                "gross_sell": 0.0,
            }

    # 最后一个 T+2 开盘尝试清仓。只有当日真正可交易的持仓才会卖出；
    # 无法卖出的持仓仍按最后有效价估值，并在输出中明确披露，不伪装成现金。
    if records:
        final_open_row, final_tradable = market_state(final_settlement)
        nav_before_liquidation = account_value()
        liquidation = rebalance([], final_open_row, final_tradable)
        last = records[-1]
        interval_base = active_interval["nav_before"]
        last["liquidation_turnover"] = (
            liquidation["gross_sell"] / nav_before_liquidation
            if nav_before_liquidation > 0 else np.nan
        )
        last["sell_turnover"] += liquidation["gross_sell"] / interval_base
        last["turnover"] = max(last["buy_turnover"], last["sell_turnover"])
        liquidation_cost_amount = (
            liquidation["gross_buy"] + liquidation["gross_sell"]
        ) * TRANSACTION_COST
        last["transaction_cost"] += liquidation_cost_amount / interval_base
        last["net_ret"] = (liquidation["nav_after"] - interval_base) / interval_base
        last["cash_weight"] = liquidation["cash_weight"]
        last["frozen_count"] = liquidation["frozen_count"]
        last["frozen_weight"] = liquidation["frozen_weight"]
        last["unfilled_sell_count"] += liquidation["unfilled_sell_count"]

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

    # 信号可以生成到 TEST_END，但最后一个信号还需要 T+1、T+2 两个开盘价才能
    # 完成买入和退出。因此从 Qlib 交易日历自动取得 TEST_END 之后的两个交易日，
    # 只把它们作为收益结算缓冲，不允许它们产生新的测试信号。
    calendar_buffer = pd.DatetimeIndex(D.calendar(
        start_time=TEST_END,
        end_time=pd.Timestamp(TEST_END) + pd.Timedelta(days=31),
        freq="day",
    ))
    future_trading_dates = calendar_buffer[calendar_buffer > pd.Timestamp(TEST_END)]
    if len(future_trading_dates) < 2:
        raise RuntimeError("TEST_END 之后不足两个交易日，无法结算最后一个测试信号")
    settlement_end = future_trading_dates[1]
    print(f"测试信号截止: {TEST_END}  |  结算行情截止: {settlement_end.date()}")

    # 两步加载，修复刚入选股票因子 NaN 问题：
    # 若直接传 "csi300" 字符串，Qlib 内部会把每只股票的数据裁剪到其成员资格区间，
    # 导致 shift()/rolling() 在入选前的历史窗口上产生虚假 NaN。
    # 改为先取成员列表（保留 spans 供后续过滤），再用纯列表加载完整历史。
    membership_dict = D.list_instruments(  # 记录了每只股票曾经入选 CSI 300 的时间段
        D.instruments(UNIVERSE),  # 构造"CSI 300 股票池"的描述对象，此时还没有真正查数据
        start_time=DATA_START,
        # 最后一个信号要到 TEST_END 之后的 T+1 才成交，所以成分
        # 资格也必须加载到结算日，不能在 TEST_END 提前截断。
        end_time=settlement_end,
        freq="day",
        as_list=False,          # 保留 {stock: [(start, end), ...]}，后续 universe 过滤用
    )
    all_stocks = list(membership_dict.keys())

    # 传入纯列表 → Qlib 内部 spans=None → 不做成员资格裁剪，返回完整历史
    raw_df = D.features(
        all_stocks,
        fields=["$open", "$high", "$low", "$close", "$volume"],
        start_time=DATA_START,
        end_time=settlement_end,
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
    open_  = raw_df["open"].unstack("instrument")  # 避免使用 open，防止覆盖 Python 内置函数
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
    # T 日收盘生成信号，T+1 开盘买入，T+2 开盘卖出/调仓；该收益区间既避免
    # 使用已经结束的 T 日收盘价成交，也满足普通 A 股“当日买入、次日才能卖出”。
    LABEL     = open_.shift(-2) / open_.shift(-1) - 1

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

    # rolling 因子需要每只股票入选 CSI300 前的历史行情来预热窗口，所以 Step 2
    # 在完整历史上计算因子；但下面的 MAD 和 Z-Score 是“同一天股票之间”的截面
    # 比较，只能让当天真实的 CSI300 成分股参与，否则尚未入选或已经退出的股票
    # 会改变当天的中位数、MAD、均值和标准差。
    dt  = factor_df.index.get_level_values("datetime")
    ins = factor_df.index.get_level_values("instrument")
    in_universe = pd.Series(False, index=factor_df.index)
    for stock, spans in membership_dict.items():  # 判断每只股票在每个交易日是否属于 CSI300
        for start, end in spans:
            in_universe |= (ins == stock) & (dt >= start) & (dt <= end)

    # 先按日期过滤当日成分股，再仅在这些股票内部执行截面预处理。
    processed = factor_df[in_universe].copy()
    processed[FACTOR_COLS] = (
        processed.groupby(level="datetime")[FACTOR_COLS].transform(winsorize_cs)
    )
    processed[FACTOR_COLS] = (
        processed.groupby(level="datetime")[FACTOR_COLS].transform(zscore_cs)
    )

    # 【重要：不能用未来标签筛选当天的可选股票】
    # LABEL[t] 是 T+1 开盘到 T+2 开盘的未来收益，在 T 日生成信号时尚不可知。
    # 如果这里直接 processed.dropna()，就会把 LABEL 为 NaN 的股票也删掉：例如
    # 某股未来停牌、退市或缺数据，策略却会在 T 日提前避开它，这就是前视偏差。
    #
    # 因此先建立“打分数据集”：只要当天的因子都可用，该股票就能参与打分。
    # 此处依然要求全部候选因子非空，仅保留了原脚本对因子完整性的要求；
    # 唯一的行为变化是：不再要求未来 LABEL 非空。
    scoring_df = processed.dropna(subset=FACTOR_COLS)

    # “有标签数据集”只用于需要知道正确答案的场景：模型训练和 IC 检验。
    # 它是 scoring_df 的子集，但不能反过来影响 scoring_df 的股票池。
    labeled_df = scoring_df.dropna(subset=["LABEL"])

    print(f"处理前  shape = {factor_df.shape}  NaN 行数 = {factor_df.isna().any(axis=1).sum()}")
    print(f"可打分  shape = {scoring_df.shape}   只要求因子非空（不查看 LABEL）")
    print(f"可训练  shape = {labeled_df.shape}   在可打分数据上再要求 LABEL 非空")
    print(f"  保留了 {scoring_df['LABEL'].isna().sum()} 行‘因子可用、但未来标签缺失’的打分样本")
    print(f"  首个可打分日期: {scoring_df.index.get_level_values('datetime').min().date()}")
    print(f"  末个可打分日期: {scoring_df.index.get_level_values('datetime').max().date()}")

    # 训练需要 X（因子）和 y（LABEL），所以使用 labeled_df。
    train_df = segment(labeled_df, TRAIN_START, TRAIN_END)
    # 验证和测试先为所有因子可用的股票打分，不能根据 LABEL 是否存在缩小股票池。
    # 后面计算验证集 IC 时，才会在“已经生成的得分”和“事后可用的 LABEL”之间取交集。
    valid_df = segment(scoring_df, VALID_START, VALID_END)
    test_df  = segment(scoring_df, TEST_START,  TEST_END)

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

    print("IC  = Spearman(因子值, T+1开盘至T+2开盘收益率)，在每日截面计算，再对时序取统计")
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
            if pd.isna(ic_val):
                continue
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

    # valid_factors 是按 |IC| 选择的，所以负 IC 因子同样可能有预测能力，只是方向
    # 与“因子越大、未来收益越高”相反。等权相加前先将负 IC 因子乘以 -1，使所有
    # 因子都统一为“校正后数值越大，预期收益越高”。方向只由训练期确定，并固定
    # 应用于 valid/test，绝不能根据验证期或测试期结果回头调整，否则会产生泄漏。
    train_ic = ic_table.loc[valid_factors, "IC均值"]
    factor_directions = pd.Series(
        np.where(train_ic >= 0, 1.0, -1.0),
        index=valid_factors,
    )
    print("  训练期 IC 决定的因子方向（+1 保持，-1 反向）:")
    for factor in valid_factors:
        print(f"    {factor:<12} IC={train_ic[factor]:+.4f}  direction={factor_directions[factor]:+g}")

    train_score_ew = score_equal_weight(train_df, valid_factors, factor_directions)
    valid_score_ew = score_equal_weight(valid_df, valid_factors, factor_directions)
    test_score_ew  = score_equal_weight(test_df,  valid_factors, factor_directions)

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
        if pd.isna(ic_val):
            continue
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
            lb = grp["LABEL"].xs(date, level="datetime").dropna()  # T+1 开盘至 T+2 开盘收益率
            cm = sc.index.intersection(lb.index)
            if len(cm) < 10:
                continue
            ic_val, _ = stats.spearmanr(sc.loc[cm], lb.loc[cm])
            if pd.isna(ic_val):
                continue
            ic_vals.append(ic_val)
        iv = pd.Series(ic_vals)
        print(f"  {method_name:<8}  IC均值={iv.mean():.4f}  ICIR={iv.mean()/(iv.std()+1e-9):.4f}")

    # ─────────────────────────────────────────────────────
    # Step 6+7  组合构建 + 回测（EW 和 LR 各走一遍）
    # ─────────────────────────────────────────────────────
    # 信号仍由 test_df 严格限制在 TEST_END 以内；这里额外保留两个交易日的
    # 行情，用来提供最后一个信号的 T+1 入场和 T+2 退出。
    test_open_wide = open_.loc[TEST_START:settlement_end]
    test_volume_wide = volume.loc[TEST_START:settlement_end]
    membership_by_date = build_membership_by_date(
        membership_dict,
        test_open_wide.index,
    )
    print(f"\n开盘价宽表  shape = {test_open_wide.shape}  ({test_open_wide.shape[0]} 交易日 × {test_open_wide.shape[1]} 股票)")
    print("回测逻辑：T 日收盘生成信号 → T+1 开盘按可成交性买入 → "
          "T+2 开盘卖出/调仓 → 只对实际成交额扣手续费")

    ret_results = {}
    for method_name, test_score in [("等权EW", test_score_ew), ("LR", test_score_lr)]:
        section(f"Step 6+7  组合构建 + 回测（{method_name}）")
        ret_df = run_backtest(
            test_score,
            test_open_wide,
            test_volume_wide,
            topk=TOPK,
            membership_by_date=membership_by_date,
        )
        ret_results[method_name] = ret_df

        avg_turnover = ret_df["turnover"].mean()
        print(f"  策略日收益序列  shape={ret_df.shape}")
        print(f"  平均实际换手率: {avg_turnover:.1%}（只统计真正成交的金额）")
        print(f"  未成交买入: {ret_df['unfilled_buy_count'].sum()} 只次  |  "
              f"未成交卖出: {ret_df['unfilled_sell_count'].sum()} 只次")
        last_record = ret_df.iloc[-1]
        print(f"  期末清仓: {ret_df.index[-1].date()}  清仓换手={last_record['liquidation_turnover']:.1%}"
              f"  清仓后现金权重={last_record['cash_weight']:.1%}"
              f"  剩余冻结持仓={int(last_record['frozen_count'])} 只")
        print(ret_df.head(5).round(5))

    # 构造“当日 CSI300 成分股等权”基准。这组全 0 得分只是为了
    # 复用同一个回测函数；run_backtest 会在每个 T+1 实际成交日
    # 按 membership_by_date 保留当日成分股。topk=None 表示全部等权
    # 持有，而不是像 EW/LR 策略那样只取 Top30。
    benchmark_signal_dates = test_open_wide.index[
        (test_open_wide.index >= pd.Timestamp(TEST_START))
        & (test_open_wide.index <= pd.Timestamp(TEST_END))
    ]
    benchmark_index = pd.MultiIndex.from_product(
        [benchmark_signal_dates, test_open_wide.columns],
        names=["datetime", "instrument"],
    )
    benchmark_score = pd.Series(0.0, index=benchmark_index)

    section("Step 6+7  组合构建 + 回测（当日 CSI300 成分股等权基准）")
    benchmark_df = run_backtest(
        benchmark_score,
        test_open_wide,
        test_volume_wide,
        topk=None,
        membership_by_date=membership_by_date,
    )
    ret_results["基准"] = benchmark_df
    print(f"  基准日收益序列  shape={benchmark_df.shape}")
    print(f"  平均实际换手率: {benchmark_df['turnover'].mean():.1%}")
    print(f"  未成交买入: {benchmark_df['unfilled_buy_count'].sum()} 只次  |  "
          f"未成交卖出: {benchmark_df['unfilled_sell_count'].sum()} 只次")
    benchmark_last = benchmark_df.iloc[-1]
    print(f"  期末清仓: {benchmark_df.index[-1].date()}  "
          f"清仓后现金权重={benchmark_last['cash_weight']:.1%}  "
          f"剩余冻结持仓={int(benchmark_last['frozen_count'])} 只")

    # ─────────────────────────────────────────────────────
    # 绩效汇总对比
    # ─────────────────────────────────────────────────────
    section("绩效汇总对比")

    # 基准与两种策略共用同一个状态化回测账户，因此这里直接
    # 使用其已扣除实际买卖成本的净收益，不再对历史成分股合集求均值。
    benchmark_ret = benchmark_df["net_ret"]

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
