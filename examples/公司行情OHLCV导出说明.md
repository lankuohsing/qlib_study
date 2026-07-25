# 公司行情数据库 OHLCV 导出说明

本文记录如何从公司 Oracle 行情数据库查询 2020-01-01 至 2026-06-01 的沪深 A 股日行情，转换为与 `outputs/raw_df.csv` 相同的 CSV 结构，并说明前复权、成交量调整、上市区间过滤和数据质量检查的处理方式。

## 1. 最终产物与相关脚本

- 完整数据：`outputs/raw_df_company_20200101_20260601.csv`
- 导出脚本：`examples/export_company_ohlcv.py`
- 停牌占位修正脚本：`examples/normalize_company_ohlcv.py`
- 全文件校验脚本：`examples/verify_company_ohlcv.py`
- 使用该 CSV 的量化流程：`examples/quant_workflow_from_company_csv.py`

最终 CSV 的字段和顺序严格保持为：

```text
datetime,instrument,open,high,low,close,volume
```

最终文件共有 7,361,640 条数据记录、1,551 个交易日、5,419 只股票，实际日期范围为 2020-01-02 至 2026-06-01。2020-01-01 不是交易日，因此文件从 2020-01-02 开始。

## 2. 数据库连接

连接方式参考：

```text
D:\projects\gitlab\quantitative_trade_agent\query_embase_database\sql_example.py
```

连接参数从同目录的 `secrets.yml` 读取，密码没有写入导出脚本。连接目标为：

```text
Oracle service: EMBASE
Port: 11521
```

脚本使用 `python-oracledb` 的 thin mode 连接。

## 3. 使用的数据表

### 3.1 `NEWSADMIN.TRAD_SK_DAILY_JC`

股票日行情表，提供未复权的 OHLCV：

| 数据库字段 | 含义 | CSV 字段 |
| --- | --- | --- |
| `TDATE` | 交易日期 | `datetime` |
| `SECUCODE` | 六位证券代码 | `instrument` 的代码部分 |
| `TEXCH` | 交易所 | 转换为 `SH` 或 `SZ` 前缀 |
| `OPEN` | 未复权开盘价 | `open` |
| `HIGH` | 未复权最高价 | `high` |
| `LOW` | 未复权最低价 | `low` |
| `NEW` | 未复权收盘价 | `close` |
| `TVOL` | 原始成交量 | `volume` |
| `SECURITYVARIETYCODE` | 证券品种内码 | 用于关联其他表 |

### 3.2 `NEWSADMIN.TRAD_SK_FACTOR1`

股票复权因子表。通过以下键关联日行情：

```sql
f.SECURITYVARIETYCODE = d.SECURITYVARIETYCODE
AND f.TRADEDATE = d.TDATE
```

本次使用 `TBFACTOR`，其数据字典含义为“当日累计前复权因子”。导出前已验证目标范围内：

- `TBFACTOR IS NULL`：0 行；
- `TBFACTOR <= 0`：0 行。

因此使用内连接不会丢失符合股票池条件的行情记录。

### 3.3 `NEWSADMIN.CDSY_SECUCODE`

证券主数据表，用于确认证券类型和上市生命周期：

- `SECURITYTYPECODE = '058001001'`：A 股；
- `LISTINGDATE`：上市日期；
- `ENDDATE`：终止上市日期；
- `SECURITYVARIETYCODE`：与行情表连接。

## 4. 股票范围和上市生命周期

本次导出的范围是全部沪深 A 股，不是 CSI300 成分股。北交所被排除：

```sql
d.TEXCH IN ('上交所', '深交所')
```

没有按照“当前是否上市”过滤整只股票。过滤是在每一条股票日行情上进行的：

```sql
d.TDATE >= s.LISTINGDATE
AND (s.ENDDATE IS NULL OR d.TDATE < s.ENDDATE)
```

其含义是：

- 上市前的行情不进入 CSV；
- 上市期间的行情保留；
- 历史退市股不会被整只删除；
- 退市日及退市后的数据库占位行情不进入 CSV；
- 尚未退市的股票保留至查询结束日。

这样能够保留退市股在真实上市期间的历史，避免仅保留当前存续股票所造成的幸存者偏差；同时也排除了数据库为部分退市股持续生成的冻结价格“僵尸行情”。

## 5. 为什么需要前复权

`TRAD_SK_DAILY_JC` 中的开高低收是未复权价格。股票发生分红、送股、拆股或配股后，未复权价格会在除权日产生并非真实投资损益的跳变。如果直接用未复权收盘价计算：

```python
close.pct_change()
```

这些机械跳变会污染动量、波动率、均线偏离、标签和回测收益。

原始 Qlib `raw_df.csv` 中的 `$open/$high/$low/$close` 也不是交易所原始价格，而是经过因子归一化的价格；其成交量与价格因子做了相反方向的调整。因此，为了让新 CSV 的经济口径与旧 Qlib 输入一致，本次使用前复权口径。

## 6. 前复权的具体计算

设某只股票在交易日 `t` 的累计前复权因子为：

```text
F_t = TBFACTOR_t
```

四个价格字段分别计算为：

```text
open_adj  = open_raw  × F_t
high_adj  = high_raw  × F_t
low_adj   = low_raw   × F_t
close_adj = close_raw × F_t
```

成交量采用相反方向调整：

```text
volume_adj = volume_raw ÷ F_t
```

成交量反向调整有两个目的：

1. 与旧 Qlib 数据中价格乘因子、成交量除因子的表达一致；
2. 在拆股、送股等股本变化场景下，使调整前后的“价格 × 成交量”量级大致保持一致。

例如 `SH600000` 在 2026-06-01 的数据库记录为：

```text
原始 open      = 9.32
原始 volume    = 75,072,117
TBFACTOR       = 0.95488722
```

导出结果为：

```text
open_adj   = 9.32 × 0.95488722
           = 8.8995488904

volume_adj = 75,072,117 ÷ 0.95488722
           = 78,618,831.02802444
```

### 6.1 为什么没有使用 `TAFACTOR`

数据字典中：

- `TAFACTOR` 是当日累计后复权因子；
- `TBFACTOR` 是当日累计前复权因子。

后复权通常把早期价格作为基准并把后续价格抬高，适合观察长期持有价值；本流程需要计算日收益、滚动动量和回测标签，更适合让当前附近价格保持接近现价、历史价格随公司行为向下调整的前复权口径。因此选择 `TBFACTOR`。

需要注意：公司数据库和 Qlib 的数据源、复权因子快照不同，所以同一股票同一天的复权价格不会逐位完全相同；这里匹配的是复权方法和经济含义，而不是强行匹配旧 Qlib 的具体数值。

## 7. 实际执行的核心 SQL

导出脚本使用的核心查询如下。实际运行时 `chunk_start` 和 `chunk_end` 按年度传入：

```sql
SELECT TO_CHAR(d.TDATE, 'YYYY-MM-DD') AS datetime,
       CASE d.TEXCH
           WHEN '上交所' THEN 'SH' || d.SECUCODE
           WHEN '深交所' THEN 'SZ' || d.SECUCODE
       END AS instrument,
       CASE WHEN d.OPEN = 0 AND d.HIGH = 0 AND d.LOW = 0 AND d.NEW > 0
            THEN d.NEW ELSE d.OPEN END * f.TBFACTOR AS open,
       CASE WHEN d.OPEN = 0 AND d.HIGH = 0 AND d.LOW = 0 AND d.NEW > 0
            THEN d.NEW ELSE d.HIGH END * f.TBFACTOR AS high,
       CASE WHEN d.OPEN = 0 AND d.HIGH = 0 AND d.LOW = 0 AND d.NEW > 0
            THEN d.NEW ELSE d.LOW END * f.TBFACTOR AS low,
       d.NEW * f.TBFACTOR AS close,
       d.TVOL / f.TBFACTOR AS volume
FROM NEWSADMIN.TRAD_SK_DAILY_JC d
JOIN NEWSADMIN.TRAD_SK_FACTOR1 f
  ON f.SECURITYVARIETYCODE = d.SECURITYVARIETYCODE
 AND f.TRADEDATE = d.TDATE
WHERE d.TDATE >= :chunk_start
  AND d.TDATE < :chunk_end
  AND d.TEXCH IN ('上交所', '深交所')
  AND f.TBFACTOR > 0
  AND EXISTS (
      SELECT 1
      FROM NEWSADMIN.CDSY_SECUCODE s
      WHERE s.SECURITYVARIETYCODE = d.SECURITYVARIETYCODE
        AND s.SECURITYTYPECODE = '058001001'
        AND d.TDATE >= s.LISTINGDATE
        AND (s.ENDDATE IS NULL OR d.TDATE < s.ENDDATE)
  )
ORDER BY d.TDATE, instrument
```

## 8. 日期边界

用户要求的区间是 2020-01-01 至 2026-06-01，结束日需要包含在内。程序内部把闭区间转换成半开区间：

```text
[2020-01-01, 2026-06-02)
```

每一个年度分块也使用：

```sql
d.TDATE >= :chunk_start
AND d.TDATE < :chunk_end
```

半开区间可以避免相邻年度重复导出边界日。

## 9. 为什么按年度分块导出

过滤后的数据仍有 736 万行。脚本没有一次性把全部结果加载到 pandas，而是：

1. 按自然年查询；
2. Oracle cursor 每次预取并读取 10,000 行；
3. 使用 Python `csv.writer` 流式写入；
4. 先写入 `.tmp` 文件；
5. 所有年度成功后再原子替换为最终 CSV。

各年度行数为：

| 年度 | 行数 |
| --- | ---: |
| 2020 | 952,183 |
| 2021 | 1,062,370 |
| 2022 | 1,149,219 |
| 2023 | 1,211,909 |
| 2024 | 1,236,280 |
| 2025 | 1,251,221 |
| 2026-01-01 至 2026-06-01 | 498,458 |
| 合计 | 7,361,640 |

## 10. 停牌和零 OHLC 占位处理

初次导出后发现 724 行具有相同特征：

```text
open = high = low = 0
close > 0
volume = 0
```

这些是数据库中的停牌/无成交占位行，不是真实的零价格。如果原样进入因子计算：

```text
PRICE_POS = (close - low) / (high - low)
```

会产生极端异常值。因此将其规范化为零成交的平盘 bar：

```text
open = high = low = close
volume 保持 0
```

这一规则已经写入导出 SQL。对于首次导出的现有文件，也使用 `normalize_company_ohlcv.py` 做了相同的流式修正。

另外有 21,895 行 `volume` 为空。这些空值没有填成 0，而是保留为空；后续量化脚本在构造完整因子样本时会通过 `dropna()` 排除无法计算量价因子的记录，避免把“未知成交量”误解释成“明确零成交”。

## 11. 证券代码转换

数据库使用六位代码，目标 CSV 沿用 Qlib 风格：

```text
上交所 600000 → SH600000
深交所 000001 → SZ000001
```

最终校验要求代码满足：

```regex
^(SH|SZ)\d{6}$
```

## 12. 最终质量检查

校验脚本以流式方式扫描完整 CSV，没有把 599 MB 文件一次性读入内存。最终结果：

| 检查项 | 结果 |
| --- | ---: |
| 数据行数 | 7,361,640 |
| 日期数 | 1,551 |
| 股票数 | 5,419 |
| 最早日期 | 2020-01-02 |
| 最晚日期 | 2026-06-01 |
| 重复 `datetime + instrument` | 0 |
| 全局排序错误 | 0 |
| 非法证券代码 | 0 |
| 非法 OHLC | 0 |
| 空成交量 | 21,895 |

OHLC 合法性至少满足：

```text
open > 0
high > 0
low > 0
close > 0
high >= max(open, close, low)
low <= min(open, close, high)
```

最终文件 SHA256：

```text
B8D0E60FE5FC6327EA98B6C638EB0BBB5EF48BC67E1CBB3437B4C294AC86661D
```

## 13. 复现命令

在仓库根目录运行：

```powershell
& 'D:\ProgramData\miniforge3\envs\py312\python.exe' `
  'examples\export_company_ohlcv.py' `
  --start-date 2020-01-01 `
  --end-date 2026-06-01 `
  --output 'outputs\raw_df_company_20200101_20260601.csv'
```

验证文件：

```powershell
& 'D:\ProgramData\miniforge3\envs\py312\python.exe' `
  'examples\verify_company_ohlcv.py' `
  'outputs\raw_df_company_20200101_20260601.csv'
```

## 14. 使用限制与注意事项

1. **不是 CSI300 股票池**：CSV 是沪深全 A 股。若策略需要 CSI300，必须另行引入按日期变化的指数成分历史，不能用当前成分股静态过滤历史。
2. **不是交易日历笛卡尔骨架**：导出以行情表中实际存在的记录为基础，没有主动补齐每只股票的全部市场交易日；完全缺失的停牌日不会凭空生成。
3. **前复权历史可能变化**：累计前复权因子会随着后续公司行为更新。未来重新导出时，历史复权价格可能与本次文件略有差异，这是动态复权因子的正常特性。
4. **空成交量未插值**：空值保持空值，不做前向填充或置零。
5. **退市端点采用左闭右开**：当前规则包含上市日、排除 `ENDDATE` 当天及以后，与本次数据库探索中识别出的僵尸行情处理口径一致。

