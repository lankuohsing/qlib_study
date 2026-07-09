# 量化投资全流程入门教程

> 配套脚本：`quant_workflow_from_scratch.py`  
> 适合人群：有机器学习背景、无量化经验的工程师

---

## 目录

1. [背景与全局视角](#1-背景与全局视角)
2. [环境与数据准备](#2-环境与数据准备)
3. [Step 1 数据加载](#3-step-1-数据加载)
4. [Step 2 因子计算](#4-step-2-因子计算)
5. [Step 3 因子预处理](#5-step-3-因子预处理)
6. [Step 4 因子有效性分析（IC / ICIR）](#6-step-4-因子有效性分析ic--icir)
7. [Step 5 因子合成](#7-step-5-因子合成)
8. [Step 6 组合构建](#8-step-6-组合构建)
9. [Step 7 回测](#9-step-7-回测)
10. [结果解读与常见问题](#10-结果解读与常见问题)

---

## 1 背景与全局视角

量化投资的核心思想是：**用数据和模型替代主观判断，系统性地从市场中寻找超额收益**。

从 ML 的角度看，这整个流程可以类比为一个**监督学习 → 部署 → 评估**的管线：

| 量化步骤 | 对应 ML 概念 |
|----------|-------------|
| 原始行情 OHLCV | 原始输入数据 |
| 因子计算 | 特征工程 |
| 因子预处理（去极值 + 标准化） | 数据归一化（类似 sklearn 的 StandardScaler）|
| IC 分析 | 特征重要性 / 相关性分析 |
| 因子合成 | 特征加权 / 线性模型 |
| 组合构建 | 预测结果 → 决策（取 Top-K）|
| 回测 | 模型在保留测试集上的评估 |

**数据集划分**

```
DATA_START(2014-06) ──→ TRAIN(2015~2017) ──→ VALID(2018) ──→ TEST(2019~2020-08)
         ↑
     rolling window 需要的历史（最长 20 日）
```

`DATA_START` 比 `TRAIN_START` 早约 6 个月，是为了让因子的 rolling 窗口（最长 20 日）在训练期开始时已经"预热"完毕，避免训练集头部出现大量 NaN。

**整体数据流**

```
原始 OHLCV 行情
     ↓ Step 1  D.features()
  raw_df (MultiIndex: datetime × instrument)
     ↓ Step 2  手工公式
  factor_df (7 个因子 + 1 个 label)
     ↓ Step 3  截面标准化 + 过滤
  clean_df → train / valid / test 三段
     ↓ Step 4  IC / ICIR 分析
  筛选有效因子列表
     ↓ Step 5  因子合成
  test_score（每只股票每天一个综合得分）
     ↓ Step 6  Top-K 选股
  holdings（每天持哪些股票）
     ↓ Step 7  逐日模拟
  净值曲线 + 绩效指标
```

---

## 2 环境与数据准备

脚本依赖以下库：

```bash
pip install qlib numpy pandas scipy scikit-learn
```

数据来自 Qlib 官方提供的 A 股历史行情（CSI 300 相关）。按照 [Qlib 官方说明](https://github.com/microsoft/qlib#data-preparation) 下载并解压到 `PROVIDER_URI` 目录后，`qlib.init()` 即可找到数据。

**macOS 特别注意**：脚本所有执行代码都放在 `if __name__ == '__main__':` 内。原因是 macOS 上 Python 多进程默认使用 `spawn`（而非 Linux 的 `fork`），Qlib 内部的 `joblib` 并行加载数据时会 spawn 子进程并重新导入主模块。没有 `__main__` 保护就会导致无限递归 spawn、进程爆炸。

---

## 3 Step 1 数据加载

### 3.1 核心 API

```python
qlib.init(provider_uri=PROVIDER_URI, region=REG_CN)

membership_dict = D.list_instruments(
    D.instruments("csi300"),
    start_time=DATA_START, end_time=TEST_END,
    freq="day", as_list=False,  # 保留 {stock: [(start, end), ...]}
)
all_stocks = list(membership_dict.keys())

raw_df = D.features(
    all_stocks,                          # 传入纯列表，不传 "csi300"
    fields=["$open", "$high", "$low", "$close", "$volume"],
    start_time=DATA_START, end_time=TEST_END,
    freq="day",
)
```

### 3.2 两步加载的原因

直接把字符串 `"csi300"` 传给 `D.features()` 时，Qlib 会把每只股票的数据**裁剪到其成员资格区间**。例如某股票 2016 年才入选 CSI 300，其数据只从 2016 年开始，在此之前的 rolling 窗口无法预热，导致入选初期产生大量虚假 NaN。

解决方案分两步：

1. 先取成员列表（`membership_dict`），保留每只股票的入选起止时间
2. 再用**纯代码列表**加载完整历史（Qlib 内部 `spans=None`，不做成员资格裁剪）
3. Step 3 再用 `membership_dict` 过滤掉"当天不在指数内"的行

### 3.3 索引结构

```python
raw_df.index.names = ["instrument", "datetime"]
raw_df = raw_df.swaplevel().sort_index()
# 结果索引变为：["datetime", "instrument"]
```

`D.features()` 返回 `(instrument, datetime)` 的 MultiIndex。交换后变为 `(datetime, instrument)`，这样后续 `groupby(level="datetime")` 才能高效按日期做截面运算。`sort_index()` 使索引有序，是 `.xs()`、标签范围切片等操作**正确性**的前提（不只是性能）。

---

## 4 Step 2 因子计算

### 4.1 长表 → 宽表

因子计算在**宽表（wide format）** 上进行：行是交易日，列是股票代码。

```python
close  = raw_df["close"].unstack("instrument")
# shape = (交易日数, 股票数)，例如 (1660, 320)
```

相比 `groupby + apply`，宽表上的 `shift()`、`rolling()` 等操作可以对所有股票并行完成，速度快得多。

### 4.2 七个基础因子

| 因子 | 公式 | 经济含义 | ML 类比 |
|------|------|----------|---------|
| `MOM_5D` | `close[t] / close[t-5] - 1` | 5 日动量，捕捉短期趋势 | 近期变化率特征 |
| `MOM_20D` | `close[t] / close[t-20] - 1` | 20 日动量，捕捉中期趋势 | 中期变化率特征 |
| `VOL_20D` | `std(daily_return, 20日窗口)` | 近20日波动率（高波动 = 高风险）| 滚动标准差特征 |
| `TURN_5D` | `volume[t] / mean(volume, 5日窗口)` | 量比，衡量成交活跃度 | 滚动均值比特征 |
| `MA_DEV` | `close[t] / mean(close, 20日窗口) - 1` | 偏离均线，反映超买/超卖 | 滚动均值偏差 |
| `DAY_RANGE` | `(high - low) / close[t-1]` | 当日振幅，反映日内波动 | 极差/基准比 |
| `PRICE_POS` | `(close - low) / (high - low)` | 收盘在高低区间的位置 | 区间归一化位置 |

**LABEL（预测目标）**

```python
LABEL = close.shift(-1) / close - 1  # 次日收益率
```

`shift(-1)` 将每一行的值替换为**下一行**的值，即"明天的收盘价"。因此 `LABEL[t]` = 股票在 t 日收盘后持有到 t+1 日收盘的收益率。在回测中，这是不可预知的未来信息，只用于训练和 IC 评估，不用于选股决策。

### 4.3 关于 rolling 窗口的说明

pandas 的 `rolling(n)` 默认包含**当日**在内的 n 个样本（窗口为 `[t-n+1, t]`）。例如 `rolling(20).std()` 是当日及前 19 个交易日共 20 天的标准差，而非"过去 20 天不含今天"。

### 4.4 宽表 → 长表

```python
factor_df = pd.concat({
    "MOM_5D": MOM_5D.stack(future_stack=True),
    ...
}, axis=1)
# shape ≈ (交易日数 × 股票数, 8)，其中 8 = 7因子 + 1标签
```

`stack()` 把宽表的列（股票代码）压回为 MultiIndex 的第二层，重新得到 `(datetime, instrument)` 长表，方便后续 groupby 截面操作。

---

## 5 Step 3 因子预处理

### 5.1 为什么要截面标准化？

- 不同因子量纲差异巨大：`MOM_5D` ≈ ±0.1，`TURN_5D` ≈ 1~3，无法直接比较或等权合成
- **截面（cross-sectional）操作**：每天只在"同一天的所有股票"之间做标准化，消除因子在不同日期的系统性漂移

这与 ML 中的 `StandardScaler` 类似，但不是跨时间的全局标准化，而是**逐日**的局部标准化。原因是：股票的绝对价格和交易量随时间漂移，跨期的全局均值/方差没有意义。

### 5.2 去极值（MAD 法）

```python
def winsorize_cs(s, n_sigma=3):
    median = s.median()
    mad    = (s - median).abs().median()       # Median Absolute Deviation
    lo     = median - n_sigma * 1.4826 * mad
    hi     = median + n_sigma * 1.4826 * mad
    return s.clip(lo, hi)
```

**为什么用 MAD 而不是标准差？**

标准差受极端值影响大（计算时用了 `(x - mean)²`，极端值被平方放大）。MAD 用中位数做基准，极端值只影响排序位置，不影响中位数本身——这是统计学中的**鲁棒估计量（robust estimator）**。

**系数 1.4826** 是正态分布的一致性常数：对于正态分布 `σ ≈ 1.4826 × MAD`，所以 `3 × 1.4826 × MAD ≈ 3σ`，与传统"3 倍标准差"的截断等价。

### 5.3 Z-Score 标准化

```python
def zscore_cs(s):
    return (s - s.mean()) / (s.std() + 1e-9)
```

去极值后再做截面 Z-Score，让每个因子在每天都有均值 0、标准差 1，可以安全地等权相加或作为线性模型的输入。

### 5.4 成员资格过滤

```python
for stock, spans in membership_dict.items():
    for start, end in spans:
        in_universe |= (ins == stock) & (dt >= start) & (dt <= end)
clean_df = clean_df[in_universe]
```

Step 1 加载了完整历史（为了预热 rolling 窗口），这里把"某天不是 CSI 300 成分股"的行过滤掉，确保选股和回测只在指数成员中进行。`membership_dict` 中的 `spans` 记录了每只股票历次入选/退出指数的时间段。

### 5.5 三段切分

预处理完成后，按时间将数据分为训练集（用于训练模型、计算 IC）、验证集（选择合成方式）、测试集（最终回测），严格避免**前视偏差（look-ahead bias）**——即用未来数据辅助当前决策。

---

## 6 Step 4 因子有效性分析（IC / ICIR）

### 6.1 什么是 IC？

**IC（Information Coefficient）** = 因子值与未来收益的 **Spearman 秩相关系数**。

```
IC(t) = Spearman(因子值[t, 所有股票], 次日收益率[t, 所有股票])
```

- `IC > 0`：因子值越高，次日涨得越多（正向因子）
- `IC < 0`：因子值越高，次日跌得越多（反向因子）
- `|IC| ≈ 0`：因子无预测力

与皮尔逊相关系数不同，Spearman 是基于**排名**的相关，对非线性关系和极端值更鲁棒——股票收益率分布有厚尾，排名相关更适合。

### 6.2 什么是 ICIR？

```
ICIR = mean(IC时间序列) / std(IC时间序列)
```

类比夏普比率：分子衡量平均预测力，分母衡量预测稳定性。

| 指标 | 经验阈值 | 含义 |
|------|---------|------|
| `\|IC均值\|` | > 0.03 | 有一定预测力（0.05 以上算优秀）|
| `ICIR` | > 0.5 | 预测较稳定 |
| `IC > 0 占比` | > 55% | 因子方向一致性好 |

**重要直觉**：IC 很低（通常 < 0.05）是正常的。股票涨跌本身就很难预测，0.03 的 IC 在数百只股票上持续应用也能产生显著的累积超额收益。这与 ML 中高精度要求（AUC > 0.8）的直觉截然不同，需要重新校准预期。

### 6.3 代码解读

```python
for factor in FACTOR_COLS:
    ic_list = []
    for date, grp in train_df.groupby(level="datetime"):
        grp_flat = grp.xs(date, level="datetime")        # 某一天所有股票的数据
        valid = grp_flat[[factor, "LABEL"]].dropna()
        if len(valid) < 10:
            continue
        ic_val, _ = stats.spearmanr(valid[factor], valid["LABEL"])
        ic_list.append(ic_val)
    # ic_list 是一个时间序列，长度 = 训练集交易日数
```

每天做一次截面 Spearman 相关，收集成 IC 时间序列，最后对时序取统计量（均值、标准差、ICIR）。

---

## 7 Step 5 因子合成

### 7.1 方式 A：等权平均

```python
score = df[valid_factors].mean(axis=1)
```

对每只股票，把其 Z-Score 标准化后的各因子值简单平均，得到综合得分。

优点：不过拟合训练集，鲁棒性强。  
缺点：没有利用各因子对未来收益的差异化预测力。

### 7.2 方式 B：线性回归

```python
lr = LinearRegression().fit(X_tr, y_tr)
# X_tr: (样本数, 因子数)，y_tr: (样本数,) = 次日收益率
```

把因子视为特征、次日收益率视为目标，训练线性模型，学到各因子的最优权重。

**为什么 R² 这么低（< 0.01）？**  
这是量化领域的正常现象。市场是半有效的，单个线性模型能解释的收益方差极少。R² 低不代表模型没用——哪怕 IC 只有 0.03，在数百只股票上持续应用也能产生显著的累积超额收益。

### 7.3 在验证集上对比

脚本在验证集上对比两种合成方式的 IC，并选择**等权合成**进入回测：

> **等权合成更鲁棒，不过拟合训练集。**

这是量化领域的一个重要经验：简单方法往往胜过复杂模型，因为金融数据的信噪比极低，复杂模型很容易过拟合历史规律而在未来失效。

这与 ML 的模型选择逻辑完全一致：在 valid 集上选方法，在 test 集上做最终评估。

---

## 8 Step 6 组合构建

```python
holdings = {}
for date, grp in test_score.groupby(level="datetime"):
    top_stocks = grp.xs(date, level="datetime").nlargest(TOPK).index.tolist()
    holdings[date] = top_stocks
```

每个交易日，按综合得分从高到低排序，取前 `TOPK=30` 只股票，等权持有（每只权重 = 1/30）。

**策略假设**：因子得分高的股票明天更可能上涨，持有它们构成多头组合。

这是一个**纯多头、等权重**的策略。真实量化策略还可以做多空（做空得分低的股票）、根据预测置信度调整权重、加入风险约束等，但本脚本以学习为目的，保持简单。

---

## 9 Step 7 回测

### 9.1 回测时序逻辑（避免前视偏差）

```
T 日收盘 → 用 T 日因子计算得分 → 选出 Top-K 持仓（holdings[T]）
                                                    ↓
T+1 日收盘：持有 holdings[T]，实现 T+1 日的涨跌幅
            同时用 T+1 日因子选出新持仓（holdings[T+1]）→ 计算换手率 → 扣手续费
```

代码实现的核心逻辑：

```python
for i, date_T in enumerate(dates):
    curr_set   = set(holdings[date_T])        # T 日选出的新持仓
    ret_stocks = list(prev_set) if prev_set else list(curr_set)
    # ret_stocks = T-1 日持仓，今天（T日）实现其收益

    port_ret = test_ret_wide.loc[date_T, valid_stocks].mean()
    # test_ret_wide.loc[date_T] = T-1日收盘到T日收盘的涨跌幅

    prev_set = curr_set  # 本轮持仓留作下一轮的 prev_set
```

`test_ret_wide.loc[date_T]` 是"T-1 日收盘到 T 日收盘"的涨跌幅，而 `ret_stocks` 是 T-1 日选出的持仓——因此是用 **T-1 日的信号**去实现 **T 日的收益**，没有用到未来信息。

### 9.2 换手率与手续费

```python
turnover = len(curr_set.symmetric_difference(prev_set)) / (2 * TOPK)
net_ret  = port_ret - turnover * TRANSACTION_COST
```

- `symmetric_difference`：两个集合的对称差 = 本日新增股 + 本日卖出股的总只数
- `/ (2 * TOPK)`：把"调仓只数/2"换算为"交易金额/组合总市值"

**举例**：若 30 只股票中换了 10 只，则：
- 卖出 10 只（= 组合 1/3 的市值）
- 买入 10 只（= 组合 1/3 的市值）
- `symmetric_difference = 20`，`turnover = 20 / (2×30) = 1/3`
- 单边手续费 `TRANSACTION_COST = 0.1%`，交易成本 = `1/3 × 0.1% ≈ 0.033%`

高换手率会显著侵蚀收益，这是量化策略中需要特别关注的成本来源。

### 9.3 基准对比

```python
benchmark_ret = test_ret_wide.mean(axis=1).reindex(ret_df.index).fillna(0)
```

基准为**等权指数**：每天对所有 CSI 300 成分股的涨跌幅取均值，近似于等权配置所有成分股。这与市值加权的"沪深 300 指数"不同，但足以衡量策略是否有超额收益。

### 9.4 绩效指标

| 指标 | 公式 | 含义 | 参考水平 |
|------|------|------|---------|
| 年化收益 | `累计收益^(252/交易日数) - 1` | 折算到一年的平均收益 | > 基准 |
| 年化波动 | `日收益标准差 × √252` | 收益的不稳定程度 | 越低越好 |
| 夏普比率 | `年化收益 / 年化波动` | 单位风险的超额收益 | > 1 较好 |
| 最大回撤 | `min((净值 - 历史最高) / 历史最高)` | 最大亏损幅度（负数）| 越小越好 |
| 累计收益 | `最终净值 - 1` | 整个测试期的总收益 | > 0 |

---

## 10 结果解读与常见问题

### 10.1 IC 偏低怎么办？

量化界 `|IC|` 在 0.02~0.05 之间都是正常的。如果某因子 IC 接近 0，说明其对未来收益没有预测力，可以考虑：

1. 尝试不同的时间窗口（1 日、10 日、60 日动量）
2. 非线性变换（排名、分位数）
3. 结合多个低 IC 但低相关性的因子（分散化提升组合 IC）

### 10.2 策略跑输基准怎么看？

影响策略表现的因素：

- **因子本身的有效性**：IC 低的因子带来低超额
- **交易成本**：高换手率侵蚀超额
- **股票池**：CSI 300 大盘股市场效率较高，alpha 较难挖掘
- **时间段**：2019~2020 包含 2020 年新冠冲击，市场结构特殊

### 10.3 过拟合风险

本脚本用 valid 集选择了合成方式，用 train 集训练了线性回归权重。尽管如此，仍需警惕：

- 7 个因子的选择本身是基于对 A 股市场的先验知识，存在**研究者过拟合（researcher bias）**
- IC 阈值（0.02）是在 train 集上确定的
- test 集仅约 1.5 年，统计置信度有限

真实的量化研究需要更严格的样本外测试（out-of-sample）和 walk-forward 验证（滚动扩窗或滑窗回测）。

### 10.4 数据结构全流程一览

```
D.features() 原始数据
    shape = (总行数, 5)
    索引 = (instrument, datetime)
          ↓  swaplevel + sort_index
    索引 = (datetime, instrument)
          ↓  unstack("instrument")
宽表 close / high / low / volume
    shape = (交易日数, 股票数)
          ↓  因子计算（shift, rolling, pct_change）
因子宽表 MOM_5D, VOL_20D, ...
    shape = (交易日数, 股票数)
          ↓  stack() + concat
因子长表 factor_df
    shape = (交易日数 × 股票数, 8)
    索引 = (datetime, instrument)
          ↓  groupby("datetime").transform(winsorize + zscore)
          ↓  dropna + 成员资格过滤
clean_df（预处理后）
    shape ≈ (有效行数, 8)
          ↓  segment 切分
train_df / valid_df / test_df
          ↓  IC 分析（只用 train_df）
筛选有效因子
          ↓  等权合成（train + valid 选方法）
test_score（每行一个综合得分）
    shape = (test 有效行数,)
          ↓  nlargest(TOPK) per day
holdings（每天 Top-30 股票列表）
    dict{date: [stock1, ...]}
          ↓  逐日模拟，扣手续费
ret_df（策略日收益、换手率）
          ↓  calc_performance
绩效报告 + outputs/nav_curve.csv
```

### 10.5 下一步学习方向

| 进阶方向 | 关键词 |
|----------|--------|
| 更多因子 | 财务因子（市盈率、ROE）、情绪因子（新闻情绪）|
| 非线性模型 | LightGBM、LSTM、Transformer 预测 α |
| 风险模型 | Barra 多因子风险模型，组合优化 |
| 多空组合 | long-short portfolio，空头对冲市场风险 |
| 高频因子 | 分钟级别数据，订单簿特征 |
| 工业化框架 | Qlib 内置的 workflow（`qlib.workflow`）|

---

*本教程覆盖了一个完整量化策略从数据到回测的全链路。代码见配套脚本 `quant_workflow_from_scratch.py`。*
