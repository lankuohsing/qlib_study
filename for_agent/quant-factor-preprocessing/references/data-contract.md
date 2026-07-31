# 数据契约

因子输入：`datetime,instrument,<factor columns...>,LABEL`。

成员资格输入：`instrument,start_time,end_time`，区间两端均包含。

输出：

- `*_scoring.csv.gz`：动态股票池内、全部因子可用的样本；LABEL 可为空。
- `*_labeled.csv.gz`：scoring 中 LABEL 也可用的子集。
- `*_train.csv.gz`：训练区间的 labeled。
- `*_valid.csv.gz`、`*_test.csv.gz`：相应区间的 scoring。

所有输出主键均为 `datetime,instrument`。默认自动把除主键和 LABEL 外的列识别为因子，也可用 `--factor-cols` 限定。
