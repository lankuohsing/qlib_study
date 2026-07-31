---
name: quant-portfolio-backtest
description: 从一个或多个选股得分运行状态化 Top-K 回测，模拟 T+1 开盘成交、T+2 开盘结算、现金、实际持仓、动态成分资格、零成交量或缺价冻结、排名缓冲、收益门槛、实际成交成本和统一期末清仓。用于比较策略、评估交易约束或验证新合成得分。
---

# 状态化组合回测

运行 `scripts/run_topk_portfolio_backtest.py`：

```powershell
D:\ProgramData\miniforge3\envs\py312\python.exe for_agent/quant-portfolio-backtest/scripts/run_topk_portfolio_backtest.py `
  --raw-csv datasets/exported/raw_ohlcv_csi300_20140601_20200801.csv `
  --membership-csv datasets/exported/raw_ohlcv_csi300_20140601_20200801_membership.csv `
  --score-csvs "equal_weight=path/to/ew_test_score.csv.gz,linear_regression=path/to/lr_test_score.csv.gz" `
  --test-start 2018-01-01 --test-end 2019-06-01
```

先读 [回测契约](references/data-contract.md)。

## 关键规则

- T 日收盘信号只能在 T+1 开盘执行，收益区间为 T+1 开盘至 T+2 开盘。
- 在 T+1 成交前再次校验当日成分资格；成员资格只限制新买，不强行消失已有仓位。
- 仅在开盘价有限且大于 0、成交量有限且大于 0 时交易；失败卖单成为冻结持仓并继续估值。
- 先卖后买；现金不足时按比例缩放所有可成交买单；成本只按实际成交额收取。
- EW 等无量纲分数只用排名缓冲，绝不能直接与成本率比较。
- 默认 `linear_regression` 得分按收益率解释，新买门槛为两倍单边成本加安全余量，持有门槛为 0。自定义方法用 `--method-config-json` 明确覆盖。
- 所有方法和动态成分股等权基准使用同一结算日并尝试期末清仓。

检查每日换手、现金权重、未成交买卖、冻结持仓、期末清仓和统一日期覆盖。高现金导致的低回撤不能解释成选股能力增强。
