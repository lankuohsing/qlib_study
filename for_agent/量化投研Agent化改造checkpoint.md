# 量化投研 Agent 化改造 Checkpoint

> 用途：下次重新开启对话时，直接让 Codex 阅读本文件，就能以较少信息损失继续推进。
> 更新时间：2026-07-15

## 1. 原始需求

用户有一个教学型量化投研脚本：

```text
examples/quant_workflow_from_scratch.py
```

以及配套教程：

```text
examples/quant_workflow_from_scratch_tutorial.md
```

目标是把“手搓的全流程量化投研脚本”逐步改造成可被基于 nanobot 的 Agent 自动调用和创新探索的系统，包括：

- 把脚本抽象成函数。
- 把函数封装成 Agent 可调用工具。
- 把通用流程封装成 skill。
- 把人工投研经验沉淀成知识。
- 重点支持因子挖掘、因子合成、组合构建、回测评估等环节。

用户本地 nanobot 仓库在：

```text
D:\projects\github\official\nanobot
```

## 2. 已制定并持续更新的总计划

计划文档：

```text
for_agent/量化投研全流程Agent化改造计划.md
```

当前计划核心判断：

- 后续主流程应该 CSV-first，不再默认从 Qlib 动态取数。
- Python 研究库负责计算。
- MCP server 负责把研究函数暴露成工具。
- nanobot 作为 MCP client，负责发现工具和调用工具。
- skill / references 负责告诉 Agent 如何做量化研究、如何解释结果、如何防过拟合。

## 3. 已完成：固定原始行情数据集

用户提出：真实情况下数据不会直接来自 Qlib，因此应该先把原脚本中：

```python
raw_df = raw_df.swaplevel().sort_index()
```

执行后的 `raw_df` 固化为 CSV，作为统一原始数据集。

已经新增脚本：

```text
examples/export_raw_ohlcv_to_csv.py
```

执行命令：

```powershell
D:\ProgramData\miniforge3\envs\py312\python.exe examples/export_raw_ohlcv_to_csv.py
```

已生成本地数据：

```text
datasets/exported/raw_ohlcv_csi300_20140601_20200801.csv
datasets/exported/raw_ohlcv_csi300_20140601_20200801_membership.csv
datasets/exported/raw_ohlcv_csi300_20140601_20200801_metadata.json
```

数据集信息：

```text
shape:       (757978, 5)
index:       ["datetime", "instrument"]
columns:     ["open", "high", "low", "close", "volume"]
date range:  2014-06-03 ~ 2020-07-31
instruments: 549
```

后续读取方式：

```python
raw_df = (
    pd.read_csv("datasets/exported/raw_ohlcv_csi300_20140601_20200801.csv", parse_dates=["datetime"])
    .set_index(["datetime", "instrument"])
    .sort_index()
)
```

成员资格过滤应从：

```text
datasets/exported/raw_ohlcv_csi300_20140601_20200801_membership.csv
```

还原，而不是再调用 Qlib 的 `D.list_instruments()`。

注意：`.gitignore` 已忽略 `datasets/`，所以大 CSV 是本地产物，不会进 git；导出脚本会进 git。

## 4. 已讨论：工具和 MCP 的关系

结论：

- 普通函数是实际逻辑。
- tool 是 Agent 可调用的能力接口。
- MCP server 是把一组 tool 标准化暴露出来的服务。
- nanobot 是 MCP client，会连接 MCP server、发现工具、调用工具。

关系：

```text
普通 Python 函数
  ↓ 注册为 tool
MCP server 暴露工具
  ↓ tools/list
nanobot 发现工具
  ↓ tools/call
nanobot 调用工具
```

重要澄清：

- nanobot 不是静态扫描 Python 文件发现工具。
- 对 stdio MCP：nanobot 启动子进程，通过 stdin/stdout 进行 MCP 协议通信。
- 对 HTTP MCP：nanobot 连接 URL，通过 MCP HTTP 协议发现和调用工具。
- `calculator_mcp_sdk_client.py` 只是教学演示；真实 nanobot 已经内置 MCP client 逻辑，不需要用户自己写 client。

## 5. 已新增：手搓 HTTP 工具发现/调用示例

为帮助理解“不是扫进程，而是请求工具清单”，新增教学版 HTTP 工具服务：

```text
examples/calculator_http_tool_server.py
examples/calculator_http_tool_client.py
```

这个版本不是标准 MCP，只是用普通 HTTP 模拟两个核心动作：

```text
GET  /tools  -> 发现 add/subtract/multiply/divide
POST /call   -> 按 name + arguments 调用工具
```

运行方式：

```powershell
python examples/calculator_http_tool_server.py --host 127.0.0.1 --port 8766
python examples/calculator_http_tool_client.py --base-url http://127.0.0.1:8766
```

作用：教学用，帮助跟量化研究员解释“工具发现”和“工具调用”的基本模式。

## 6. 已新增：官方 MCP Python SDK 示例

用户指出实际情况应使用主流 MCP 库，而不是手搓协议。已确认本地环境已有：

```text
mcp 1.28.1
```

路径：

```text
D:\ProgramData\miniforge3\envs\py312\Lib\site-packages\mcp
```

新增真实 MCP SDK 示例：

```text
examples/calculator_mcp_sdk_server.py
examples/calculator_mcp_sdk_client.py
```

服务端使用：

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("calculator-sdk", host="127.0.0.1", port=8767, streamable_http_path="/mcp")

@mcp.tool()
def multiply(a: float, b: float) -> float:
    """Return a * b."""
    return a * b

mcp.run(transport="streamable-http")
```

已实际跑通：

```powershell
D:\ProgramData\miniforge3\envs\py312\python.exe examples/calculator_mcp_sdk_server.py --host 127.0.0.1 --port 8767
D:\ProgramData\miniforge3\envs\py312\python.exe examples/calculator_mcp_sdk_client.py --url http://127.0.0.1:8767/mcp
```

发现工具：

```text
add
subtract
multiply
divide
```

调用结果：

```text
multiply(a=12.5, b=8) -> 100.0
divide(a=10, b=4)     -> 2.5
```

真实 nanobot 配置 HTTP MCP 的思路：

```json
{
  "tools": {
    "mcpServers": {
      "calculator": {
        "url": "http://127.0.0.1:8767/mcp"
      }
    }
  }
}
```

如有鉴权：

```json
{
  "tools": {
    "mcpServers": {
      "quant-research": {
        "url": "https://research.example.com/mcp",
        "headers": {
          "Authorization": "Bearer YOUR_TOKEN"
        }
      }
    }
  }
}
```

## 7. 与量化研究员合作时的沟通方式

可以对量化研究员这样描述交付要求：

```text
请把你们已有的研究函数用 MCP SDK 封装成 MCP server。
每个核心函数用 @mcp.tool() 暴露。
参数使用简单类型和清晰类型注解。
docstring 写清楚用途、输入、输出和注意事项。
大结果落盘，tool 返回结构化摘要和 artifact path。
最终交付 MCP endpoint、工具清单、鉴权方式和示例调用。
```

推荐量化工具接口风格：

```python
@mcp.tool()
def evaluate_factor_ic(
    factor_csv_path: str,
    return_csv_path: str,
    start_date: str,
    end_date: str,
    method: str = "spearman",
) -> dict:
    """Evaluate a factor's IC/ICIR over a date range."""
    return {
        "ic_mean": 0.034,
        "ic_std": 0.12,
        "icir": 0.28,
        "coverage": 0.97,
        "n_days": 756,
        "warnings": [],
        "artifact_paths": {},
    }
```

## 8. 已完成：量价/行情类基础因子 skill/scripts

用户决定先从简单做起：不急着 MCP 化，先把原流程中的“从 OHLCV 计算基础因子”整理为 skill 中的可执行脚本，让 Agent 按脚本稳定执行。

已新增 skill 草稿：

```text
for_agent/quant-price-volume-factor-mining/
  SKILL.md
  scripts/
    compute_price_volume_ohlcv_factors.py
```

定位：

- 这是量价/行情类基础因子计算 skill。
- 输入是标准长表行情 CSV，必须包含：

```text
datetime, instrument, open, high, low, close, volume
```

- 输出是长表因子文件，列为：

```text
datetime, instrument, MOM_5D, MOM_20D, VOL_20D, TURN_5D, MA_DEV, DAY_RANGE, PRICE_POS, LABEL
```

当前脚本只使用 `raw_ohlcv_csi300_20140601_20200801.csv`，不读取：

```text
raw_ohlcv_csi300_20140601_20200801_membership.csv
raw_ohlcv_csi300_20140601_20200801_metadata.json
```

原因：当前阶段只负责量价/行情类基础因子计算，不做成员资格过滤、数据集版本校验、train/valid/test 切分。

后续分工：

- `membership.csv`：在“因子预处理/股票池过滤”阶段使用，用于恢复每只股票的有效成分区间。
- `metadata.json`：在“数据集 registry / 实验追踪”阶段使用，用于记录数据版本、导出参数和可追溯信息。

脚本运行方式：

```powershell
D:\ProgramData\miniforge3\envs\py312\python.exe for_agent/quant-price-volume-factor-mining/scripts/compute_price_volume_ohlcv_factors.py `
  --raw-csv datasets/exported/raw_ohlcv_csi300_20140601_20200801.csv
```

重要设计调整：

- `SKILL.md` 已改为纯中文描述。
- 已去掉 `Step 2`、`quant_workflow_from_scratch.py` 等离线实验术语，使 skill 更像通用能力。
- 脚本要求显式传入 `--raw-csv`，不再依赖硬编码默认输入路径。
- `--output-prefix` 可选；不传时会根据输入 CSV 文件名自动生成。
- 输出目录不存在时会自动创建。
- 输出文件默认放在：

```text
for_agent/results/price_volume_ohlcv_factors/
```

已验证正常运行：

```text
宽表 close  shape = (1473, 549)  (1473 交易日 × 549 股票)
MOM_5D        shape=(1473, 549)  NaN占比=12.3%
MOM_20D       shape=(1473, 549)  NaN占比=14.2%
VOL_20D       shape=(1473, 549)  NaN占比=7.9%
TURN_5D       shape=(1473, 549)  NaN占比=12.7%
MA_DEV        shape=(1473, 549)  NaN占比=18.6%
DAY_RANGE     shape=(1473, 549)  NaN占比=11.5%
PRICE_POS     shape=(1473, 549)  NaN占比=11.1%
LABEL         shape=(1473, 549)  NaN占比=11.5%
因子数据（long format）shape = (808677, 8)
```

读回完整因子表也正常：

```text
(808677, 10)
['datetime', 'instrument', 'MOM_5D', 'MOM_20D', 'VOL_20D', 'TURN_5D', 'MA_DEV', 'DAY_RANGE', 'PRICE_POS', 'LABEL']
```

已增强异常处理，方便 Agent 出错时理解：

- `--raw-csv` 文件不存在。
- `--raw-csv` 不是文件。
- 输入 CSV 为空。
- 输入 CSV 缺少必要列。
- `--preview-rows` 不是正整数。
- 输出目录无法创建，或输出路径已存在但不是目录。
- `--output-prefix` 包含不适合作为文件名的字符。
- 默认输出中文错误和排查建议；加 `--debug` 才打印完整 traceback。
- 如果原始长表不是完整的 `日期 × 股票` 矩阵，会输出普通警告，提示宽表存在缺口，滚动因子可能因此产生额外 NaN。

已运行 skill 校验：

```text
Skill is valid!
```

## 9. 当前未提交变更

当前 git 状态中重要变更：

```text
M  for_agent/量化投研全流程Agent化改造计划.md
?? for_agent/quant-price-volume-factor-mining/
?? for_agent/results/
?? for_agent/量化投研Agent化改造checkpoint.md
?? examples/export_raw_ohlcv_to_csv.py
?? examples/calculator_http_tool_server.py
?? examples/calculator_http_tool_client.py
?? examples/calculator_mcp_sdk_server.py
?? examples/calculator_mcp_sdk_client.py
?? for_agent/nanobot_workspace/
```

此外，用户原本已有：

```text
examples/quant_workflow_from_scratch.py
```

存在修改状态或历史上被提示修改过，但不是本轮新增改造的重点；不要随意回滚用户已有改动。

还看到 `.gitignore` 和 `.vscode/launch.json` 在工作区中处于修改状态，这些不是本轮因子 skill 的核心改动，处理前应先确认是否为用户自己的 IDE/配置修改。

## 10. 建议下一步

更贴合当前节奏的下一步是继续“单阶段 skill/scripts 化”，而不是马上进入完整 `quant_agent/` 抽库：

1. 做“因子预处理/股票池过滤” skill/scripts：
   - 输入：上一步生成的因子长表 CSV.GZ。
   - 输入：`membership.csv`。
   - 执行：截面 MAD 去极值、Z-Score 标准化、dropna、成员资格过滤。
   - 输出：`clean_factor_*.csv.gz`、预览、诊断 JSON。
2. 该阶段需要开始使用：

```text
datasets/exported/raw_ohlcv_csi300_20140601_20200801_membership.csv
```

3. 等 Step 2/Step 3 都稳定后，再把共用逻辑抽到 `quant_agent/` 研究库。
4. 随后再封装 MCP tool。

原计划里的 Milestone A 仍然有效，但可以拆得更细：

1. 新建 `quant_agent/` 研究库。
2. 先实现 CSV 数据入口：
   - `load_raw_ohlcv_csv()`
   - `load_membership_csv()`
   - `build_market_data_bundle()`
3. 从 `quant_workflow_from_scratch.py` 抽出：
   - 因子计算
   - 因子预处理
   - IC/ICIR 分析
   - 因子合成
   - Top-K 回测
   - 绩效报告
4. 新增一个 CSV-first 入口脚本。
5. 做“原 Qlib 版本 vs CSV-first 版本”的基线对齐。
6. 再把稳定函数包装成 `quant_mcp_server.py`，使用官方 MCP Python SDK 的 `FastMCP`。

## 11. 下次继续对话建议开场

可以直接说：

```text
请阅读 for_agent/量化投研Agent化改造checkpoint.md 和 for_agent/量化投研全流程Agent化改造计划.md，然后继续做“因子预处理/股票池过滤” skill/scripts。
```
