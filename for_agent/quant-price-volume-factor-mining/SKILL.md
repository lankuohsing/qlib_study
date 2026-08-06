---
name: quant-price-volume-factor-mining
description: 从标准 OHLCV 长表计算量价因子与无前视偏差的 T+1 开盘至 T+2 开盘收益标签。用于生成基础因子文件、验证新量价因子公式、为截面预处理和 IC 分析准备输入，或基于现有脚本扩展新的价格成交量因子挖掘方法。
---

# 量价因子挖掘

优先运行 `scripts/compute_price_volume_ohlcv_factors.py`，把它作为新因子脚本的正确性基线。

## 执行

```powershell
D:\ProgramData\miniforge3\envs\py312\python.exe for_agent/quant-price-volume-factor-mining/scripts/compute_price_volume_ohlcv_factors.py `
  --raw-csv datasets/exported/raw_ohlcv_csi300_20140601_20200801.csv
```

按需传入 `--output-dir`、`--output-prefix` 和 `--preview-rows`。读取 [数据契约](references/data-contract.md) 后再接入其他数据或编写新脚本；A股日频标签与后续调仓之间的时间关系见 [A股日频策略的信号—标签—成交对齐与滚动再平衡](references/label-and-rebalance-timeline.md)。

## 约束

- 在完整历史行情上计算 rolling/shift 因子，让股票入选前的数据只用于窗口预热；不要在本阶段按成员资格裁剪历史。
- 只使用 T 日及以前可知的数据生成 T 日信号因子。
- 固定 `LABEL = open[T+2] / open[T+1] - 1`，与后续成交和持有区间一致。
- 保留因子和标签 NaN；不要在本阶段依据未来标签删行。
- 扩展新因子时复制或修改独立脚本，保持 `datetime,instrument,<factor...>,LABEL` 输出契约，并在 diagnostics 中记录公式和时间可用性。

## 验证

检查 diagnostics 中的日期范围、股票数、宽表形状、各列 NaN 比例和 `label_interval`。若修改公式，至少用小样本手算一个股票的若干日期，确认 shift 方向和窗口边界。
