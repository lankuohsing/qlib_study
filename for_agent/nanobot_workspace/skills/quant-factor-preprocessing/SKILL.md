---
name: quant-factor-preprocessing
description: 对量化因子长表执行每日截面 MAD 去极值、Z-Score 标准化、缺失值清理、股票池成员资格过滤和 train/valid/test 时间切分。适用于上游因子挖掘阶段已经生成一个或多个因子列，并带有 datetime、instrument、LABEL，需要为 IC 分析、因子合成、模型训练或回测准备干净样本表的场景。
---

# 因子预处理与股票池过滤

使用本 skill 时，优先运行内置脚本，不要让 Agent 每次临场重写清洗逻辑。脚本输入是因子长表和股票池成员资格表，输出是预处理后的全量样本以及 `train`、`valid`、`test` 三段数据。

## 脚本

```text
scripts/preprocess_cross_sectional_factors.py
```

因子 CSV 必须包含索引列、标签列，以及至少一个因子列：

```text
datetime, instrument, <factor columns...>, LABEL
```

默认情况下，脚本会把除 `datetime`、`instrument`、`LABEL` 之外的所有列自动识别为因子列。需要手动限定时，可用 `--factor-cols` 传入逗号分隔的列名。

成员资格 CSV 必须包含以下列：

```text
instrument, start_time, end_time
```

默认输出目录：

```text
for_agent/results/factor_preprocessing/
```

## 参数传递

在当前项目示例数据已经生成的情况下，可以直接运行脚本：

```powershell
D:\ProgramData\miniforge3\envs\py312\python.exe for_agent/quant-factor-preprocessing/scripts/preprocess_cross_sectional_factors.py
```

默认输入为：

```text
for_agent/results/price_volume_ohlcv_factors/price_volume_ohlcv_factors_raw_ohlcv_csi300_20140601_20200801.csv.gz
datasets/exported/raw_ohlcv_csi300_20140601_20200801_membership.csv
```

如果要换数据集，显式传入因子文件和成员资格文件：

```powershell
D:\ProgramData\miniforge3\envs\py312\python.exe for_agent/quant-factor-preprocessing/scripts/preprocess_cross_sectional_factors.py `
  --factor-csv for_agent/results/price_volume_ohlcv_factors/price_volume_ohlcv_factors_raw_ohlcv_csi300_20140601_20200801.csv.gz `
  --membership-csv datasets/exported/raw_ohlcv_csi300_20140601_20200801_membership.csv
```

手动指定需要处理的因子列：

```powershell
D:\ProgramData\miniforge3\envs\py312\python.exe for_agent/quant-factor-preprocessing/scripts/preprocess_cross_sectional_factors.py `
  --factor-csv for_agent/results/price_volume_ohlcv_factors/price_volume_ohlcv_factors_raw_ohlcv_csi300_20140601_20200801.csv.gz `
  --membership-csv datasets/exported/raw_ohlcv_csi300_20140601_20200801_membership.csv `
  --factor-cols MOM_5D,MOM_20D,VOL_20D
```

指定输出目录、文件名前缀和切分区间：

```powershell
D:\ProgramData\miniforge3\envs\py312\python.exe for_agent/quant-factor-preprocessing/scripts/preprocess_cross_sectional_factors.py `
  --factor-csv for_agent/results/price_volume_ohlcv_factors/price_volume_ohlcv_factors_raw_ohlcv_csi300_20140601_20200801.csv.gz `
  --membership-csv datasets/exported/raw_ohlcv_csi300_20140601_20200801_membership.csv `
  --output-dir for_agent/results/factor_preprocessing `
  --output-prefix preprocessed_price_volume_ohlcv_factors_csi300_20140601_20200801 `
  --train-start 2015-01-01 --train-end 2017-12-31 `
  --valid-start 2018-01-01 --valid-end 2018-12-31 `
  --test-start 2019-01-01 --test-end 2020-08-01
```

如果不传 `--output-prefix`，脚本会根据输入因子文件名自动生成。

## 行为

脚本会执行以下步骤：

1. 读取因子长表，并恢复为 `["datetime", "instrument"]` 索引。
2. 读取成员资格表，并解析每只股票的有效区间。
3. 自动识别因子列，或使用 `--factor-cols` 指定的因子列。
4. 对每个交易日的截面因子执行 MAD 去极值。
5. 对每个交易日的截面因子执行 Z-Score 标准化。
6. 删除任一因子或 `LABEL` 为空的行。
7. 只保留当日属于股票池成员区间的样本。
8. 按日期切分 `train`、`valid`、`test`。
9. 保存全量清洗表、三段切分表、预览 CSV 和诊断 JSON。

输出目录不存在时，脚本会自动创建目录。

## 输出文件

默认会输出：

- `*_clean.csv.gz`：成员资格过滤后的完整清洗样本。
- `*_train.csv.gz`：训练区间样本。
- `*_valid.csv.gz`：验证区间样本。
- `*_test.csv.gz`：测试区间样本。
- `*_preview.csv`：少量预览行。
- `*_diagnostics.json`：中文诊断摘要。

## 使用要求

- 当前项目示例数据可直接使用默认参数；换数据集或自动化批量运行时，应显式传入 `--factor-csv` 和 `--membership-csv`。
- 输入因子表应是未经过截面标准化和成员资格过滤的长表。
- 输入因子表可以来自不同的上游因子挖掘假设；不要在 skill 层固定某一组具体因子名。
- `LABEL` 是未来一期收益，只能用于训练、评估或 IC 分析，不能作为当日可用选股信号。
- 如果要和既有研究基线对齐，保持默认切分区间：
  - `train`: `2015-01-01 ~ 2017-12-31`
  - `valid`: `2018-01-01 ~ 2018-12-31`
  - `test`: `2019-01-01 ~ 2020-08-01`
- 在进入 IC 分析或因子合成前，先查看诊断 JSON，确认处理前后 shape、丢弃行数、日期范围和三段切分是否符合预期。

## 异常处理和排查

脚本会对常见错误输出中文提示，并以非 0 状态码退出，方便 Agent 判断执行失败。

已覆盖的检查包括：

- `--factor-csv` 或 `--membership-csv` 指向的文件不存在。
- 输入路径不是文件。
- CSV 为空。
- 因子表缺少 `datetime`、`instrument`、`LABEL`，或无法识别任何因子列。
- `--factor-cols` 指定了输入 CSV 中不存在的列。
- 成员资格表缺少 `instrument`、`start_time`、`end_time`。
- 日期无法解析。
- `train`、`valid`、`test` 任一时间段开始日期晚于结束日期。
- `train`、`valid`、`test` 时间段互相重叠或顺序不符合先训练、再验证、再测试。
- `train`、`valid`、`test` 时间段不在清洗后数据日期范围内，或切分后没有可用样本。
- `--preview-rows` 不是正整数。
- 输出目录无法创建，或输出路径已存在但不是目录。
- `--output-prefix` 包含不适合作为文件名的字符。
- 清洗或成员资格过滤后没有剩余样本。

如果只需要给用户或 Agent 一个可读错误，直接看控制台输出即可；如果需要开发调试，追加 `--debug` 查看完整 Python traceback。
