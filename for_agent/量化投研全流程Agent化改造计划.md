# 量化投研全流程 Agent 化改造计划

> 目标：把现有量化投研脚本从“人手动跑脚本”改造成“Agent 可调用工具 + 可复用 skill + 可沉淀知识”的自动化研究系统。  
> 当前阶段：已固定原始行情 CSV，并已完成第一个“量价/行情类基础因子计算”skill/scripts；下一步推进“因子预处理/股票池过滤”skill/scripts。

续聊 checkpoint：

```text
for_agent/量化投研Agent化改造checkpoint.md
```

下次重新开启对话时，可先阅读该 checkpoint，再阅读本文档继续推进。

## 0. 最新进展：先把数据源从 Qlib 固化为 CSV

我们已经明确一个重要原则：**后续 Agent 化流程不应默认从 Qlib 动态取数，而应从一个固定的 CSV 数据集读取原始行情**。Qlib 在这里只作为一次性历史数据导出器，模拟真实场景中“外部数据源已经落地成文件/表”的状态。

已经新增导出脚本：

```text
examples/export_raw_ohlcv_to_csv.py
```

该脚本导出的是 `examples/quant_workflow_from_scratch.py` 中这一行执行后的 `raw_df`：

```python
raw_df = raw_df.swaplevel().sort_index()
```

已生成的固定数据集：

```text
datasets/exported/raw_ohlcv_csi300_20140601_20200801.csv
datasets/exported/raw_ohlcv_csi300_20140601_20200801_membership.csv
datasets/exported/raw_ohlcv_csi300_20140601_20200801_metadata.json
```

主数据集信息：

```text
shape:       (757978, 5)
index:       ["datetime", "instrument"]
columns:     ["open", "high", "low", "close", "volume"]
date range:  2014-06-03 ~ 2020-07-31
instruments: 549
```

后续读取方式应统一为：

```python
raw_df = (
    pd.read_csv("datasets/exported/raw_ohlcv_csi300_20140601_20200801.csv", parse_dates=["datetime"])
    .set_index(["datetime", "instrument"])
    .sort_index()
)
```

成员资格过滤所需的 `membership_dict` 后续也应从 `raw_ohlcv_csi300_20140601_20200801_membership.csv` 还原，而不是再调用 `D.list_instruments()`。

注意：`datasets/` 已在 `.gitignore` 中忽略，因此大 CSV 默认不会进入 git；导出脚本会进入 git，数据文件作为本地可复现产物保留。

## 0.1 最新进展：先用 skill/scripts 固化单阶段流程

在进入完整研究库和 MCP 服务之前，当前采用更轻量的过渡路径：

```text
先把单个研究阶段整理成 skill + scripts
  -> 试跑并对齐原流程输出
  -> 稳定后再抽象到 quant_agent/ 研究库
  -> 最后再封装成 MCP tool
```

已完成第一个阶段：

```text
for_agent/quant-price-volume-factor-mining/
  SKILL.md
  scripts/compute_price_volume_ohlcv_factors.py
```

该 skill 负责从标准长表行情 CSV 中计算量价/行情类基础因子：

```text
MOM_5D, MOM_20D, VOL_20D, TURN_5D, MA_DEV, DAY_RANGE, PRICE_POS, LABEL
```

设计原则：

- 输入路径必须通过 `--raw-csv` 显式传入。
- 不依赖 Qlib，不依赖某个离线实验脚本名。
- 当前只读取原始 OHLCV CSV，不读取 `membership.csv` 和 `metadata.json`。
- 输出目录不存在时自动创建。
- 常见错误会输出中文提示和排查建议，避免 Agent 只看到 traceback。
- 输出完整因子长表、预览 CSV 和诊断 JSON。

已验证：

```text
宽表 close shape = (1473, 549)
因子长表 shape = (808677, 8)
完整输出读回 shape = (808677, 10)
Skill is valid!
```

下一步应继续用同样方式做“因子预处理/股票池过滤”阶段，并开始使用 `membership.csv`。

## 0.2 当前数据文件职责边界

为了避免后续 Agent 混淆输入文件，当前约定如下：

| 文件 | 当前是否被量价因子脚本使用 | 规划用途 |
|---|---:|---|
| `raw_ohlcv_csi300_20140601_20200801.csv` | 是 | 基础行情输入，字段固定为 `open/high/low/close/volume` |
| `raw_ohlcv_csi300_20140601_20200801_membership.csv` | 否 | 下一阶段用于成员资格过滤、股票池还原 |
| `raw_ohlcv_csi300_20140601_20200801_metadata.json` | 否 | 后续用于数据集版本、导出参数、实验可追溯 |

因此，“量价/行情类基础因子计算”阶段的边界是：

```text
raw OHLCV CSV
  -> 计算 MOM_5D/MOM_20D/VOL_20D/TURN_5D/MA_DEV/DAY_RANGE/PRICE_POS/LABEL
  -> 输出未清洗的因子长表
```

它不负责：

- 成员资格过滤。
- 截面去极值和标准化。
- 删除含 NaN 的样本。
- train/valid/test 切分。
- 数据版本 registry。

这些能力应放到后续独立阶段中，避免一个脚本同时承担太多语义。

## 1. 我对需求的理解

现有 `examples/quant_workflow_from_scratch.py` 是一条教学型、可解释的完整量化投研链路：

1. 原始版本用 Qlib 行情数据加载 CSI300 历史成分股；现在先把这一步固化成 CSV 数据集。
2. 基于 OHLCV 手写计算基础因子。
3. 做截面去极值、标准化、成员资格过滤和 train/valid/test 切分。
4. 用 IC / ICIR 分析因子有效性。
5. 用等权和线性回归做因子合成。
6. 用 Top-K 等权组合构建。
7. 做简化回测、绩效汇总和结果落盘。

已有扩展示例还包括：

- `examples/quant_workflow_qlib.py`：把因子表达式和模型训练切到 Qlib 风格，便于与工业化框架对齐。
- `examples/quant_workflow_barra.py`：把组合构建替换为 Barra 风险模型 + 均值方差优化。
- `examples/quant_workflow_from_scratch_tutorial.md`：解释了流程、数据形态、关键注意事项和常见陷阱。

用户希望后续的 nanobot Agent 能：

- 自动完成量化研究流程。
- 调用被抽象出来的函数或工具。
- 使用通用 skill 理解和执行研究流程。
- 通过知识沉淀学习人工挖掘经验。
- 重点激发模型在因子挖掘、因子合成等核心环节的创新。

## 2. nanobot 侧的落地判断

根据本地 `D:\projects\github\official\nanobot` 文档和源码结构，建议采用三层设计：

1. **Python 研究库层**
   - 把当前脚本拆成普通 Python 模块和函数。
   - 这是最稳定、最容易测试、最适合复用的底座。

2. **Agent 工具层**
   - 优先通过 MCP server 暴露量化研究工具给 nanobot。
   - Python 侧建议使用官方 `mcp` SDK 的 `FastMCP`，不要手写 MCP 协议。
   - nanobot 支持在 `config.json` 的 `tools.mcpServers` 中注册 stdio 或 HTTP MCP server。
   - 如果只在本机使用，stdio MCP 最简单、权限边界清晰。
   - 如果是量化研究员独立部署服务，应优先交付 HTTP MCP endpoint，例如 `https://host/mcp`。

3. **Skill / 知识层**
   - nanobot 的 skill 更适合承载“何时调用哪些工具、如何解释结果、如何避免研究偏差”的流程性知识。
   - 自定义 skill 放在 active workspace 的 `skills/<skill-name>/SKILL.md` 下即可被发现。
   - 大段经验、因子库、评估标准、反例与 checklist 应放到 skill 的 `references/` 中，避免主 `SKILL.md` 过长。

结论：**函数库负责计算，MCP 工具负责执行，skill 负责引导 Agent 决策，知识库负责约束和启发研究思路。**

## 3. 总体改造路线

建议分 5 个阶段做，每个阶段都有可验收产物。

### Phase 0A：固定原始数据集（已完成）

目标：把 Qlib 获取到的原始 OHLCV 行情固定成 CSV，让后续研究流程脱离 Qlib 数据接口。

已完成产出：

- `examples/export_raw_ohlcv_to_csv.py`
- `datasets/exported/raw_ohlcv_csi300_20140601_20200801.csv`
- `datasets/exported/raw_ohlcv_csi300_20140601_20200801_membership.csv`
- `datasets/exported/raw_ohlcv_csi300_20140601_20200801_metadata.json`

验收结果：

- CSV 读回后 shape 为 `(757978, 5)`。
- 索引层级为 `["datetime", "instrument"]`。
- 字段为 `open/high/low/close/volume`。
- 与原脚本中 `raw_df.swaplevel().sort_index()` 后的数据形态一致。

后续要求：

- 研究主流程默认从 CSV 读取原始行情。
- Qlib 相关代码只保留在数据导出脚本或可选数据适配器中。
- 成员资格过滤从 `membership.csv` 读取，不再依赖 Qlib。

### Phase 0B：基线冻结与可复现

目标：先把现有脚本的行为固定下来，避免后续拆分时不知道是否改坏。

产出：

- 一份 `baseline` 运行说明：CSV 数据路径、依赖、命令、预期输出文件。
- 固化当前关键结果：`ic_analysis.csv`、`nav_curve.csv`、主要绩效指标。
- 明确现有脚本中的平台路径差异，并将数据路径配置化。
- 为后续测试准备小型 smoke test：能在较短时间内跑通少量股票、短时间窗口。

建议验收：

- 从 Qlib 读取的原脚本仍能运行。
- 从 CSV 读取的新流程能跑出同等结果。
- 拆分前后的核心数据 shape、IC 表、回测收益序列在允许误差内一致。

### Phase 0C：单阶段 skill/scripts 化（进行中）

目标：在正式抽象 `quant_agent/` 研究库前，先把原流程中的关键阶段拆成 Agent 可稳定执行的脚本，并配套 skill 说明。

已完成：

- `for_agent/quant-price-volume-factor-mining/SKILL.md`
- `for_agent/quant-price-volume-factor-mining/scripts/compute_price_volume_ohlcv_factors.py`

已对齐的关键结果：

```text
宽表 close shape = (1473, 549)
因子长表 shape = (808677, 8)
完整输出读回 shape = (808677, 10)
Skill is valid!
```

脚本健壮性要求已纳入当前阶段标准：

- 所有输入路径显式传参，不写死真实数据路径。
- 输出目录不存在时自动创建。
- 常见异常输出中文错误和排查建议。
- 调试时可用 `--debug` 打印完整 traceback。
- 大结果落盘，屏幕只输出摘要、诊断和产物路径。

下一步：

- 新建“因子预处理/股票池过滤”skill/scripts。
- 输入上一步生成的因子长表和 `membership.csv`。
- 复现截面 MAD 去极值、Z-Score 标准化、dropna、成员资格过滤。
- 输出清洗后的因子表、预览 CSV、诊断 JSON。

### Phase 1：把脚本拆成可调用 Python 研究库

目标：把 `quant_workflow_from_scratch.py` 从“一次性脚本”拆成 Agent 可组合调用的模块。

当前补充策略：在正式抽库前，先用 `for_agent/<skill-name>/scripts/` 固化关键阶段脚本；当阶段脚本稳定后，再把共用逻辑沉入 `quant_agent/`。

建议目录：

```text
qlib_study/
  quant_agent/
    __init__.py
    config.py
    data.py
    factors.py
    preprocess.py
    analysis.py
    combine.py
    portfolio.py
    backtest.py
    report.py
    pipeline.py
```

建议抽象的核心对象：

- `ResearchConfig`：raw_csv_path、membership_csv_path、日期切分、topk、交易成本、输出目录。
- `MarketDataBundle`：raw_df、close/high/low/volume 宽表、membership_dict。
- `FactorSpec`：因子名、表达式/函数、窗口、方向、解释、依赖字段。
- `ResearchResult`：IC 表、选中因子、合成得分、回测结果、绩效表、产物路径。

建议函数：

- `load_raw_ohlcv_csv(config)`
- `load_membership_csv(config)`
- `build_market_data_bundle(raw_df, membership_df)`
- `compute_builtin_factors(raw_df, factor_specs)`
- `preprocess_factor_table(factor_df, membership_df, config)`
- `build_label(close, horizon=1)`
- `preprocess_factors(factor_df, membership_dict, method)`
- `split_segments(clean_df, config)`
- `calc_ic_table(df, factor_cols, label_col)`
- `select_factors(ic_table, threshold)`
- `combine_equal_weight(df, factor_cols)`
- `fit_linear_combiner(train_df, factor_cols)`
- `score_linear_combiner(df, model, factor_cols)`
- `build_topk_holdings(score, topk)`
- `run_topk_backtest(score, returns, topk, transaction_cost)`
- `calc_performance(return_series)`
- `save_research_outputs(result, output_dir)`
- `run_research_pipeline(config, factor_specs, combiner_specs, portfolio_specs)`

注意点：

- 保留教学脚本，但让它调用新库，避免丢掉学习价值。
- 新库主路径必须是 CSV-first，不能在核心流程中隐式调用 Qlib。
- Qlib 获取数据的逻辑保留在 `examples/export_raw_ohlcv_to_csv.py`，作为“数据快照生成器”。
- 当前已完成量价/行情类基础因子计算 skill/scripts，可作为后续抽库的第一份代码来源。
- 下一阶段因子预处理/股票池过滤应使用 `membership.csv`。
- 先覆盖 from_scratch 逻辑，不急着统一 Qlib 版本和 Barra 版本。
- `quant_workflow_barra.py` 中的优化逻辑可在 Phase 1 后半段拆到 `portfolio.py`。

### Phase 2：把研究库包装成 Agent 工具

目标：让 nanobot Agent 可以稳定调用量化研究能力，而不是直接改/跑整段脚本。

推荐工具形态：

- 本地自用：stdio MCP server。
- 跨团队协作：HTTP MCP server，量化研究员部署服务并提供 endpoint。
- Python 实现：优先使用官方 MCP SDK `mcp.server.fastmcp.FastMCP`。

建议新增：

```text
agent_tools/
  quant_mcp_server.py
```

工具注册示意：

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("quant-research")

@mcp.tool()
def describe_dataset(dataset_id: str) -> dict:
    """Describe an available fixed market dataset."""
    ...
```

nanobot 配置示意：

```json
{
  "tools": {
    "mcpServers": {
      "quant-research": {
        "url": "http://127.0.0.1:8767/mcp"
      }
    }
  }
}
```

如果是本地 stdio MCP，则配置为：

```json
{
  "tools": {
    "mcpServers": {
      "quant-research": {
        "command": "D:\\ProgramData\\miniforge3\\envs\\py312\\python.exe",
        "args": ["D:\\projects\\github\\qlib_study\\agent_tools\\quant_mcp_server.py"]
      }
    }
  }
}
```

第一批 MCP tools：

| 工具 | 作用 | 输入 | 输出 |
|---|---|---|---|
| `quant_run_baseline_research` | 跑一条标准全流程 | config、因子列表、合成方式、组合方式 | run_id、绩效摘要、输出目录 |
| `quant_compute_factor_ic` | 单独评估候选因子 | 因子定义、时间段、股票池 | IC/ICIR、稳定性、覆盖率 |
| `quant_compare_factor_sets` | 比较多个因子组合 | factor_set 列表、评估配置 | 排名、相关性、冗余度 |
| `quant_run_backtest` | 对得分或信号做回测 | score 文件或 score spec | 绩效、换手、净值曲线 |
| `quant_get_run_artifact` | 读取某次运行产物 | run_id、artifact_name | csv/json/markdown 摘要 |
| `quant_list_builtin_factors` | 列出内置因子 | 可选分类 | 因子清单和解释 |
| `quant_describe_dataset` | 描述固定 CSV 数据集 | dataset_id 或路径 | 时间范围、字段、股票数、缺失率 |

第二批 MCP tools：

| 工具 | 作用 |
|---|---|
| `quant_suggest_factor_variants` | 根据已有因子生成窗口、方向、变换候选 |
| `quant_evaluate_formula_factor` | 评估 Agent 提出的公式型因子 |
| `quant_run_walk_forward` | 做滚动训练/测试，降低固定切分偶然性 |
| `quant_run_regime_diagnostics` | 做市场 regime 诊断，解释因子方向翻转 |
| `quant_optimize_portfolio_barra` | 暴露 Barra 风险约束组合优化 |

工具设计原则：

- 工具默认读取固定 CSV 数据集，不默认触发 Qlib 导出。
- 工具服务端由 MCP SDK 负责工具发现和调用协议；nanobot 作为 MCP client 已内置 `initialize`、`list_tools`、`call_tool` 等逻辑。
- 研究员交付 HTTP MCP 服务时，应同时交付 endpoint、鉴权方式、工具清单、输入输出说明和示例调用。
- 每个工具返回结构化 JSON 摘要，同时把大文件落到 `outputs/runs/<run_id>/`。
- 所有工具都要返回 `warnings`，例如样本不足、NaN 过多、换手过高、测试期太短。
- 工具不要把大 DataFrame 直接塞给模型，避免上下文爆炸。
- 每次运行保存 config、因子定义、代码版本信息、输出文件索引，保证可追溯。

### Phase 3：为 nanobot 编写量化研究 skill

目标：让 Agent 不只是“会调用工具”，还知道什么时候该调用、如何解释、如何避免常见错误。

建议 skill 结构：

```text
<nanobot-workspace>/skills/quant-research/
  SKILL.md
  references/
    workflow.md
    factor-engineering.md
    factor-validation.md
    factor-combination.md
    portfolio-construction.md
    backtest-checklist.md
    anti-overfit-checklist.md
    output-interpretation.md
```

`SKILL.md` 只保留核心流程：

1. 明确用户研究目标。
2. 选择数据区间、股票池和基准。
3. 先跑基线。
4. 再提出因子候选。
5. 逐个评估 IC、覆盖率、稳定性、相关性。
6. 组合低相关、逻辑不同的因子。
7. 做回测、换手和成本分析。
8. 做 walk-forward 或 regime 检查。
9. 给出结论、风险和下一步实验。

references 负责沉淀具体经验：

- 因子挖掘经验：动量、反转、波动率、量价、价量背离、流动性、跳空、日内位置等。
- 因子变换经验：rank、zscore、winsorize、分位数、非线性截断、窗口族。
- 因子合成经验：等权、IC 加权、滚动 IC 加权、线性模型、正则模型、树模型、风险中性化。
- 验证经验：IC 均值、ICIR、IC 胜率、分组收益、换手、覆盖率、相关性、样本外稳定。
- 反过拟合 checklist：固定 test、walk-forward、参数搜索预算、避免看未来、避免在 test 上反复调参。

### Phase 4：引入“研究自动化闭环”

目标：让 Agent 能从“跑一次流程”升级为“提出假设 -> 实验 -> 评估 -> 反思 -> 下一轮”。

建议实现一个实验循环：

1. `baseline`：跑当前 7 因子基线。
2. `hypothesis`：Agent 根据已有结果提出 3-5 个候选因子或合成方法。
3. `screen`：快速 IC 筛选，淘汰明显无效或覆盖率差的候选。
4. `combine`：合成低相关且逻辑互补的候选。
5. `backtest`：跑 Top-K / Barra / 其他组合方式。
6. `diagnose`：分析收益来源、换手、回撤、regime 敏感性。
7. `memo`：生成研究备忘录，记录成功、失败和下一轮建议。

建议每次实验保存：

```text
outputs/runs/<run_id>/
  config.json
  factor_specs.json
  ic_analysis.csv
  factor_corr.csv
  nav_curve.csv
  backtest_metrics.json
  holdings_sample.csv
  research_memo.md
  warnings.json
```

### Phase 5：增强创新能力，但加上护栏

目标：鼓励 Agent 在因子挖掘和合成上创新，同时不让它把过拟合当发现。

可以开放给 Agent 的创新空间：

- 窗口族：`1/3/5/10/20/60` 日。
- 因子方向：动量 vs 反转。
- 非线性变换：rank、分位数、clip、符号函数、交互项。
- 价量交互：高量上涨、放量下跌、量价背离。
- 风险调整：波动率归一、Beta/风格中性。
- 合成方法：IC 加权、滚动 IC 加权、Ridge/Lasso、LightGBM、分 regime 权重。
- 组合方法：Top-K、Top-K dropout、权重连续化、Barra 风险约束。

必须加的护栏：

- Agent 每轮最多提出有限数量候选，避免参数爆炸。
- 所有候选必须有经济直觉说明。
- 不允许根据 test 表现反复调参。
- 优先看 walk-forward 和稳定性，而不是单次年化收益。
- 对高换手策略强制输出成本敏感性。
- 对收益好但 IC 差的策略标记为可疑。

## 4. 建议的实施顺序

我建议先做最小可用闭环，而不是一次性把所有东西都做完。

### Milestone A：可复现函数库

范围：

- 将数据入口从 Qlib 改为 CSV。
- 先完成关键阶段的 skill/scripts 化：基础因子计算已完成，下一步是因子预处理/股票池过滤。
- 再拆分 from_scratch。
- 原脚本改为调用库。
- 增加 smoke test。
- 产出 baseline 对齐报告。

价值：

- 后续所有 Agent 工具都有可靠底座。

### Milestone B：MCP 工具最小集

范围：

- 实现 `quant_run_baseline_research`。
- 实现 `quant_compute_factor_ic`。
- 实现 `quant_get_run_artifact`。
- 给出 nanobot `config.json` 的 MCP 配置片段。

价值：

- nanobot 可以开始真实调用量化研究流程。

### Milestone C：quant-research skill

范围：

- 写 `SKILL.md`。
- 写 3-4 个核心 references：workflow、factor-validation、anti-overfit、output-interpretation。

价值：

- Agent 有了流程指导和研究护栏。

### Milestone D：自动实验循环

范围：

- 增加候选因子公式评估工具。
- 增加 factor set 比较工具。
- 增加研究 memo 输出。

价值：

- Agent 能从“执行工具”变成“自动研究助理”。

### Milestone E：高级研究能力

范围：

- Barra 优化工具化。
- walk-forward。
- regime diagnostics。
- 更多合成方法。

价值：

- 系统进入更接近真实投研研究台的状态。

## 5. 需要你 review 的决策点

1. **工具接入方式**
   - 推荐：本地开发用 stdio MCP server；跨团队协作用 HTTP MCP server。
   - Python 推荐使用官方 `mcp` SDK 的 `FastMCP`。
   - 备选：直接让 nanobot shell 调 Python CLI。
   - 备选：修改 nanobot 源码添加内置 tool。

2. **量化研究员工具交付边界**
   - 推荐：研究员交付 MCP server，而不是只交付零散函数。
   - 交付物应包含 MCP endpoint 或 stdio 启动命令、工具清单、参数 schema、返回 JSON 结构、鉴权方式和示例调用。
   - Agent 侧不实现自定义 client；nanobot 已自带 MCP client。

3. **数据源边界**
   - 已决定：主研究流程从 CSV 读取。
   - Qlib 只作为一次性导出器或可选数据适配器。
   - 下一步需要决定：是否把 `datasets/exported/*.json` 元数据作为 dataset registry 的雏形。

4. **第一阶段是否只拆 from_scratch**
   - 推荐：先只拆 `quant_workflow_from_scratch.py`，验证一致后再纳入 Qlib/Barra 版本。

5. **因子定义格式**
   - 推荐：先支持 Python 函数式内置因子 + 简单 JSON spec。
   - 后续再支持 Qlib 表达式或安全受限的公式 DSL。

6. **输出目录规范**
   - 推荐：统一为 `outputs/runs/<run_id>/`，每次实验可追溯。

7. **自动化程度**
   - 推荐：初期让 Agent 提建议并运行有限实验；等验证稳定后再开放长循环自动探索。

8. **知识沉淀位置**
   - 推荐：项目内先维护 `for_agent/knowledge/`，确认内容后复制或同步到 nanobot workspace 的 `skills/quant-research/references/`。

## 6. 风险与注意事项

- 量化研究很容易过拟合，Agent 自动探索会放大这个风险。
- 当前测试期较短，且教程中已经指出 regime 差异明显，不能只看单次 test 表现。
- Qlib 数据路径目前在脚本间有 Windows/macOS 差异；主流程 CSV 化后，这个风险会收敛到导出脚本。
- 固定 CSV 是数据快照，不会自动反映数据修正、复权规则变化或股票池更新；后续需要 dataset version。
- Agent 工具返回内容要短，完整数据落文件，否则上下文会被 DataFrame 吃满。
- HTTP MCP 对外部署时需要考虑鉴权、网络访问控制、超时、日志审计和数据权限。
- 不建议让 Agent 直接调用任意 Python 代码；应通过受控 MCP tool 暴露有限能力。
- 公式型因子如果允许任意 Python 执行，会有安全风险；应先用白名单 DSL 或受控函数组合。
- Barra 优化依赖 `cvxpy`，环境和求解器可能带来安装/运行不稳定，需要作为可选能力。

## 7. 我建议下一步先实施的最小任务

如果你 review 后认可，我建议下一轮继续按“单阶段 skill/scripts 化”推进：

1. 新建“因子预处理/股票池过滤”skill。
2. 编写脚本，输入上一步生成的因子长表和 `membership.csv`。
3. 复现原流程中的截面 MAD 去极值、Z-Score 标准化、dropna、成员资格过滤。
4. 输出 clean factor 表、预览 CSV、诊断 JSON。
5. 对齐原脚本 Step 3 的屏幕输出。

之后再进入完整 Milestone A：

1. 新建 `quant_agent/` 研究库。
2. 先实现 `load_raw_ohlcv_csv()` 和 `load_membership_csv()`，把固定 CSV 作为唯一默认数据入口。
3. 从 `quant_workflow_from_scratch.py` 抽出因子计算、预处理、IC、合成、回测、报告函数。
4. 保留原脚本行为，把主流程改为调用新库；同时新增一个 CSV-first 入口脚本。
5. 增加一个短窗口 smoke test 或 dry-run 配置。
6. 生成“Qlib 原始脚本 vs CSV-first 新流程”的对齐说明。

这样做完后，MCP 工具和 skill 都会有明确的、可测试的调用目标。
