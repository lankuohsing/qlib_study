---
name: quant-factor-ic-analysis
description: 仅用训练期有标签样本计算每日截面 Spearman IC、ICIR、覆盖率、因子相关性和训练期方向，并按绝对 IC 均值筛选候选因子。用于因子有效性检验、等权方向校正、候选因子筛选或比较新挖掘因子的训练期证据。
---

# 因子 IC 分析

运行 `scripts/analyze_factor_ic.py`，输入必须是预处理阶段的 `*_train.csv.gz`，不要用 valid/test 决定因子选择或方向。

```powershell
D:\ProgramData\miniforge3\envs\py312\python.exe for_agent/quant-factor-ic-analysis/scripts/analyze_factor_ic.py `
  --input-csv for_agent/results/factor_preprocessing/preprocessed_price_volume_ohlcv_factors_raw_ohlcv_csi300_20140601_20200801_train.csv.gz `
  --ic-mean-threshold 0.02
```

脚本输出 IC 汇总、每日 IC、因子相关矩阵、诊断和 `selected_factors.json`。后者同时包含 `selected_factors`、`factor_directions` 和训练期 IC 均值，供等权合成固定应用到其他时间段。

读取 [统计口径](references/data-contract.md)。若没有因子超过阈值，脚本会回退保留全部因子并明确记录；这只是让流程继续，不代表全部因子有效。
