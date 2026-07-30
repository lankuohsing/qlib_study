"""因子合成批量入口：按需运行一个或多个方法脚本。"""

from __future__ import annotations

import argparse
import subprocess
import sys
import traceback
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SUPPORTED_METHODS = {
    "equal_weight": SCRIPT_DIR / "combine_equal_weight_scores.py",
    "linear_regression": SCRIPT_DIR / "combine_linear_regression_scores.py",
}


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


def parse_methods(value: str) -> list[str]:
    methods = [item.strip() for item in value.split(",") if item.strip()]
    if not methods:
        raise ValueError("--methods 不能为空。")
    unsupported = [method for method in methods if method not in SUPPORTED_METHODS]
    if unsupported:
        raise ValueError(f"--methods 包含暂不支持的方法：{unsupported}；支持的方法为：{list(SUPPORTED_METHODS)}")
    return methods


def append_if_present(command: list[str], name: str, value: str | int | None) -> None:
    if value is not None:
        command.extend([name, str(value)])


def build_method_command(method: str, args: argparse.Namespace) -> list[str]:
    command = [sys.executable, "-X", "utf8", str(SUPPORTED_METHODS[method])]
    append_if_present(command, "--train-csv", args.train_csv)
    append_if_present(command, "--valid-csv", args.valid_csv)
    append_if_present(command, "--test-csv", args.test_csv)
    append_if_present(command, "--selected-factors-json", args.selected_factors_json)
    append_if_present(command, "--factor-cols", args.factor_cols)
    append_if_present(command, "--label-col", args.label_col)
    append_if_present(command, "--output-dir", args.output_dir)
    append_if_present(command, "--output-prefix", args.output_prefix)
    append_if_present(command, "--min-samples", args.min_samples)
    append_if_present(command, "--preview-rows", args.preview_rows)
    if args.debug:
        command.append("--debug")
    return command


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="批量运行因子合成方法。具体算法分别由独立方法脚本实现。",
    )
    parser.add_argument("--methods", default="equal_weight,linear_regression", help="逗号分隔的合成方法，支持 equal_weight、linear_regression。")
    parser.add_argument("--train-csv", default=None, help="训练集样本 CSV/CSV.GZ 路径；不传时使用方法脚本默认值。")
    parser.add_argument("--valid-csv", default=None, help="验证集样本 CSV/CSV.GZ 路径；不传时使用方法脚本默认值。")
    parser.add_argument("--test-csv", default=None, help="测试集样本 CSV/CSV.GZ 路径；不传时使用方法脚本默认值。")
    parser.add_argument("--selected-factors-json", default=None, help="IC 分析阶段输出的候选因子 JSON；不传时使用方法脚本默认值。")
    parser.add_argument("--factor-cols", default=None, help="可选，逗号分隔的因子列名；优先级高于 --selected-factors-json。")
    parser.add_argument("--label-col", default=None, help="标签列名；不传时使用方法脚本默认值 LABEL。")
    parser.add_argument("--output-dir", default=None, help="输出目录；不传时使用方法脚本默认值。")
    parser.add_argument("--output-prefix", default=None, help="输出文件名前缀；不传时使用方法脚本默认值。")
    parser.add_argument("--min-samples", type=int, default=None, help="每日截面计算合成得分 IC 所需最小样本数。")
    parser.add_argument("--preview-rows", type=int, default=None, help="预览 CSV 保存的行数。")
    parser.add_argument("--debug", action="store_true", help="出错时打印完整 Python traceback，便于开发调试。")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        methods = parse_methods(args.methods)
        print("\n" + "=" * 65, flush=True)
        print("  因子合成批量入口", flush=True)
        print("=" * 65, flush=True)
        print(f"将依次运行方法: {methods}", flush=True)
        for method in methods:
            print("\n" + "-" * 65, flush=True)
            print(f"运行方法: {method}", flush=True)
            print("-" * 65, flush=True)
            subprocess.run(build_method_command(method, args), check=True)
    except Exception as exc:
        print("\n[错误] 因子合成批量入口失败。", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        print("\n建议排查：", file=sys.stderr)
        print("  1. 确认 --methods 只包含 equal_weight 或 linear_regression。", file=sys.stderr)
        print("  2. 如传入路径参数，确认 train/valid/test 样本和 selected_factors.json 都存在。", file=sys.stderr)
        print("  3. 如果某个方法失败，可直接运行对应方法脚本定位问题。", file=sys.stderr)
        print("  4. 如需调试细节，请追加 --debug 查看完整 traceback。", file=sys.stderr)
        if getattr(args, "debug", False):
            print("\n完整 traceback:", file=sys.stderr)
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
