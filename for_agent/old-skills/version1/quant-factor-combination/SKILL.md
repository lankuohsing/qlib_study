---
name: quant-factor-combination
description: 对预处理后的量化因子样本执行因子合成，支持等权平均、LinearRegression 等方法脚本，并输出 train/valid/test 三段合成得分、合成得分 IC 对比、模型参数和诊断文件。适用于 IC 分析已经筛选出候选因子，需要进入组合构建、回测或比较不同合成方式之前，把多个因子合成为单一选股得分的场景。
---

# 因子合成

使用本 skill 时，优先运行内置脚本，不要让 Agent 每次临场重写因子合成逻辑。输入是因子预处理阶段生成的 `train`、`valid`、`test` 三段样本，以及 IC 分析阶段输出的候选因子 JSON；输出是一个或多个合成方法在三段样本上的综合得分和诊断结果。

## 脚本

推荐默认入口：

```text
scripts/combine_factor_scores.py
```

该入口只负责按 `--methods` 依次调用具体方法脚本，不承载具体算法实现。

当前方法脚本：

```text
scripts/combine_equal_weight_scores.py
scripts/combine_linear_regression_scores.py
scripts/factor_combination_common.py
```

新增合成方法时，优先新增独立方法脚本，并复用 `factor_combination_common.py` 中的读取、校验、IC 评估和落盘函数。

默认输出目录：

```text
for_agent/results/factor_combination/
```

## 参数传递

在当前项目示例数据已经生成的情况下，可以直接运行默认入口：

```powershell
D:\ProgramData\miniforge3\envs\py312\python.exe for_agent/quant-factor-combination/scripts/combine_factor_scores.py
```

默认输入为：

```text
for_agent/results/factor_preprocessing/preprocessed_price_volume_ohlcv_factors_raw_ohlcv_csi300_20140601_20200801_train.csv.gz
for_agent/results/factor_preprocessing/preprocessed_price_volume_ohlcv_factors_raw_ohlcv_csi300_20140601_20200801_valid.csv.gz
for_agent/results/factor_preprocessing/preprocessed_price_volume_ohlcv_factors_raw_ohlcv_csi300_20140601_20200801_test.csv.gz
for_agent/results/factor_ic_analysis/factor_ic_preprocessed_price_volume_ohlcv_factors_raw_ohlcv_csi300_20140601_20200801_train_selected_factors.json
```

换数据集或自动化运行时，显式传入三段样本和候选因子 JSON：

```powershell
D:\ProgramData\miniforge3\envs\py312\python.exe for_agent/quant-factor-combination/scripts/combine_factor_scores.py `
  --train-csv for_agent/results/factor_preprocessing/preprocessed_price_volume_ohlcv_factors_raw_ohlcv_csi300_20140601_20200801_train.csv.gz `
  --valid-csv for_agent/results/factor_preprocessing/preprocessed_price_volume_ohlcv_factors_raw_ohlcv_csi300_20140601_20200801_valid.csv.gz `
  --test-csv for_agent/results/factor_preprocessing/preprocessed_price_volume_ohlcv_factors_raw_ohlcv_csi300_20140601_20200801_test.csv.gz `
  --selected-factors-json for_agent/results/factor_ic_analysis/factor_ic_preprocessed_price_volume_ohlcv_factors_raw_ohlcv_csi300_20140601_20200801_train_selected_factors.json
```

只运行等权合成：

```powershell
D:\ProgramData\miniforge3\envs\py312\python.exe for_agent/quant-factor-combination/scripts/combine_equal_weight_scores.py
```

只运行线性回归合成：

```powershell
D:\ProgramData\miniforge3\envs\py312\python.exe for_agent/quant-factor-combination/scripts/combine_linear_regression_scores.py
```

通过默认入口选择方法：

```powershell
D:\ProgramData\miniforge3\envs\py312\python.exe for_agent/quant-factor-combination/scripts/combine_factor_scores.py `
  --methods equal_weight

D:\ProgramData\miniforge3\envs\py312\python.exe for_agent/quant-factor-combination/scripts/combine_factor_scores.py `
  --methods linear_regression

D:\ProgramData\miniforge3\envs\py312\python.exe for_agent/quant-factor-combination/scripts/combine_factor_scores.py `
  --methods equal_weight,linear_regression
```

手动指定参与合成的因子列：

```powershell
D:\ProgramData\miniforge3\envs\py312\python.exe for_agent/quant-factor-combination/scripts/combine_factor_scores.py `
  --factor-cols MOM_5D,MOM_20D,VOL_20D
```

指定输出目录和文件名前缀：

```powershell
D:\ProgramData\miniforge3\envs\py312\python.exe for_agent/quant-factor-combination/scripts/combine_factor_scores.py `
  --output-dir for_agent/results/factor_combination `
  --output-prefix factor_combination_csi300_20150101_20200801
```

## 行为

默认入口会执行以下步骤：

1. 解析 `--methods`。
2. 按顺序调用对应方法脚本。
3. 将通用路径参数、候选因子参数和输出参数传给方法脚本。
4. 如果任一方法失败，入口脚本以非 0 状态码退出。

等权方法脚本会：

1. 读取 `train`、`valid`、`test` 三段预处理样本。
2. 从 `selected_factors.json` 读取候选因子；如果传入 `--factor-cols`，则以手动指定为准。
3. 对每行候选因子取等权平均，得到综合得分。
4. 对每个样本段计算合成得分的每日截面 IC。
5. 保存得分文件、IC 摘要、每日 IC、预览 CSV 和诊断 JSON。

线性回归方法脚本会：

1. 读取 `train`、`valid`、`test` 三段预处理样本。
2. 从 `selected_factors.json` 或 `--factor-cols` 确定候选因子。
3. 只在 `train` 样本上拟合 `LinearRegression`。
4. 分别对 `train`、`valid`、`test` 生成综合得分。
5. 对每个样本段计算合成得分的每日截面 IC。
6. 保存得分文件、IC 摘要、每日 IC、回归系数、预览 CSV 和诊断 JSON。

输出目录不存在时，脚本会自动创建目录。

## 输出文件

等权方法默认输出：

- `*_equal_weight_train_score.csv.gz`
- `*_equal_weight_valid_score.csv.gz`
- `*_equal_weight_test_score.csv.gz`
- `*_equal_weight_ic_summary.csv`
- `*_equal_weight_daily_score_ic.csv.gz`
- `*_equal_weight_preview.csv`
- `*_equal_weight_diagnostics.json`

线性回归方法默认输出：

- `*_linear_regression_train_score.csv.gz`
- `*_linear_regression_valid_score.csv.gz`
- `*_linear_regression_test_score.csv.gz`
- `*_linear_regression_ic_summary.csv`
- `*_linear_regression_daily_score_ic.csv.gz`
- `*_linear_regression_coefficients.csv`
- `*_linear_regression_coefficients.json`
- `*_linear_regression_preview.csv`
- `*_linear_regression_diagnostics.json`

## 使用要求

- 输入样本应来自因子预处理阶段，已经完成截面标准化、缺失值清理和股票池成员资格过滤。
- 候选因子清单应优先来自 IC 分析阶段的 `selected_factors.json`。
- `LABEL` 只用于训练线性回归和评估合成得分 IC，不能作为当日可用选股信号。
- LinearRegression 只能在 `train` 样本上拟合；`valid` 用于比较合成方法，`test` 留给后续组合构建和回测。
- 当前方法脚本不按训练期 IC 符号强制翻转因子方向，保持与现有研究基线一致。
- 在进入组合构建前，先查看各方法的 `*_ic_summary.csv` 和 diagnostics，确认合成得分在 `valid` 段表现是否合理。

## 异常处理和排查

脚本会对常见错误输出中文提示，并以非 0 状态码退出，方便 Agent 判断执行失败。

已覆盖的检查包括：

- `--train-csv`、`--valid-csv`、`--test-csv` 或 `--selected-factors-json` 指向的文件不存在。
- 输入路径不是文件。
- CSV 为空。
- 样本表缺少 `datetime`、`instrument`、`LABEL`，或无法识别任何因子列。
- `selected_factors.json` 缺少有效的 `selected_factors` 列表。
- 候选因子列在三段样本中不完全存在。
- `--factor-cols` 为空或指定了不存在的列。
- `--methods` 包含不支持的方法。
- `--min-samples` 不是正整数。
- 输出目录无法创建，或输出路径已存在但不是目录。
- `--output-prefix` 包含不适合作为文件名的字符。

如果只需要给用户或 Agent 一个可读错误，直接看控制台输出即可；如果需要开发调试，追加 `--debug` 查看完整 Python traceback。
