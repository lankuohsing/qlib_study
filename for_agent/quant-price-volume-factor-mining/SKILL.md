---
name: quant-price-volume-factor-mining
description: 从包含 open、high、low、close、volume 的 CSV 行情数据中计算量价/行情类基础因子和次日收益标签。适用于需要生成 MOM_5D、MOM_20D、VOL_20D、TURN_5D、MA_DEV、DAY_RANGE、PRICE_POS、LABEL 等基础特征文件，并为后续因子预处理、IC 分析、因子合成或回测准备确定性输入数据的场景。
---

# 量价/行情类基础因子挖掘

使用本 skill 时，优先运行内置脚本，不要让 Agent 每次临场重写公式。脚本输入是标准长表 CSV，输出是包含基础因子和标签的长表因子文件。

## 脚本

```text
scripts/compute_price_volume_ohlcv_factors.py
```

输入 CSV 必须包含以下列：

```text
datetime, instrument, open, high, low, close, volume
```

默认输出目录：

```text
for_agent/results/price_volume_ohlcv_factors/
```

## 参数传递

必须显式传入原始行情 CSV 路径：

```powershell
D:\ProgramData\miniforge3\envs\py312\python.exe for_agent/quant-price-volume-factor-mining/scripts/compute_price_volume_ohlcv_factors.py `
  --raw-csv datasets/exported/raw_ohlcv_csi300_20140601_20200801.csv
```

指定输出目录和文件名前缀：

```powershell
D:\ProgramData\miniforge3\envs\py312\python.exe for_agent/quant-price-volume-factor-mining/scripts/compute_price_volume_ohlcv_factors.py `
  --raw-csv datasets/exported/raw_ohlcv_csi300_20140601_20200801.csv `
  --output-dir for_agent/results/price_volume_ohlcv_factors `
  --output-prefix price_volume_ohlcv_factors_csi300_20140601_20200801
```

如果不传 `--output-prefix`，脚本会根据输入 CSV 文件名自动生成，例如：

```text
raw_ohlcv_csi300_20140601_20200801.csv
  -> price_volume_ohlcv_factors_raw_ohlcv_csi300_20140601_20200801
```

## 行为

脚本会执行以下步骤：

1. 读取传入的原始 OHLCV CSV。
2. 将索引设置为 `["datetime", "instrument"]`。
3. 将 `close`、`high`、`low`、`volume` 转成宽表，行是交易日，列是股票。
4. 计算 7 个量价/行情类基础因子和 1 个标签：
   - `MOM_5D`
   - `MOM_20D`
   - `VOL_20D`
   - `TURN_5D`
   - `MA_DEV`
   - `DAY_RANGE`
   - `PRICE_POS`
   - `LABEL`
5. 保存完整长表因子文件、预览 CSV 和诊断 JSON。

输出目录不存在时，脚本会自动创建目录。

## 因子含义

- `MOM_5D`：5 日动量，`close[t] / close[t-5] - 1`。
- `MOM_20D`：20 日动量，`close[t] / close[t-20] - 1`。
- `VOL_20D`：20 日收益波动率，`std(daily_return, 20)`。
- `TURN_5D`：5 日量比，`volume[t] / mean(volume, 5)`。
- `MA_DEV`：价格偏离 20 日均线，`close[t] / mean(close, 20) - 1`。
- `DAY_RANGE`：当日振幅，`(high - low) / close[t-1]`。
- `PRICE_POS`：收盘价在当日高低区间中的位置，`(close - low) / (high - low)`。
- `LABEL`：次日收益率，`close[t+1] / close[t] - 1`，只用于后续训练和评估，不能作为当日选股信号。

## 使用要求

- 始终显式传入 `--raw-csv`，不要依赖某个实验数据集的硬编码路径。
- 输入数据必须是未经过因子预处理的原始行情长表。
- 输出因子表会保留 NaN；去极值、标准化、成员资格过滤应在后续预处理阶段完成。
- `LABEL` 是未来一期收益，只能用于训练、评估或 IC 分析，不能作为当日可用选股信号。
- 在进入因子预处理或 IC 分析前，先查看诊断 JSON，确认 shape、日期范围、股票数量和 NaN 比例是否符合预期。

## 异常处理和排查

脚本会对常见错误输出中文提示，并以非 0 状态码退出，方便 Agent 判断执行失败。

已覆盖的检查包括：

- `--raw-csv` 指向的文件不存在。
- `--raw-csv` 指向的路径不是文件。
- 输入 CSV 为空。
- 输入 CSV 缺少 `datetime`、`instrument`、`open`、`high`、`low`、`close`、`volume` 中任一列。
- `--preview-rows` 不是正整数。
- 输出目录无法创建，或输出路径已存在但不是目录。
- `--output-prefix` 包含不适合作为文件名的字符。

如果只需要给用户或 Agent 一个可读错误，直接看控制台输出即可；如果需要开发调试，追加 `--debug` 查看完整 Python traceback。
