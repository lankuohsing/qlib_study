---
name: quant-portfolio-backtest
description: 基于一个或多个合成得分文件执行 Top-K 组合构建和简化回测，输出每日持仓、日收益、换手率、净值曲线、绩效指标和诊断文件。适用于因子合成阶段已经生成 test score 文件，需要比较不同得分方法、评估交易成本影响，或进入量化策略回测报告前验证组合表现的场景。
---

# 组合构建与回测

使用本 skill 时，优先运行内置脚本，不要让 Agent 每次临场重写组合构建和回测逻辑。脚本输入是一个或多个合成得分文件，以及原始 OHLCV CSV；输出每日持仓、日收益、净值曲线和绩效指标。

## 脚本

```text
scripts/run_topk_portfolio_backtest.py
```

本 skill 与上游因子合成方法解耦。只要输入得分文件包含以下列，就可以用于回测：

```text
datetime, instrument, <score column>
```

默认会使用因子合成阶段当前示例产出的两个测试集得分文件：

```text
equal_weight
linear_regression
```

以后新增 `ic_weighted`、`ridge`、`rolling_ic_weighted` 等合成方法时，只要也输出同样格式的 test score 文件，就可以通过 `--score-csvs method=path` 传入本脚本。

默认输出目录：

```text
for_agent/results/portfolio_backtest/
```

## 参数传递

在当前项目示例数据已经生成的情况下，可以直接运行脚本：

```powershell
D:\ProgramData\miniforge3\envs\py312\python.exe for_agent/quant-portfolio-backtest/scripts/run_topk_portfolio_backtest.py
```

默认输入为：

```text
datasets/exported/raw_ohlcv_csi300_20140601_20200801.csv
for_agent/results/factor_combination/factor_combination_preprocessed_price_volume_ohlcv_factors_raw_ohlcv_csi300_20140601_20200801_equal_weight_test_score.csv.gz
for_agent/results/factor_combination/factor_combination_preprocessed_price_volume_ohlcv_factors_raw_ohlcv_csi300_20140601_20200801_linear_regression_test_score.csv.gz
```

换数据集或指定其他得分方法时，显式传入原始行情和得分文件：

```powershell
D:\ProgramData\miniforge3\envs\py312\python.exe for_agent/quant-portfolio-backtest/scripts/run_topk_portfolio_backtest.py `
  --raw-csv datasets/exported/raw_ohlcv_csi300_20140601_20200801.csv `
  --score-csvs equal_weight=for_agent/results/factor_combination/factor_combination_preprocessed_price_volume_ohlcv_factors_raw_ohlcv_csi300_20140601_20200801_equal_weight_test_score.csv.gz,linear_regression=for_agent/results/factor_combination/factor_combination_preprocessed_price_volume_ohlcv_factors_raw_ohlcv_csi300_20140601_20200801_linear_regression_test_score.csv.gz
```

只回测一个方法：

```powershell
D:\ProgramData\miniforge3\envs\py312\python.exe for_agent/quant-portfolio-backtest/scripts/run_topk_portfolio_backtest.py `
  --score-csvs my_method=for_agent/results/factor_combination/my_method_test_score.csv.gz
```

调整组合规模、交易成本和测试期：

```powershell
D:\ProgramData\miniforge3\envs\py312\python.exe for_agent/quant-portfolio-backtest/scripts/run_topk_portfolio_backtest.py `
  --topk 30 `
  --transaction-cost 0.001 `
  --test-start 2019-01-01 `
  --test-end 2020-08-01
```

当得分文件里有多个非索引列时，用 `--score-col` 指定得分列：

```powershell
D:\ProgramData\miniforge3\envs\py312\python.exe for_agent/quant-portfolio-backtest/scripts/run_topk_portfolio_backtest.py `
  --score-csvs custom=for_agent/results/factor_combination/custom_test_score.csv.gz `
  --score-col custom_score
```

## 行为

脚本会执行以下步骤：

1. 读取一个或多个测试期得分文件，并恢复为 `["datetime", "instrument"]` 索引。
2. 读取原始 OHLCV CSV，并从 `close` 计算测试期个股日收益率宽表。
3. 对每个得分方法，在每个交易日选取分数最高的 `Top-K` 股票作为下一期目标持仓。
4. 按原教学脚本逻辑做逐日回测：
   - T 日得分选股。
   - T+1 日等权持有上一日选出的股票。
   - 组合收益为持仓股票收益的算术平均。
   - 换手率为调仓差异只数除以 `2 * TopK`。
   - 净收益为组合毛收益扣除 `turnover * transaction_cost`。
5. 计算年化收益、年化波动、夏普比率、最大回撤、累计收益。
6. 生成基准：原始行情测试期所有可用股票的等权日收益。
7. 保存持仓、日收益、净值曲线、绩效指标、预览 CSV 和诊断 JSON。

输出目录不存在时，脚本会自动创建目录。

## 输出文件

每个方法会输出：

- `*_<method>_holdings.csv.gz`：每日 Top-K 持仓。
- `*_<method>_daily_returns.csv`：每日毛收益、换手率、净收益。

多方法共享输出：

- `*_nav_curve.csv`：各方法净值、相对基准超额净值、基准净值。
- `*_backtest_metrics.csv`：可读绩效指标。
- `*_backtest_metrics.json`：数值型绩效指标和可读指标。
- `*_preview.csv`：各方法日收益预览。
- `*_diagnostics.json`：中文诊断摘要。

## 使用要求

- 输入得分应来自因子合成阶段的测试集 score 文件，但不限制合成方法名称。
- 得分文件中的 `datetime` 和 `instrument` 必须能与原始行情 CSV 对齐。
- `LABEL` 不参与本阶段；本阶段只使用已生成的 score 和未来真实收益做回测。
- 当前回测是教学型简化回测，不含停牌、涨跌停、真实成交价、滑点、容量约束等更真实交易细节。
- 如果要和现有研究基线对齐，保持默认 `--topk 30`、`--transaction-cost 0.001`、`--test-start 2019-01-01`、`--test-end 2020-08-01`。

## 异常处理和排查

脚本会对常见错误输出中文提示，并以非 0 状态码退出，方便 Agent 判断执行失败。

已覆盖的检查包括：

- `--score-csvs` 或 `--raw-csv` 指向的文件不存在。
- 输入路径不是文件。
- 得分 CSV 为空。
- 得分 CSV 缺少 `datetime`、`instrument` 或可识别 score 列。
- 得分 CSV 有多个候选 score 列但未指定 `--score-col`。
- 原始行情 CSV 缺少 `datetime`、`instrument`、`open/high/low/close/volume`。
- `--topk` 不是正整数。
- `--transaction-cost` 为负数。
- 输出目录无法创建，或输出路径已存在但不是目录。
- `--output-prefix` 包含不适合作为文件名的字符。

如果只需要给用户或 Agent 一个可读错误，直接看控制台输出即可；如果需要开发调试，追加 `--debug` 查看完整 Python traceback。
