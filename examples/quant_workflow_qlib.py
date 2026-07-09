"""
量化投资全流程 —— 纯 Qlib 实现（用于与 quant_workflow_from_scratch.py 对比）
=====================================================================
使用与 from_scratch 版本完全相同的：
  - 股票池：CSI300
  - 时间范围：train 2015-2017 / valid 2018 / test 2019-2020
  - 7 个基础因子（相同表达式）
  - 模型：LinearRegression（qlib.contrib.model.linear.LinearModel）
  - 策略：TopkDropoutStrategy topk=30
  - 手续费：买 0.05% + 卖 0.15%

对比关注点：
  1. 特征定义方式（表达式 vs pandas rolling）
  2. 预处理方式（Qlib processor vs 手写截面函数）
  3. 代码量与透明度
  4. IC 和回测结果是否一致
"""

import os
import warnings

os.environ["MLFLOW_ALLOW_FILE_STORE"] = "true"
warnings.filterwarnings("ignore")

import pandas as pd

import qlib
from qlib.constant import REG_CN
from qlib.utils import init_instance_by_config
from qlib.workflow import R
from qlib.workflow.record_temp import SignalRecord, PortAnaRecord, SigAnaRecord
from qlib.data.dataset import DatasetH
from qlib.data.dataset.handler import DataHandlerLP

# ─────────────────────────────────────────────────────────
# 全局配置（与 from_scratch 版本保持一致）
# ─────────────────────────────────────────────────────────
PROVIDER_URI = "/Users/guoxing.lan/projects/github/qlib_study/datasets/cn_data"
UNIVERSE     = "csi300"

TRAIN_START  = "2015-01-01"
TRAIN_END    = "2017-12-31"
VALID_START  = "2018-01-01"
VALID_END    = "2018-12-31"
TEST_START   = "2019-01-01"
TEST_END     = "2020-08-01"

TOPK         = 30
BENCH        = "SH000300"

# 与 from_scratch 完全相同的 7 个因子，用 Qlib 表达式语言描述
FIELDS = [
    "$close / Ref($close, 5)  - 1",              # MOM_5D
    "$close / Ref($close, 20) - 1",              # MOM_20D
    "Std($close / Ref($close, 1) - 1, 20)",      # VOL_20D
    "$volume / Mean($volume, 5)",                 # TURN_5D
    "$close / Mean($close, 20) - 1",             # MA_DEV
    "($high - $low) / Ref($close, 1)",           # DAY_RANGE
    "($close - $low) / ($high - $low + 1e-9)",   # PRICE_POS
]
NAMES = ["MOM_5D", "MOM_20D", "VOL_20D", "TURN_5D", "MA_DEV", "DAY_RANGE", "PRICE_POS"]

LABEL_FIELD = ["Ref($close, -1) / $close - 1"]
LABEL_NAME  = ["LABEL"]


def section(title):
    print(f"\n{'=' * 65}")
    print(f"  {title}")
    print(f"{'=' * 65}")


# ─────────────────────────────────────────────────────────
# macOS / Windows 上使用 spawn 启动子进程时，必须将所有实际执行
# 代码放在 if __name__ == '__main__': 里，否则子进程重新导入模块
# 时会递归触发 multiprocessing，导致 RuntimeError。
# Qlib 的 DataHandlerLP 初始化时会通过 joblib 并行加载数据，
# 因此必须加此保护。
# ─────────────────────────────────────────────────────────
if __name__ == "__main__":

    # ─────────────────────────────────────────────────────
    # Step 1  初始化
    # ─────────────────────────────────────────────────────
    section("Step 1  初始化")

    qlib.init(provider_uri=PROVIDER_URI, region=REG_CN)
    print(f"数据路径: {PROVIDER_URI}")
    print(f"股票池:   {UNIVERSE}")

    # ─────────────────────────────────────────────────────
    # Step 2  定义特征（Handler）
    # ─────────────────────────────────────────────────────
    section("Step 2  定义特征（DataHandlerLP + QlibDataLoader）")

    print("因子表达式（Qlib expression language）:")
    for name, expr in zip(NAMES, FIELDS):
        print(f"  {name:<12} = {expr}")
    print(f"  {'LABEL':<12} = {LABEL_FIELD[0]}")

    # 预处理对比：
    #   from_scratch：手写 winsorize_cs (MAD 3-sigma) + zscore_cs
    #   Qlib：CSZScoreNorm(method="robust") 等价于 MAD 去极值 + clip±3 + 标准化
    handler_config = {
        "class": "DataHandlerLP",
        "module_path": "qlib.data.dataset.handler",
        "kwargs": {
            "instruments": UNIVERSE,
            "start_time":  "2014-06-01",   # 同 from_scratch 留 rolling 窗口
            "end_time":    TEST_END,
            "data_loader": {
                "class": "QlibDataLoader",
                "kwargs": {
                    "config": {
                        "feature": (FIELDS, NAMES),
                        "label":   (LABEL_FIELD, LABEL_NAME),
                    },
                    "freq": "day",
                },
            },
            # infer_processors：对所有 segment（含 test）做截面标准化
            "infer_processors": [
                {
                    "class": "CSZScoreNorm",
                    "module_path": "qlib.data.dataset.processor",
                    "kwargs": {"fields_group": "feature", "method": "robust"},
                },
            ],
            # learn_processors：在 infer 基础上再删掉 label 为 NaN 的行（仅影响训练）
            "learn_processors": [
                {"class": "DropnaLabel"},
            ],
        },
    }

    handler = init_instance_by_config(handler_config)
    print(f"\nHandler 初始化完成")

    # ─────────────────────────────────────────────────────
    # Step 3  构建 Dataset（切分三段）
    # ─────────────────────────────────────────────────────
    section("Step 3  构建 DatasetH（三段切分）")

    dataset = DatasetH(
        handler=handler,
        segments={
            "train": (TRAIN_START, TRAIN_END),
            "valid": (VALID_START, VALID_END),
            "test":  (TEST_START,  TEST_END),
        },
    )

    # 打印各段数据形状（与 from_scratch Step 3 末尾的切分打印对应）
    print("各段数据 shape（通过 dataset.prepare() 获取）:")
    for seg in ["train", "valid", "test"]:
        df = dataset.prepare(seg, col_set=["feature", "label"],
                             data_key=DataHandlerLP.DK_L)
        x = df["feature"]
        y = df["label"]
        n_days   = x.index.get_level_values("datetime").nunique()
        n_stocks = x.index.get_level_values("instrument").nunique()
        print(f"  {seg:<6}  feature shape={x.shape}  label shape={y.shape}"
              f"  → {n_days} 交易日 × ~{n_stocks} 股票/日")

    print(f"\n特征列: {list(dataset.prepare('train', col_set='feature', data_key=DataHandlerLP.DK_L).columns)}")

    # ─────────────────────────────────────────────────────
    # Step 4  定义模型（LinearRegression）
    # ─────────────────────────────────────────────────────
    section("Step 4  定义模型（LinearModel = sklearn LinearRegression）")

    model_config = {
        "class": "LinearModel",
        "module_path": "qlib.contrib.model.linear",
        "kwargs": {
            "estimator":     "ols",    # sklearn LinearRegression
            "fit_intercept": False,    # 与 from_scratch 保持一致
            "include_valid": True,     # 同时用 valid 段训练（与 from_scratch 一致）
        },
    }

    model = init_instance_by_config(model_config)
    print(f"模型: LinearModel(estimator='ols')  ← 对应 from_scratch 的 LinearRegression()")
    print(f"  fit_intercept=False  include_valid=True（用 train+valid 共同拟合）")

    # ─────────────────────────────────────────────────────
    # Step 5  定义回测配置
    # ─────────────────────────────────────────────────────
    section("Step 5  定义回测配置（与 from_scratch 对齐）")

    port_analysis_config = {
        "executor": {
            "class": "SimulatorExecutor",
            "module_path": "qlib.backtest.executor",
            "kwargs": {
                "time_per_step": "day",
                "generate_portfolio_metrics": True,
            },
        },
        "strategy": {
            "class": "TopkDropoutStrategy",
            "module_path": "qlib.contrib.strategy.signal_strategy",
            "kwargs": {
                "signal":  (model, dataset),
                "topk":    TOPK,
                "n_drop":  5,
            },
        },
        "backtest": {
            "start_time": TEST_START,
            "end_time":   TEST_END,
            "account":    100_000_000,
            "benchmark":  BENCH,
            "exchange_kwargs": {
                "freq":            "day",
                "limit_threshold": 0.095,
                "deal_price":      "close",
                "open_cost":       0.0005,
                "close_cost":      0.0015,
                "min_cost":        5,
            },
        },
    }

    print(f"策略: TopkDropoutStrategy  topk={TOPK}  n_drop=5")
    print(f"回测: {TEST_START} ~ {TEST_END}  初始资金=1亿  基准={BENCH}")
    print(f"手续费: 买 0.05% + 卖 0.15%  涨跌停阈值 9.5%")

    # ─────────────────────────────────────────────────────
    # Step 6  运行实验
    # ─────────────────────────────────────────────────────
    section("Step 6  运行实验（训练 → 预测 → 信号分析 → 回测）")

    with R.start(experiment_name="qlib_linear_workflow"):

        # 6-1 训练
        print("\n[6-1] 模型训练...")
        model.fit(dataset)

        # 打印学到的因子权重（与 from_scratch Step 5 的系数对比）
        if hasattr(model, "model") and hasattr(model.model, "coef_"):
            coef = pd.Series(model.model.coef_, index=NAMES).round(6)
            print(f"  学到的因子权重（对比 from_scratch LinearRegression 系数）:")
            for fname, c in coef.items():
                print(f"    {fname:<12} : {c:+.6f}")

        # 6-2 预测（生成信号）
        print("\n[6-2] 生成预测信号（SignalRecord）...")
        recorder = R.get_recorder()
        sr = SignalRecord(model, dataset, recorder)
        sr.generate()

        pred = recorder.load_object("pred.pkl")
        print(f"  预测信号 shape = {pred.shape}")
        print(f"  索引: {pred.index.names}  列: {list(pred.columns)}")
        print(pred.head(3))

        # 6-3 信号分析（IC / ICIR）
        print("\n[6-3] 信号有效性分析（SigAnaRecord）...")
        sar = SigAnaRecord(recorder)
        sar.generate()

        try:
            # SigAnaRecord 将 IC 汇总存在 sig_analysis.pkl
            sig = recorder.load_object("sig_analysis.pkl")
            print(f"  IC 汇总（对比 from_scratch ic_table）:")
            print(sig)
        except Exception:
            pass  # 已在 sar.generate() 时直接打印在日志里

        # 6-4 组合回测
        print("\n[6-4] 组合回测（PortAnaRecord）...")
        par = PortAnaRecord(recorder, port_analysis_config, "day")
        par.generate()

        try:
            port_metrics = recorder.load_object("portfolio_analysis/port_analysis_1day.pkl")
            print(f"\n  回测绩效（对比 from_scratch 绩效表）:")
            if isinstance(port_metrics, pd.DataFrame):
                print(port_metrics.to_string())
            else:
                print(port_metrics)
        except Exception as e:
            print(f"  （读取回测结果时出错：{e}，可在 mlflow UI 中查看）")

        print(f"\n  实验 run_id: {recorder.id}")
        print(f"  所有结果已保存到 Qlib Recorder，运行 `qrun` 或查看 mlruns/ 目录")

    # ─────────────────────────────────────────────────────
    # 附：两种实现的核心差异对照
    # ─────────────────────────────────────────────────────
    section("附：两种实现方式核心差异对照")

    print("""
  环节              from_scratch 版本                    Qlib 版本
  ─────────────────────────────────────────────────────────────────
  特征定义          pandas rolling 手写                  Qlib 表达式引擎
                    (close / close.shift(5) - 1)         ($close / Ref($close,5) - 1)

  预处理            手写 winsorize_cs + zscore_cs         CSZScoreNorm(method="robust")
                    (分两步，显式可见)                    (一步完成，稍有差异见注释)

  数据管理          手动 segment() 切片                  DatasetH 统一管理三段

  模型训练          sklearn LR.fit(X, y)                  model.fit(dataset)
                    (显式传 X, y 矩阵)                    (dataset 内部 prepare)

  信号生成          model.predict(X_test) → Series        SignalRecord.generate()
                    (手动对齐 index)                      (自动保存到 recorder)

  IC 分析           手写 spearmanr 循环                   SigAnaRecord.generate()
                    (逐日计算，全程可见)                  (自动计算，结果存 pkl)

  回测              手写逐日 P&L 循环                     PortAnaRecord.generate()
                    (换手率/手续费显式可见)                (SimulatorExecutor 封装)

  结果存储          CSV 文件（nav_curve.csv）              Qlib Recorder（mlruns/）
  ─────────────────────────────────────────────────────────────────
  注：CSZScoreNorm(method="robust") 先做 (x-median)/(MAD*1.4826) 再 clip±3，
      from_scratch 先 clip 再单独 zscore，两者结果接近但不完全相同。
    """)
