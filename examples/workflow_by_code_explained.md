# Qlib 量化研究全流程解析：workflow_by_code.py

本文面向有机器学习基础、初次接触量化投资的工程师，逐步拆解 `workflow_by_code.py` 所展示的 Qlib 完整研究链路。

---

## 整体流程概览

```
数据准备 → 特征工程(Alpha158) → 模型训练(LightGBM) → 信号生成 → 信号分析 → 回测 → 结果记录
```

Qlib 把量化研究拆成五个标准模块：**数据层、特征层、模型层、策略层、评估层**，各模块可独立替换，像积木一样组合。

---

## 第一步：环境初始化与数据下载

```python
provider_uri = "~/.qlib/qlib_data/cn_data"
GetData().qlib_data(target_dir=provider_uri, region=REG_CN, exists_skip=True)
qlib.init(provider_uri=provider_uri, region=REG_CN)
```

- `GetData().qlib_data(...)` 从微软提供的服务器下载 A 股历史行情数据，存储为 Qlib 自有的高性能二进制格式（`.bin`）。`exists_skip=True` 表示已下载则跳过。
- `qlib.init(...)` 相当于"注册数据源"，告诉后续所有模块去哪里读数据，以及使用哪个市场的交易日历（`REG_CN` = 中国 A 股）。

> **类比**：就像 PyTorch 里先设置好 `data_root`，之后的 `DataLoader` 才知道去哪读图片。

---

## 第二步：构建模型与数据集

```python
model   = init_instance_by_config(CSI300_GBDT_TASK["model"])
dataset = init_instance_by_config(CSI300_GBDT_TASK["dataset"])
```

`init_instance_by_config` 是 Qlib 的工厂函数，根据配置字典动态实例化对象，等价于：

```python
model   = LGBModel(loss="mse", learning_rate=0.0421, ...)
dataset = DatasetH(handler=Alpha158(...), segments={...})
```

### 模型：LGBModel（LightGBM）

使用调好超参数的 LightGBM，目标是预测股票未来收益（`loss="mse"`，均方误差回归）。

| 参数 | 值 | 说明 |
|---|---|---|
| `learning_rate` | 0.0421 | 学习率 |
| `num_leaves` | 210 | 树的叶子数，控制模型复杂度 |
| `max_depth` | 8 | 树的最大深度 |
| `lambda_l1/l2` | 205.7 / 580.9 | L1/L2 正则，防止过拟合 |

### 数据集：DatasetH + Alpha158

**Alpha158** 是 Qlib 内置的经典特征集，从 OHLCV（开高低收量）原始行情中衍生出 **158 个技术因子**，包括各时间窗口的收益率、换手率、波动率、价格动量等。

时间切分：

| 阶段 | 时间范围 | 用途 |
|---|---|---|
| train | 2008-01-01 ~ 2014-12-31 | 模型训练 |
| valid | 2015-01-01 ~ 2016-12-31 | 超参调优 / 早停 |
| test  | 2017-01-01 ~ 2020-08-01 | 最终评估 |

`fit_start_time` / `fit_end_time` 指向 train 段，确保特征归一化只用训练期数据（避免未来数据泄漏）。

---

## 第三步：配置回测参数

```python
port_analysis_config = {
    "executor": { "class": "SimulatorExecutor", ... },
    "strategy": { "class": "TopkDropoutStrategy", ... },
    "backtest": { "start_time": "2017-01-01", ... },
}
```

这一步只是**声明配置**，不执行任何计算。回测的三个核心组件：

### 执行器（Executor）：SimulatorExecutor

模拟订单撮合与资金管理，`time_per_step="day"` 表示以天为频率执行调仓。

### 策略（Strategy）：TopkDropoutStrategy

这是一个经典的量化选股策略：

- `topk=50`：每期持有模型预测收益排名前 50 的股票（等权重）
- `n_drop=5`：每次调仓时，从当前持仓中剔除排名下滑最多的 5 只，再补入 5 只新晋 top50 股票

这个设计减少换手率（降低交易成本），同时保持持仓与信号同步。

### 回测参数

| 参数 | 值 | 说明 |
|---|---|---|
| `account` | 1 亿元 | 初始资金 |
| `benchmark` | SH000300（沪深300）| 对比基准 |
| `deal_price` | 收盘价成交 | 简化假设 |
| `open_cost` | 0.05% | 买入手续费 |
| `close_cost` | 0.15% | 卖出手续费（含印花税） |
| `limit_threshold` | 9.5% | 涨跌停板限制，超过则不可交易 |

---

## 第四步：数据预览（可选）

```python
example_df = dataset.prepare("train")
print(example_df.head())
```

`prepare("train")` 触发特征计算，返回训练集的 DataFrame，索引为 `(datetime, instrument)`（时间 × 股票代码），列为 158 个 Alpha 因子。这一步展示了 Dataset 可以独立于模型使用。

---

## 第五步：实验记录 + 训练 + 预测（核心）

```python
with R.start(experiment_name="workflow"):
    R.log_params(**flatten_dict(CSI300_GBDT_TASK))
    model.fit(dataset)
    R.save_objects(**{"params.pkl": model})

    recorder = R.get_recorder()
    sr = SignalRecord(model, dataset, recorder)
    sr.generate()
```

`R`（Recorder）是 Qlib 的实验管理系统，类似 MLflow：

- `R.start(...)` 创建一次实验运行，自动分配 run_id
- `R.log_params(...)` 记录所有超参数，方便事后复现
- `model.fit(dataset)` 训练 LightGBM，内部自动使用 train/valid 两段数据
- `R.save_objects(...)` 将训练好的模型序列化保存
- `SignalRecord.generate()` 用训练好的模型对 **test 段所有股票** 生成预测分（alpha 信号），并持久化

---

## 第六步：信号分析

```python
sar = SigAnaRecord(recorder)
sar.generate()
```

`SigAnaRecord` 对预测信号做统计分析，常见指标包括：

- **IC（信息系数）**：预测值与未来真实收益的 Spearman/Pearson 相关系数，衡量信号的方向性
- **ICIR**：IC 均值 / IC 标准差，衡量信号稳定性
- **分组收益**：将股票按预测分五分位，看各分位组的收益分布

这一步帮助判断模型预测信号的质量，**在进入回测前先验证信号是否有效**。

---

## 第七步：组合回测分析

```python
par = PortAnaRecord(recorder, port_analysis_config, "day")
par.generate()
```

`PortAnaRecord` 将信号、策略、执行器组合起来跑完整回测，生成绩效报告，包括：

| 指标 | 说明 |
|---|---|
| 年化收益率 | 策略 vs 基准 |
| 最大回撤 | 资金曲线从高点的最大跌幅 |
| 夏普比率 | 风险调整后收益 |
| 换手率 | 每期调仓比例 |
| 超额收益（Alpha）| 相对沪深300的超额部分 |

结果自动保存到 Recorder，可通过 `qrun` 的 web UI 或代码接口查询。

---

## 完整数据流示意

```
A股行情数据 (OHLCV)
        ↓
    Alpha158 特征工程（158个技术因子）
        ↓
    LightGBM 训练（2008-2014）→ 验证（2015-2016）
        ↓
    预测信号（2017-2020，每只CSI300股票每日一个预测分）
        ↓
   ┌────────────────┐
   │  信号分析       │  ← IC / ICIR / 分组收益
   └────────────────┘
   ┌────────────────┐
   │  组合回测       │  ← TopkDropout策略 + SimulatorExecutor
   └────────────────┘
        ↓
    绩效报告（年化收益、夏普、最大回撤…）
```

---

## 关键概念对照（ML → 量化）

| 机器学习概念 | 量化对应概念 | 说明 |
|---|---|---|
| 特征工程 | Alpha 因子 | 从行情构造的预测特征 |
| 回归目标 | 未来N日收益率 | 模型要预测的 label |
| 模型预测值 | 信号 / alpha 分 | 用于股票排序 |
| 测试集评估 | 信号分析（IC） | 验证预测质量 |
| 模型上线 | 回测 | 模拟交易验证策略可行性 |
| 准确率 / AUC | 夏普比率 / 年化收益 | 最终业务指标 |
