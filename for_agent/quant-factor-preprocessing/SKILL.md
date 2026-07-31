---
name: quant-factor-preprocessing
description: 按动态股票池成员资格执行每日截面 MAD 去极值和 Z-Score，并严格拆分可打分样本与有标签样本。用于准备 train/valid/test 数据，防止用未来 LABEL 筛选候选股票，或为新因子挖掘结果建立统一清洗与时间切分流程。
---

# 因子截面预处理

运行 `scripts/preprocess_cross_sectional_factors.py`。输入输出定义见 [数据契约](references/data-contract.md)。

```powershell
D:\ProgramData\miniforge3\envs\py312\python.exe for_agent/quant-factor-preprocessing/scripts/preprocess_cross_sectional_factors.py `
  --factor-csv for_agent/results/price_volume_ohlcv_factors/price_volume_ohlcv_factors_raw_ohlcv_csi300_20140601_20200801.csv.gz `
  --membership-csv datasets/exported/raw_ohlcv_csi300_20140601_20200801_membership.csv `
  --train-start 2010-01-01 --train-end 2014-12-31 `
  --valid-start 2015-01-01 --valid-end 2017-12-31 `
  --test-start 2018-01-01 --test-end 2019-06-01
```

## 固定顺序

1. 按当日成员资格过滤股票。
2. 仅在当日真实成员内部做 MAD 去极值和 Z-Score。
3. 生成 `scoring`：只要求全部因子非空，不查看 LABEL。
4. 生成 `labeled`：在 scoring 上再要求 LABEL 非空。
5. train 取 labeled；valid/test 取 scoring。

不得把顺序改成先全市场标准化后过滤，也不得对 valid/test 执行整行 `dropna()`。检查 diagnostics 中 `scoring_rows_with_missing_label`、三段形状和日期覆盖。
