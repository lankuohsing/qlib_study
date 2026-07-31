# 统计口径

输入必须包含 `datetime,instrument,LABEL` 和至少一个因子列，且通常来自训练区间的 labeled 样本。

每日 IC：当天至少 `--min-samples` 个因子与 LABEL 成对非空样本的 Spearman 相关系数。

ICIR：每日 IC 均值除以每日 IC 样本标准差。方向为训练期 IC 均值非负时 `+1`，否则 `-1`。

筛选条件：`abs(IC均值) > --ic-mean-threshold`。所有选择、方向和阈值必须在查看 valid/test 策略收益前确定。
