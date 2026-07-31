# 回测契约

得分输入：`datetime,instrument,<exactly one score column>`；datetime 是 T 日信号日。

行情输入：`datetime,instrument,open,high,low,close,volume`，必须覆盖 test end 后至少两个交易日。

成员资格输入：`instrument,start_time,end_time`。

自定义方法配置 JSON 示例：

```json
{
  "my_return_model": {
    "rank_buffer": 5,
    "min_buy_score": 0.003,
    "min_hold_score": 0.0
  },
  "my_rank_model": {
    "rank_buffer": 5,
    "min_buy_score": null,
    "min_hold_score": null
  }
}
```

只有与 LABEL 同为收益率单位的得分才能设置成本门槛。rank/z-score/概率等得分应保持门槛为 null，或使用同单位、事先确定的规则。
