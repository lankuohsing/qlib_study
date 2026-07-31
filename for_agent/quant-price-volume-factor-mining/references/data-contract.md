# 数据契约

输入列：`datetime,instrument,open,high,low,close,volume`。同一 `(datetime,instrument)` 必须唯一，行情应包含测试截止日后的至少两个交易日。

默认输出因子：`MOM_5D,MOM_20D,VOL_20D,TURN_5D,MA_DEV,DAY_RANGE,PRICE_POS`。

标签：`LABEL = open.shift(-2) / open.shift(-1) - 1`。T 日收盘后生成信号，T+1 开盘成交，T+2 开盘结算。

输出主键：`datetime,instrument`。因子文件保留完整股票历史和 NaN；动态股票池过滤在预处理阶段执行。
