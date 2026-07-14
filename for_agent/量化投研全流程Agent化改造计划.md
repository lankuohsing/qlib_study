# 量化投研全流程 Agent 化改造计划

> 目标：把现有量化投研脚本从“人手动跑脚本”改造成“Agent 可调用工具 + 可复用 skill + 可沉淀知识”的自动化研究系统。  
> 当前阶段：只制定计划，等待 review 后再实施。

## 1. 我对需求的理解

现有 `examples/quant_workflow_from_scratch.py` 是一条教学型、可解释的完整量化投研链路：

1. 用 Qlib 行情数据加载 CSI300 历史成分股。
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
   - nanobot 支持在 `config.json` 的 `tools.mcpServers` 中注册 stdio 或 HTTP MCP server。
   - 如果只在本机使用，stdio MCP 最简单、权限边界清晰。

3. **Skill / 知识层**
   - nanobot 的 skill 更适合承载“何时调用哪些工具、如何解释结果、如何避免研究偏差”的流程性知识。
   - 自定义 skill 放在 active workspace 的 `skills/<skill-name>/SKILL.md` 下即可被发现。
   - 大段经验、因子库、评估标准、反例与 checklist 应放到 skill 的 `references/` 中，避免主 `SKILL.md` 过长。

结论：**函数库负责计算，MCP 工具负责执行，skill 负责引导 Agent 决策，知识库负责约束和启发研究思路。**

## 3. 总体改造路线

建议分 5 个阶段做，每个阶段都有可验收产物。

### Phase 0：基线冻结与可复现

目标：先把现有脚本的行为固定下来，避免后续拆分时不知道是否改坏。

产出：

- 一份 `baseline` 运行说明：数据路径、依赖、命令、预期输出文件。
- 固化当前关键结果：`ic_analysis.csv`、`nav_curve.csv`、主要绩效指标。
- 明确现有脚本中的平台路径差异，例如 Windows 路径和 macOS 路径目前混用。
- 为后续测试准备小型 smoke test：能在较短时间内跑通少量股票、短时间窗口。

建议验收：

- 原脚本仍能运行。
- 拆分前后的核心数据 shape、IC 表、回测收益序列在允许误差内一致。

### Phase 1：把脚本拆成可调用 Python 研究库

目标：把 `quant_workflow_from_scratch.py` 从“一次性脚本”拆成 Agent 可组合调用的模块。

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

- `ResearchConfig`：provider_uri、universe、日期切分、topk、交易成本、输出目录。
- `MarketDataBundle`：raw_df、close/high/low/volume 宽表、membership_dict。
- `FactorSpec`：因子名、表达式/函数、窗口、方向、解释、依赖字段。
- `ResearchResult`：IC 表、选中因子、合成得分、回测结果、绩效表、产物路径。

建议函数：

- `init_qlib(config)`
- `load_universe_membership(config)`
- `load_ohlcv(config, instruments)`
- `compute_builtin_factors(raw_df, factor_specs)`
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
- 先覆盖 from_scratch 逻辑，不急着统一 Qlib 版本和 Barra 版本。
- `quant_workflow_barra.py` 中的优化逻辑可在 Phase 1 后半段拆到 `portfolio.py`。

### Phase 2：把研究库包装成 Agent 工具

目标：让 nanobot Agent 可以稳定调用量化研究能力，而不是直接改/跑整段脚本。

推荐工具形态：本地 stdio MCP server。

建议新增：

```text
agent_tools/
  quant_mcp_server.py
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

第二批 MCP tools：

| 工具 | 作用 |
|---|---|
| `quant_suggest_factor_variants` | 根据已有因子生成窗口、方向、变换候选 |
| `quant_evaluate_formula_factor` | 评估 Agent 提出的公式型因子 |
| `quant_run_walk_forward` | 做滚动训练/测试，降低固定切分偶然性 |
| `quant_run_regime_diagnostics` | 做市场 regime 诊断，解释因子方向翻转 |
| `quant_optimize_portfolio_barra` | 暴露 Barra 风险约束组合优化 |

工具设计原则：

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

- 拆分 from_scratch。
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
   - 推荐：stdio MCP server。
   - 备选：直接让 nanobot shell 调 Python CLI。
   - 备选：修改 nanobot 源码添加内置 tool。

2. **第一阶段是否只拆 from_scratch**
   - 推荐：先只拆 `quant_workflow_from_scratch.py`，验证一致后再纳入 Qlib/Barra 版本。

3. **因子定义格式**
   - 推荐：先支持 Python 函数式内置因子 + 简单 JSON spec。
   - 后续再支持 Qlib 表达式或安全受限的公式 DSL。

4. **输出目录规范**
   - 推荐：统一为 `outputs/runs/<run_id>/`，每次实验可追溯。

5. **自动化程度**
   - 推荐：初期让 Agent 提建议并运行有限实验；等验证稳定后再开放长循环自动探索。

6. **知识沉淀位置**
   - 推荐：项目内先维护 `for_agent/knowledge/`，确认内容后复制或同步到 nanobot workspace 的 `skills/quant-research/references/`。

## 6. 风险与注意事项

- 量化研究很容易过拟合，Agent 自动探索会放大这个风险。
- 当前测试期较短，且教程中已经指出 regime 差异明显，不能只看单次 test 表现。
- Qlib 数据路径目前在脚本间有 Windows/macOS 差异，必须配置化。
- Agent 工具返回内容要短，完整数据落文件，否则上下文会被 DataFrame 吃满。
- 公式型因子如果允许任意 Python 执行，会有安全风险；应先用白名单 DSL 或受控函数组合。
- Barra 优化依赖 `cvxpy`，环境和求解器可能带来安装/运行不稳定，需要作为可选能力。

## 7. 我建议下一步先实施的最小任务

如果你 review 后认可，我建议下一轮从 Milestone A 开始：

1. 新建 `quant_agent/` 研究库。
2. 从 `quant_workflow_from_scratch.py` 抽出数据加载、因子计算、预处理、IC、合成、回测、报告函数。
3. 保留原脚本行为，把主流程改为调用新库。
4. 增加一个短窗口 smoke test 或 dry-run 配置。
5. 生成拆分前后对齐说明。

这样做完后，MCP 工具和 skill 都会有明确的、可测试的调用目标。
