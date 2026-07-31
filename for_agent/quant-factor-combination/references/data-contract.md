# 合成契约

输入三段均包含 `datetime,instrument,<factor...>,LABEL`。train 的 LABEL 必须可用；valid/test 可含 LABEL 缺失行，但因子应完整。

IC JSON 至少包含 `selected_factors`；新版还包含 `factor_directions`。若方向缺失，等权脚本只从 train 重新计算，不读取 valid/test。

得分输出：`datetime,instrument,equal_weight_score` 或 `linear_regression_score`。得分日期代表 T 日收盘后的信号日期，不代表成交日期。

EW 是无量纲截面分数，只适合排名。LR 预测与 LABEL 相同的收益率单位，可与事先固定的交易成本门槛比较。
