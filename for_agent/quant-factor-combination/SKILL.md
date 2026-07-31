---
name: quant-factor-combination
description: 将预处理因子合成为单一选股得分，支持按训练期 IC 方向校正后的等权合成和仅在训练集拟合的 LinearRegression，并输出各时间段得分与 IC 诊断。用于生成回测输入、比较合成方法或扩展新的因子组合算法。
---

# 因子合成

优先用统一入口：

```powershell
D:\ProgramData\miniforge3\envs\py312\python.exe for_agent/quant-factor-combination/scripts/combine_factor_scores.py `
  --methods equal_weight,linear_regression
```

换数据时显式传入 `--train-csv`、`--valid-csv`、`--test-csv` 和 `--selected-factors-json`。数据约束见 [合成契约](references/data-contract.md)。也可单独运行 `combine_equal_weight_scores.py` 或 `combine_linear_regression_scores.py`。

## 方法纪律

- 等权：先按训练期 IC 把负向因子乘以 -1，再等权平均；方向固定应用到 train/valid/test。
- LR：只用 train 中因子与 LABEL 拟合；valid 用于比较方法，test 只生成最终得分。
- valid/test 的 LABEL 只用于事后 IC 诊断，不得影响当日得分覆盖。
- 新增方法时新建独立脚本，复用 `factor_combination_common.py` 的读取、校验、IC 评估和输出函数；不要把算法堆进统一入口。
- 输出得分必须只有 `datetime,instrument,<one score column>`，以便回测脚本与方法名称解耦。

检查方向文件、LR 系数、各段得分数量和 valid IC。不要根据 test 表现回头改因子、方向、阈值或模型。
