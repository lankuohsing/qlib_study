# 量化投研全流程 Agent 化改造计划

> 目标：把现有量化投研脚本从“人手动跑脚本”改造成“Agent 可调用工具 + 可复用 skill + 可沉淀知识”的自动化研究系统。  
> 当前阶段：已固定原始行情 CSV，并已完成“量价/行情类基础因子计算”“因子预处理/股票池过滤”“因子有效性分析（IC / ICIR）”“因子合成”“组合构建 + 回测”五个相邻 skill/scripts；下一步建议做上层 workflow skill，或开始抽象 `quant_agent/` 研究库和 CSV-first pipeline 入口。

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
raw_df[["open", "high", "low", "close", "volume"]] = raw_df[
    ["open", "high", "low", "close", "volume"]
].astype("float32")
```

这里显式转回 `float32` 是当前基线对齐要求。Qlib 原始 `.bin` 读出是 `float32`；如果固定 CSV 被 pandas 默认读成 `float64`，会在 `PRICE_POS = (close - low) / (high - low + 1e-9)` 这类对价格并列值敏感的因子上引入细微差异，进而影响 Spearman IC 的可计算日期数量。

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

该阶段已与后续因子预处理阶段打通，输出的因子长表可作为下一阶段输入。

已完成第二个阶段：

```text
for_agent/quant-factor-preprocessing/
  SKILL.md
  scripts/preprocess_cross_sectional_factors.py
```

该 skill 负责对上游因子长表做每日截面 MAD 去极值、Z-Score 标准化、dropna、成员资格过滤和 `train/valid/test` 时间切分。

设计原则：

- 当前项目示例数据可无参数直接运行，方便 IDE 调试。
- 换数据集或自动化批量运行时，仍可显式传入 `--factor-csv` 和 `--membership-csv`。
- 不绑定固定因子名；默认把除 `datetime`、`instrument`、`LABEL` 外的所有列识别为因子列。
- 如需手动限制因子列，可传 `--factor-cols`。
- 使用 `membership.csv` 过滤当日真实股票池成员。
- 对 `train/valid/test` 日期范围做强校验：日期格式、起止顺序、三段递增不重叠、落在清洗后数据范围内、切分后非空。
- 输出 clean 全量表、三段切分表、预览 CSV 和诊断 JSON。

已验证：

```text
处理前 shape = (808677, 8)
处理后 shape = (388691, 8)
train shape = (180841, 8)
valid shape = (67735, 8)
test  shape = (105900, 8)
Skill is valid!
```

关于多个相邻 skill 的编排判断：

- 短期可以直接在 nanobot 中输入明确指令，让它依次运行量价因子计算、因子预处理、IC 分析和因子合成 skill，并把上游输出路径传给下游脚本。
- 长期建议新增一个轻量的上层 workflow skill，只负责编排多个阶段，不重复实现计算。
- 现阶段仍不急于 MCP 化；等脚本稳定并抽成 `quant_agent/` 函数库后，再封装 MCP tool 更稳。

已完成第三个阶段：

```text
for_agent/quant-factor-ic-analysis/
  SKILL.md
  scripts/analyze_factor_ic.py
```

该 skill 负责对预处理后的因子样本计算每日截面 Spearman IC、IC 均值、IC 标准差、ICIR、IC 正值占比和计算日数，并按阈值筛选候选有效因子。

设计原则：

- 当前项目示例数据可无参数直接运行，默认读取预处理阶段输出的 `train.csv.gz`。
- 换数据集或自动化批量运行时，可显式传入 `--input-csv`。
- 不绑定固定因子名；默认把除 `datetime`、`instrument`、`LABEL` 外的所有列识别为因子列。
- 如需手动限制因子列，可传 `--factor-cols`。
- 输出 IC 汇总、每日 IC 明细、因子相关矩阵、候选因子 JSON、预览 CSV 和诊断 JSON。

已验证：

```text
IC 分析样本段 = 700 个交易日，431 只股票
MOM_5D     IC均值=-0.0672  IC标准差=0.2200  ICIR=-0.3055  IC>0占比=0.371  计算日数=700
MOM_20D    IC均值=-0.0495  IC标准差=0.2249  ICIR=-0.2199  IC>0占比=0.404  计算日数=700
VOL_20D    IC均值=-0.0231  IC标准差=0.2535  ICIR=-0.0913  IC>0占比=0.426  计算日数=700
TURN_5D    IC均值=-0.0323  IC标准差=0.1631  ICIR=-0.1981  IC>0占比=0.390  计算日数=700
MA_DEV     IC均值=-0.0595  IC标准差=0.2338  ICIR=-0.2544  IC>0占比=0.407  计算日数=700
DAY_RANGE  IC均值=-0.0557  IC标准差=0.2187  ICIR=-0.2548  IC>0占比=0.371  计算日数=700
PRICE_POS  IC均值=-0.0293  IC标准差=0.1858  ICIR=-0.1577  IC>0占比=0.400  计算日数=690
Skill is valid!
```

关于 `PRICE_POS` 的一次重要对齐修正：

- 旧 CSV 流水线曾得到 `PRICE_POS IC均值=-0.0276, ICIR=-0.1470, 计算日数=693`。
- 原始脚本在跳过不可定义 Spearman IC 后得到 `PRICE_POS IC均值=-0.0293, ICIR=-0.1577, 计算日数=690`。
- 根因不是 IC 分析脚本，而是固定 CSV 读入时 pandas 默认使用 `float64`，打散了 Qlib `float32` 数据中原本相同的 `PRICE_POS` 截面值。
- 已在 `for_agent/quant-price-volume-factor-mining/scripts/compute_price_volume_ohlcv_factors.py` 中将 OHLCV 列显式转为 `float32`，并重新跑通三阶段。
- 后续任何 CSV-first 数据入口都应保留这个 dtype 对齐要求，除非重新定义并冻结新的基线。

已完成第四个阶段：

```text
for_agent/quant-factor-combination/
  SKILL.md
  scripts/
    combine_factor_scores.py
    combine_equal_weight_scores.py
    combine_linear_regression_scores.py
    factor_combination_common.py
```

该 skill 负责把多个候选因子合成为单一选股得分。当前支持：

```text
equal_weight
linear_regression
```

脚本组织方式：

- `combine_equal_weight_scores.py`：等权合成方法脚本。
- `combine_linear_regression_scores.py`：LinearRegression 合成方法脚本。
- `factor_combination_common.py`：公共读取、校验、IC 评估和落盘函数。
- `combine_factor_scores.py`：轻量批量入口，只负责按 `--methods` 调用具体方法脚本。

这次形成的约定：

- 因子合成作为一个 skill 管理。
- 每种合成方法用独立方法脚本，方便后续增加 `ic_weighted`、`ridge`、`rolling_ic_weighted` 等方法。
- `SKILL.md` 主要写 Agent 执行所需的脚本、参数、输入输出和排查方式，不放内部设计辩护；设计取舍记录在 checkpoint 或总计划中。

已验证：

```text
等权合成:
  train IC均值=-0.0776  ICIR=-0.3652
  valid IC均值=-0.0553  ICIR=-0.3051
  test  IC均值=-0.0402  ICIR=-0.2370

LinearRegression:
  train IC均值= 0.0413  ICIR= 0.2191
  valid IC均值= 0.0116  ICIR= 0.0682
  test  IC均值= 0.0077  ICIR= 0.0481
  训练集 R²=0.00051

Skill is valid!
```

已完成第五个阶段：

```text
for_agent/quant-portfolio-backtest/
  SKILL.md
  scripts/run_topk_portfolio_backtest.py
```

该 skill 负责基于一个或多个 test score 文件执行 Top-K 组合构建和教学型简化回测。它与上游因子合成方法解耦，只要求得分文件包含：

```text
datetime, instrument, <score column>
```

当前默认回测因子合成阶段产出的：

```text
equal_weight
linear_regression
```

以后新增 `ic_weighted`、`ridge`、`rolling_ic_weighted` 等合成方法时，只要输出同样格式的 test score 文件，即可通过 `--score-csvs method=path` 传入回测脚本。

已验证：`run_topk_portfolio_backtest.py` 与 `examples/quant_workflow_from_scratch.py` 的最终绩效对齐：

```text
绩效对比（test 期间 2019-01-01 ~ 2020-08-01）

指标                    等权EW              LR          基准（等权）
年化收益                32.13%          25.02%          46.43%
年化波动                30.53%          22.90%          22.71%
夏普比率                  1.05            1.09            2.04
最大回撤               -20.35%         -22.49%         -14.12%
累计收益                48.89%          37.58%          72.42%
```

当前 CSV-first 主链路已经覆盖：

```text
raw OHLCV CSV
  -> 基础因子长表
  -> clean/train/valid/test 样本
  -> IC/ICIR 与 selected_factors.json
  -> equal_weight / linear_regression test score
  -> Top-K holdings / daily returns / nav curve / backtest metrics
```

## 0.2 Agent 编排与参数传递判断

如果用户在 nanobot 中只提供：

```text
原始行情 CSV 的绝对路径
membership CSV 的绝对路径
train/valid/test 时间范围
输出目录的绝对路径
```

理论上，nanobot 可以通过读取各阶段 `SKILL.md`，自行推理并调用对应脚本：

```text
compute_price_volume_ohlcv_factors.py
  --raw-csv <用户给的原始行情 CSV>
  --output-dir <输出目录/price_volume_ohlcv_factors>

preprocess_cross_sectional_factors.py
  --factor-csv <第一阶段输出的 factor_csv_gz>
  --membership-csv <用户给的 membership CSV>
  --train-start <用户给的 train start>
  --train-end <用户给的 train end>
  --valid-start <用户给的 valid start>
  --valid-end <用户给的 valid end>
  --test-start <用户给的 test start>
  --test-end <用户给的 test end>
  --output-dir <输出目录/factor_preprocessing>

analyze_factor_ic.py
  --input-csv <第二阶段输出的 train_csv_gz>
  --output-dir <输出目录/factor_ic_analysis>

combine_factor_scores.py
  --train-csv <第二阶段输出的 train_csv_gz>
  --valid-csv <第二阶段输出的 valid_csv_gz>
  --test-csv <第二阶段输出的 test_csv_gz>
  --selected-factors-json <第三阶段输出的 selected_factors_json>
  --output-dir <输出目录/factor_combination>

run_topk_portfolio_backtest.py
  --raw-csv <用户给的原始行情 CSV>
  --score-csvs equal_weight=<第四阶段输出的 equal_weight_test_score_csv_gz>,linear_regression=<第四阶段输出的 linear_regression_test_score_csv_gz>
  --test-start <用户给的 test start>
  --test-end <用户给的 test end>
  --output-dir <输出目录/portfolio_backtest>
```

但这属于跨 skill 编排，完全依赖 Agent 临场推理会有不稳定点：

- 第一阶段可能产生多个输出文件，Agent 必须准确选择 `factor_csv_gz`。
- 第二阶段会产生 `clean/train/valid/test` 多个输出；IC 分析阶段通常应使用 `train_csv_gz`，因子合成阶段应使用 `train_csv_gz`、`valid_csv_gz`、`test_csv_gz`。
- 第三阶段会产生多个分析产物；因子合成阶段通常应使用 `selected_factors_json`。
- 第四阶段会为每种合成方法产生多个得分文件；回测阶段通常应使用各方法的 `test_score.csv.gz`，而不是 train/valid 得分。
- 第五阶段会产生持仓、日收益、净值曲线和绩效指标；最终报告应读取 `backtest_metrics` 和 `nav_curve`，不要只看控制台文本。
- 用户给的是绝对输出目录时，Agent 需要自行规划子目录，避免不同阶段文件混在一起。
- 如果 `--output-prefix` 使用自动生成规则，Agent 需要从 stdout 或 diagnostics 中准确读取真实文件路径。
- 换数据集时，Agent 必须覆盖所有默认参数，不能误用项目内示例默认路径。

因此当前分层建议是：

```text
独立 skill
  负责单个研究环节怎么做

workflow skill
  负责告诉 Agent 多个环节之间如何接线、如何传递路径、如何检查产物

pipeline script
  把跨阶段接线固化成代码，让 Agent 只调用一个入口脚本

MCP tool
  等函数库和 pipeline 稳定后，再把能力服务化给 nanobot 或团队协作方调用
```

短期测试可以直接给 nanobot 一条明确指令，让它自行协调五个 skill。稳定复用时，至少应新增一个 `quant-research-workflow` skill；如果希望进一步降低出错概率，应再新增一个 `run_quant_research_pipeline.py`。

## 0.3 当前数据文件职责边界

为了避免后续 Agent 混淆输入文件，当前约定如下：

| 文件 | 当前是否被量价因子脚本使用 | 规划用途 |
|---|---:|---|
| `raw_ohlcv_csi300_20140601_20200801.csv` | 是 | 基础行情输入，字段固定为 `open/high/low/close/volume` |
| `raw_ohlcv_csi300_20140601_20200801_membership.csv` | 否 | 已在因子预处理阶段使用，用于成员资格过滤、股票池还原 |
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

当前已由 `quant-factor-preprocessing` 承担：

```text
未清洗因子长表 + membership CSV
  -> 截面 MAD 去极值
  -> 截面 Z-Score
  -> dropna
  -> 成员资格过滤
  -> train/valid/test 切分
```

当前已由 `quant-factor-ic-analysis` 承担：

```text
train 样本
  -> 每日截面 Spearman IC
  -> IC/ICIR 汇总
  -> 候选因子清单 selected_factors.json
```

当前已由 `quant-factor-combination` 承担：

```text
train/valid/test 样本 + selected_factors.json
  -> 等权合成得分
  -> LinearRegression 合成得分
  -> 合成得分 IC 对比
  -> 回归系数和诊断文件
```

当前已由 `quant-portfolio-backtest` 承担：

```text
test score 文件 + raw OHLCV CSV
  -> Top-K 目标持仓
  -> T+1 等权持有收益
  -> 换手率和交易成本扣减
  -> 日收益、净值曲线、绩效指标和诊断文件
```

## 0.4 nanobot workspace 同步风险

用户用 nanobot 调用当前 skill 时，曾得到旧版 `PRICE_POS` 结果：

```text
PRICE_POS  IC均值=-0.0276  IC标准差=0.1874  ICIR=-0.1470  IC>0占比=40.3%  计算日数=693
```

排查后确认，nanobot 工作路径：

```text
D:\projects\github\qlib_study\for_agent\nanobot_workspace
```

里面有独立的 skill 拷贝：

```text
for_agent/nanobot_workspace/skills/quant-price-volume-factor-mining/
for_agent/nanobot_workspace/skills/quant-factor-preprocessing/
for_agent/nanobot_workspace/skills/quant-factor-ic-analysis/
for_agent/nanobot_workspace/skills/quant-factor-combination/
for_agent/nanobot_workspace/skills/quant-portfolio-backtest/
```

该拷贝中的 `quant-price-volume-factor-mining/scripts/compute_price_volume_ohlcv_factors.py` 尚未包含主目录最新版的 `float32` 修正，因此 nanobot 结果仍然来自旧流水线。

后续原则：

- 不要默认直接修改 `for_agent/nanobot_workspace`，除非用户明确授权。
- 如果要验证 nanobot 端结果，需要先同步主目录最新版 skill 到 nanobot workspace。
- 同步后必须至少重新跑因子计算、因子预处理、IC 分析前三个阶段来确认 `PRICE_POS` 基线；如要验证完整主链路，还应继续重跑因子合成和组合回测阶段。
- 如果 nanobot 结果与离线验证不一致，优先检查 skill 拷贝版本和是否复用了旧输出文件。

## 0.5 Qlib 对照脚本的边界结论

已对 `examples/quant_workflow_qlib.py` 做过一轮最小对齐修正：

- Spearman IC 循环跳过 `NaN` IC。
- 回测收益率窗口改为与 `from_scratch` 一致：先切测试期，再计算 `pct_change()`。
- Windows Qlib 数据路径改为 raw string，避免路径转义警告。

修正后脚本可完整运行，但仍与 CSV-first 主链路不同：

```text
from_scratch / portfolio-backtest 回测日: 360 天，2019-01-02 ~ 2020-07-30
qlib 回测日:                         372 天，2019-01-02 ~ 2020-07-31
```

这不是简单多出 12 个真实交易日，而是：

```text
Qlib 多出 16 个日期
Qlib 少掉 4 个日期
净多 12 个日期
```

主要原因：

- 原始数据中 `2019-04-29` 和 `2019-04-30` 全市场 close/volume 为空；手写 pandas rolling 会被这两个空日打断，导致后续若干日期的 `TURN_5D` 或 `MA_DEV` 全市场为 NaN，`dropna()` 后整日删除。
- Qlib 表达式引擎和 `CSZScoreNorm` 对 rolling/缺失值的处理语义不同，因此保留了部分手写版删除的日期。
- `2020-07-31` 是 label 边界差异：固定 CSV 截止到 2020-07-31，手写版没有下一交易日收益，所以该日 `LABEL` 全空并被删除；Qlib 的 `Ref($close, -1)` 可能继续从 provider 取到下一交易日，因此保留该日。

当前规划判断：

- Agent 化主链路以 CSV-first 的 `quant_workflow_from_scratch.py` / skill/scripts 结果为基准，即 360 个回测日。
- `examples/quant_workflow_qlib.py` 保留为 Qlib 对照演示脚本，不作为主链路的精确对齐基准。
- 如果后续要求 Qlib 脚本也精确对齐，应进入“方案 B”：Qlib 只负责取原始数据或表达式结果，后续预处理、label 边界、训练样本和回测日期集合全部改为显式 pandas/CSV-first 口径。

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
- CSV-first 流程读取 OHLCV 时显式恢复 `float32`，保证与 Qlib `.bin` 基线对齐。
- 拆分前后的核心数据 shape、IC 表、回测收益序列在允许误差内一致。

### Phase 0C：单阶段 skill/scripts 化（主链路五阶段已完成）

目标：在正式抽象 `quant_agent/` 研究库前，先把原流程中的关键阶段拆成 Agent 可稳定执行的脚本，并配套 skill 说明。

已完成：

- `for_agent/quant-price-volume-factor-mining/SKILL.md`
- `for_agent/quant-price-volume-factor-mining/scripts/compute_price_volume_ohlcv_factors.py`
- `for_agent/quant-factor-preprocessing/SKILL.md`
- `for_agent/quant-factor-preprocessing/scripts/preprocess_cross_sectional_factors.py`
- `for_agent/quant-factor-ic-analysis/SKILL.md`
- `for_agent/quant-factor-ic-analysis/scripts/analyze_factor_ic.py`
- `for_agent/quant-factor-combination/SKILL.md`
- `for_agent/quant-factor-combination/scripts/combine_factor_scores.py`
- `for_agent/quant-factor-combination/scripts/combine_equal_weight_scores.py`
- `for_agent/quant-factor-combination/scripts/combine_linear_regression_scores.py`
- `for_agent/quant-factor-combination/scripts/factor_combination_common.py`
- `for_agent/quant-portfolio-backtest/SKILL.md`
- `for_agent/quant-portfolio-backtest/scripts/run_topk_portfolio_backtest.py`

已对齐的关键结果：

```text
宽表 close shape = (1473, 549)
因子长表 shape = (808677, 8)
完整输出读回 shape = (808677, 10)
预处理后 clean shape = (388691, 8)
train/valid/test shape = (180841, 8) / (67735, 8) / (105900, 8)
IC 分析样本段 = 700 个交易日，431 只股票
PRICE_POS IC均值=-0.0293, ICIR=-0.1577, 计算日数=690
等权合成 train/valid/test IC均值 = -0.0776 / -0.0553 / -0.0402
LR 合成 train/valid/test IC均值 = 0.0413 / 0.0116 / 0.0077
Top-K 回测绩效 = EW 年化收益 32.13%，LR 年化收益 25.02%，基准年化收益 46.43%
Skill is valid!
```

脚本健壮性要求已纳入当前阶段标准：

- 核心输入路径支持显式传参；为了 IDE 调试，部分示例脚本也可配置项目内默认参数。
- 输出目录不存在时自动创建。
- 常见异常输出中文错误和排查建议。
- 调试时可用 `--debug` 打印完整 traceback。
- 大结果落盘，屏幕只输出摘要、诊断和产物路径。
- 预处理阶段不能绑定固定因子名，应能自适应上游因子挖掘结果。
- IC 分析阶段也不能绑定固定因子名，应能自适应预处理后的因子列。
- 因子合成阶段应作为一个 skill 管理，但每种合成方法应拆成独立方法脚本，避免单个脚本无限膨胀。
- 组合回测阶段应与因子合成方法解耦，只消费标准化 test score 文件。
- `SKILL.md` 应主要放执行说明、参数、输入输出和排查方式；内部设计理由放 checkpoint 或计划文档。
- 时间切分参数必须严格校验，避免 Agent 在错误日期范围上继续研究。

下一步：

- 可先新建一个轻量的上层 workflow skill，把已完成的五个相邻阶段串起来。
- 也可开始抽象 `quant_agent/` 研究库和 CSV-first pipeline 入口脚本，为后续 MCP 化做准备。

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
- 当前已完成因子预处理/股票池过滤 skill/scripts，可作为后续抽库的第二份代码来源。
- 当前已完成因子有效性分析（IC / ICIR）skill/scripts，可作为后续抽库的第三份代码来源。
- 当前已完成因子合成 skill/scripts，可作为后续抽库的第四份代码来源；其中 `factor_combination_common.py` 的公共逻辑可优先沉入 `quant_agent/combine.py` 或 `quant_agent/analysis.py`。
- 因子预处理逻辑应保持因子列自适应，不要绑定固定因子挖掘假设。
- IC 分析逻辑也应保持因子列自适应，并把选中因子清单输出为后续因子合成可读取的 JSON。
- 因子合成逻辑应保留“方法脚本可插拔”的结构，后续新增合成方法时优先增加独立脚本或独立函数，而不是扩张一个巨型脚本。
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

1. `baseline`：跑当前基础因子基线。
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
- 先完成关键阶段的 skill/scripts 化：基础因子计算、因子预处理/股票池过滤、因子有效性分析（IC / ICIR）、因子合成、组合构建 + 回测已完成。
- 可增加一个轻量 workflow skill 编排当前五个阶段。
- 再把已稳定的脚本逻辑抽象成 `quant_agent/` 研究库和 CSV-first pipeline 入口。
- 再拆分/改造 `quant_workflow_from_scratch.py`，让教学脚本调用新库。
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

如果你 review 后认可，我建议下一轮有两个选择：

方向 A：先补一个轻量 workflow skill。

1. 新建 `for_agent/quant-research-workflow/SKILL.md`。
2. 编排当前五个相邻阶段：先运行量价/行情类基础因子计算，再运行因子预处理/股票池过滤，再运行因子有效性分析，再运行因子合成，最后运行组合构建 + 回测。
3. 明确如何把第一阶段输出的 `factor_csv_gz` 传给第二阶段的 `--factor-csv`。
4. 明确如何把第二阶段输出的 `train_csv_gz` 传给第三阶段的 `--input-csv`。
5. 明确如何把第二阶段输出的 `train/valid/test` 三段样本和第三阶段输出的 `selected_factors_json` 传给第四阶段。
6. 明确如何把第四阶段输出的各方法 `test_score.csv.gz` 和原始行情 CSV 传给第五阶段。
7. 要求检查五个阶段的 diagnostics、shape、输出路径、候选因子 JSON、合成得分 IC、回测指标和净值曲线。

方向 B：开始抽象 `quant_agent/` 研究库和 CSV-first pipeline 入口脚本。

1. 新建 `quant_agent/` 研究库。
2. 把五个已稳定阶段中的公共读取、校验、计算和落盘逻辑抽成可测试函数。
3. 新增一个 CSV-first pipeline 入口脚本，让 Agent 或用户只需传一次原始行情、membership、时间范围和输出目录。
4. 保留当前 skill/scripts 作为外层可执行入口，逐步改为调用 `quant_agent/`。
5. 增加 smoke test 或 dry-run 配置，为后续 MCP tool 提供稳定调用目标。

如果目标是先验证 nanobot 能否串联完整 CSV-first 基线，选方向 A；如果目标是减少脚本重复、为 MCP 化做工程底座，选方向 B。

之后再进入完整 Milestone A：

1. 新建 `quant_agent/` 研究库。
2. 先实现 `load_raw_ohlcv_csv()` 和 `load_membership_csv()`，把固定 CSV 作为唯一默认数据入口。
3. 从 `quant_workflow_from_scratch.py` 抽出因子计算、预处理、IC、合成、回测、报告函数。
4. 保留原脚本行为，把主流程改为调用新库；同时新增一个 CSV-first 入口脚本。
5. 增加一个短窗口 smoke test 或 dry-run 配置。
6. 生成“Qlib 原始脚本 vs CSV-first 新流程”的对齐说明。

这样做完后，MCP 工具和 skill 都会有明确的、可测试的调用目标。
