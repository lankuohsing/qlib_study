---
name: quant-factor-ic-analysis
description: 对预处理后的量化因子样本计算每日截面 IC、IC 均值、IC 标准差、ICIR、IC 正值占比和计算日数，并按阈值筛选候选有效因子。适用于因子表已经完成截面标准化、缺失值清理和股票池过滤，且包含 datetime、instrument、LABEL 以及一个或多个因子列，需要进入因子合成、模型训练或研究报告前评估因子有效性的场景。
---

# 因子有效性分析

使用本 skill 时，优先运行内置脚本，不要让 Agent 每次临场重写 IC/ICIR 统计逻辑。脚本输入是预处理后的因子样本，输出因子有效性分析表、每日 IC 明细、因子相关矩阵、候选有效因子清单和诊断 JSON。

## 脚本

```text
scripts/analyze_factor_ic.py
```

输入 CSV 必须包含索引列、标签列，以及至少一个因子列：

```text
datetime, instrument, factor columns..., LABEL
```

默认情况下，脚本会把除 `datetime`、`instrument`、`LABEL` 之外的所有列自动识别为因子列。需要手动限定时，可用 `--factor-cols` 传入逗号分隔的列名。

默认输出目录：

```text
for_agent/results/factor_ic_analysis/
```

## 参数传递

在当前项目示例数据已经生成的情况下，可以直接运行脚本：

```powershell
D:\ProgramData\miniforge3\envs\py312\python.exe for_agent/quant-factor-ic-analysis/scripts/analyze_factor_ic.py
```

默认输入为：

```text
for_agent/results/factor_preprocessing/preprocessed_price_volume_ohlcv_factors_raw_ohlcv_csi300_20140601_20200801_train.csv.gz
```

如果要换数据集，显式传入因子样本：

```powershell
D:\ProgramData\miniforge3\envs\py312\python.exe for_agent/quant-factor-ic-analysis/scripts/analyze_factor_ic.py `
  --input-csv for_agent/results/factor_preprocessing/preprocessed_price_volume_ohlcv_factors_raw_ohlcv_csi300_20140601_20200801_train.csv.gz
```

手动指定需要分析的因子列：

```powershell
D:\ProgramData\miniforge3\envs\py312\python.exe for_agent/quant-factor-ic-analysis/scripts/analyze_factor_ic.py `
  --input-csv for_agent/results/factor_preprocessing/preprocessed_price_volume_ohlcv_factors_raw_ohlcv_csi300_20140601_20200801_train.csv.gz `
  --factor-cols MOM_5D,MOM_20D,VOL_20D
```

指定输出目录、文件名前缀和筛选阈值：

```powershell
D:\ProgramData\miniforge3\envs\py312\python.exe for_agent/quant-factor-ic-analysis/scripts/analyze_factor_ic.py `
  --input-csv for_agent/results/factor_preprocessing/preprocessed_price_volume_ohlcv_factors_raw_ohlcv_csi300_20140601_20200801_train.csv.gz `
  --output-dir for_agent/results/factor_ic_analysis `
  --output-prefix factor_ic_train_csi300_20150101_20171231 `
  --ic-mean-threshold 0.02
```

如果不传 `--output-prefix`，脚本会根据输入文件名自动生成。

## 行为

脚本会执行以下步骤：

1. 读取预处理后的因子样本，并恢复为 `["datetime", "instrument"]` 索引。
2. 自动识别因子列，或使用 `--factor-cols` 指定的因子列。
3. 对每个交易日、每个因子计算截面 Spearman IC：
   `IC = Spearman(因子值, LABEL)`。
4. 汇总每个因子的 `IC均值`、`IC标准差`、`ICIR`、`IC>0占比`、`计算日数`。
5. 按 `abs(IC均值) > --ic-mean-threshold` 筛选候选有效因子。
6. 如果没有因子超过阈值，默认保留全部因子供后续阶段继续分析。
7. 保存 IC 汇总表、每日 IC 明细、因子相关矩阵、候选因子 JSON、预览 CSV 和诊断 JSON。

输出目录不存在时，脚本会自动创建目录。

## 输出文件

默认会输出：

- `*_ic_analysis.csv`：每个因子的 IC/ICIR 汇总表。
- `*_daily_ic.csv.gz`：每日 IC 明细，行是交易日，列是因子。
- `*_factor_corr.csv`：因子截面样本相关矩阵。
- `*_selected_factors.json`：按阈值筛出的候选因子；若无显著因子则记录 fallback。
- `*_preview.csv`：IC 汇总表预览。
- `*_diagnostics.json`：中文诊断摘要。

## 使用要求

- 输入样本应来自因子预处理阶段，已经完成截面标准化、缺失值清理和股票池成员资格过滤。
- 输入因子表可以来自不同的上游因子挖掘假设；不要在 skill 层固定某一组具体因子名。
- `LABEL` 是未来一期收益，只能用于训练、评估或 IC 分析，不能作为当日可用选股信号。
- IC/ICIR 只衡量单因子截面预测相关性，不等同于可交易策略收益。
- 在进入因子合成前，先查看 `*_ic_analysis.csv` 和 diagnostics，确认计算日数、样本覆盖和候选因子是否合理。

## 异常处理和排查

脚本会对常见错误输出中文提示，并以非 0 状态码退出，方便 Agent 判断执行失败。

已覆盖的检查包括：

- `--input-csv` 指向的文件不存在。
- 输入路径不是文件。
- CSV 为空。
- 因子表缺少 `datetime`、`instrument`、`LABEL`，或无法识别任何因子列。
- `--factor-cols` 指定了输入 CSV 中不存在的列。
- `--min-samples` 不是正整数。
- `--ic-mean-threshold` 为负数。
- 没有任何因子能计算出有效 IC。
- 输出目录无法创建，或输出路径已存在但不是目录。
- `--output-prefix` 包含不适合作为文件名的字符。

如果只需要给用户或 Agent 一个可读错误，直接看控制台输出即可；如果需要开发调试，追加 `--debug` 查看完整 Python traceback。
