"""因子合成脚本的公共 I/O、校验和 IC 评估函数。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


INDEX_COLUMNS = ["datetime", "instrument"]
DEFAULT_LABEL_COLUMN = "LABEL"
DEFAULT_TRAIN_CSV = (
    "for_agent/results/factor_preprocessing/"
    "preprocessed_price_volume_ohlcv_factors_raw_ohlcv_csi300_20140601_20200801_train.csv.gz"
)
DEFAULT_VALID_CSV = (
    "for_agent/results/factor_preprocessing/"
    "preprocessed_price_volume_ohlcv_factors_raw_ohlcv_csi300_20140601_20200801_valid.csv.gz"
)
DEFAULT_TEST_CSV = (
    "for_agent/results/factor_preprocessing/"
    "preprocessed_price_volume_ohlcv_factors_raw_ohlcv_csi300_20140601_20200801_test.csv.gz"
)
DEFAULT_SELECTED_FACTORS_JSON = (
    "for_agent/results/factor_ic_analysis/"
    "factor_ic_preprocessed_price_volume_ohlcv_factors_raw_ohlcv_csi300_20140601_20200801_train_selected_factors.json"
)
DEFAULT_OUTPUT_DIR = "for_agent/results/factor_combination"
DEFAULT_OUTPUT_PREFIX = "factor_combination_preprocessed_price_volume_ohlcv_factors_raw_ohlcv_csi300_20140601_20200801"


def validate_file(path_value: str | Path, arg_name: str) -> Path:
    path = Path(path_value)
    if not path.exists():
        raise FileNotFoundError(f"找不到 {arg_name} 指向的文件：{path}")
    if not path.is_file():
        raise ValueError(f"{arg_name} 指向的路径不是文件：{path}")
    return path


def parse_csv_list(value: str | None, arg_name: str) -> list[str] | None:
    if value is None:
        return None
    items = [item.strip() for item in value.split(",") if item.strip()]
    if not items:
        raise ValueError(f"{arg_name} 不能为空；请传入逗号分隔的名称，或删除该参数。")
    return items


def validate_output_dir(output_dir: str | Path) -> Path:
    out_dir = Path(output_dir)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OSError(
            f"无法创建输出目录：{out_dir}\n"
            "请检查 --output-dir 路径、磁盘权限或目录是否被其他程序占用。"
        ) from exc
    if not out_dir.is_dir():
        raise ValueError(f"输出路径已存在但不是目录：{out_dir}")
    return out_dir


def build_output_prefix(output_prefix: str | None) -> str:
    prefix = output_prefix or DEFAULT_OUTPUT_PREFIX
    unsafe_chars = set('<>:"/\\|?*')
    if any(ch in unsafe_chars for ch in prefix):
        raise ValueError(
            f"输出文件名前缀包含不适合作为文件名的字符：{prefix}\n"
            "请通过 --output-prefix 传入只包含字母、数字、下划线或短横线的名称。"
        )
    return prefix


def load_selected_factors(selected_factors_json: str | Path | None) -> list[str] | None:
    if selected_factors_json is None:
        return None
    path = validate_file(selected_factors_json, "--selected-factors-json")
    data = json.loads(path.read_text(encoding="utf-8"))
    selected = data.get("selected_factors")
    if not isinstance(selected, list) or not selected:
        raise ValueError(f"--selected-factors-json 中没有有效的 selected_factors 列表：{path}")
    factors = [str(item) for item in selected if str(item).strip()]
    if not factors:
        raise ValueError(f"--selected-factors-json 中的 selected_factors 为空：{path}")
    return factors


def load_factor_directions(selected_factors_json: str | Path | None) -> dict[str, float]:
    """读取 IC 分析输出的训练期方向；旧 JSON 缺少该字段时返回空字典。"""
    if selected_factors_json is None:
        return {}
    path = validate_file(selected_factors_json, "--selected-factors-json")
    data = json.loads(path.read_text(encoding="utf-8"))
    raw = data.get("factor_directions", {})
    if not isinstance(raw, dict):
        raise ValueError(f"factor_directions 必须是对象：{path}")
    directions = {}
    for factor, value in raw.items():
        numeric = float(value)
        if numeric not in {-1.0, 1.0}:
            raise ValueError(f"因子 {factor} 的方向必须为 +1 或 -1，当前为 {value}")
        directions[str(factor)] = numeric
    return directions


def load_factor_sample(path_value: str | Path, label_column: str, arg_name: str) -> pd.DataFrame:
    path = validate_file(path_value, arg_name)
    df = pd.read_csv(path)
    if df.empty:
        raise ValueError(f"{arg_name} 指向的 CSV 为空：{path}")

    required = INDEX_COLUMNS + [label_column]
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(
            f"{arg_name} 缺少必要列：{missing}\n"
            f"输入至少需要包含：{required}，以及一个或多个因子列。"
        )

    df["datetime"] = pd.to_datetime(df["datetime"], errors="raise")
    return df.set_index(INDEX_COLUMNS).sort_index()


def load_splits(train_csv: str | Path, valid_csv: str | Path, test_csv: str | Path, label_column: str) -> dict[str, pd.DataFrame]:
    return {
        "train": load_factor_sample(train_csv, label_column, "--train-csv"),
        "valid": load_factor_sample(valid_csv, label_column, "--valid-csv"),
        "test": load_factor_sample(test_csv, label_column, "--test-csv"),
    }


def infer_factor_columns(columns: list[str], label_column: str) -> list[str]:
    excluded = set(INDEX_COLUMNS + [label_column])
    inferred = [col for col in columns if col not in excluded]
    if not inferred:
        raise ValueError(
            "未能从输入 CSV 自动识别因子列。\n"
            f"输入至少需要包含 {INDEX_COLUMNS + [label_column]}，以及至少一个因子列；"
            "也可以通过 --factor-cols 或 --selected-factors-json 指定。"
        )
    return inferred


def resolve_factor_columns(
    splits: dict[str, pd.DataFrame],
    label_column: str,
    explicit_factor_cols: list[str] | None,
    selected_factors: list[str] | None,
) -> tuple[list[str], str]:
    if explicit_factor_cols is not None:
        factor_columns = explicit_factor_cols
        source = "--factor-cols"
    elif selected_factors is not None:
        factor_columns = selected_factors
        source = "--selected-factors-json"
    else:
        factor_columns = infer_factor_columns(list(splits["train"].columns), label_column)
        source = "auto"

    missing_by_split = {
        name: [col for col in factor_columns if col not in df.columns]
        for name, df in splits.items()
    }
    missing_by_split = {name: cols for name, cols in missing_by_split.items() if cols}
    if missing_by_split:
        raise ValueError(f"合成因子列在部分样本中不存在：{missing_by_split}")
    return factor_columns, source


def compute_daily_score_ic(
    score: pd.Series,
    sample_df: pd.DataFrame,
    label_column: str,
    min_samples: int,
) -> pd.Series:
    if min_samples <= 0:
        raise ValueError("--min-samples 必须是正整数。")

    ic_values: list[tuple[pd.Timestamp, float]] = []
    score = score.dropna()
    if score.empty:
        return pd.Series(dtype="float64", name="ic")

    for date, grp in sample_df.groupby(level="datetime", sort=True):
        if date not in score.index.get_level_values("datetime"):
            continue
        score_on_date = score.xs(date, level="datetime")
        label_on_date = grp[label_column].xs(date, level="datetime").dropna()
        common_index = score_on_date.index.intersection(label_on_date.index)
        if len(common_index) < min_samples:
            continue
        ic_value = score_on_date.loc[common_index].corr(label_on_date.loc[common_index], method="spearman")
        if pd.isna(ic_value):
            continue
        ic_values.append((date, float(ic_value)))

    return pd.Series(
        [value for _, value in ic_values],
        index=pd.Index([date for date, _ in ic_values], name="datetime"),
        name="ic",
    )


def summarize_ic_series(ic_series: pd.Series) -> dict[str, Any]:
    s = pd.to_numeric(ic_series, errors="coerce").dropna()
    if s.empty:
        return {"IC均值": pd.NA, "IC标准差": pd.NA, "ICIR": pd.NA, "IC>0占比": pd.NA, "计算日数": 0}
    return {
        "IC均值": round(float(s.mean()), 4),
        "IC标准差": round(float(s.std()), 4),
        "ICIR": round(float(s.mean() / (s.std() + 1e-9)), 4),
        "IC>0占比": round(float((s > 0).mean()), 3),
        "计算日数": int(len(s)),
    }


def build_score_frame(score: pd.Series, score_column: str) -> pd.DataFrame:
    df = score.rename(score_column).to_frame()
    df.index.names = INDEX_COLUMNS
    return df


def evaluate_method_ic(
    method: str,
    scores: dict[str, pd.Series],
    splits: dict[str, pd.DataFrame],
    label_column: str,
    min_samples: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    daily_ic = {
        split: compute_daily_score_ic(scores[split], splits[split], label_column, min_samples)
        for split in ["train", "valid", "test"]
    }
    summary = pd.DataFrame({(method, split): summarize_ic_series(ic) for split, ic in daily_ic.items()}).T
    summary.index.names = ["method", "split"]
    daily_ic_df = pd.DataFrame({f"{method}_{split}": ic for split, ic in daily_ic.items()}).sort_index()
    return summary, daily_ic_df


def save_method_outputs(
    method: str,
    scores: dict[str, pd.Series],
    ic_summary: pd.DataFrame,
    daily_ic: pd.DataFrame,
    diagnostics: dict[str, Any],
    output_dir: str | Path,
    output_prefix: str,
    preview_rows: int,
    score_column: str,
    extra_frames: dict[str, pd.DataFrame] | None = None,
    extra_json: dict[str, Any] | None = None,
) -> dict[str, str]:
    if preview_rows <= 0:
        raise ValueError("--preview-rows 必须是正整数。")
    out_dir = validate_output_dir(output_dir)
    paths: dict[str, str] = {}
    previews = []

    for split, score in scores.items():
        score_path = out_dir / f"{output_prefix}_{method}_{split}_score.csv.gz"
        score_df = build_score_frame(score, score_column)
        score_df.to_csv(score_path, compression="gzip")
        paths[f"{method}_{split}_score_csv_gz"] = str(score_path)

        preview = score_df.head(preview_rows).copy().reset_index()
        preview["method"] = method
        preview["split"] = split
        previews.append(preview)

    ic_summary_path = out_dir / f"{output_prefix}_{method}_ic_summary.csv"
    daily_ic_path = out_dir / f"{output_prefix}_{method}_daily_score_ic.csv.gz"
    preview_path = out_dir / f"{output_prefix}_{method}_preview.csv"
    diagnostics_path = out_dir / f"{output_prefix}_{method}_diagnostics.json"

    ic_summary.to_csv(ic_summary_path, encoding="utf-8-sig")
    daily_ic.to_csv(daily_ic_path, compression="gzip")
    pd.concat(previews, ignore_index=True).to_csv(preview_path, index=False, encoding="utf-8-sig")
    diagnostics_path.write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8")

    paths[f"{method}_ic_summary_csv"] = str(ic_summary_path)
    paths[f"{method}_daily_score_ic_csv_gz"] = str(daily_ic_path)
    paths[f"{method}_preview_csv"] = str(preview_path)
    paths[f"{method}_diagnostics_json"] = str(diagnostics_path)

    for name, frame in (extra_frames or {}).items():
        csv_path = out_dir / f"{output_prefix}_{method}_{name}.csv"
        frame.to_csv(csv_path, encoding="utf-8-sig")
        paths[f"{method}_{name}_csv"] = str(csv_path)

    for name, data in (extra_json or {}).items():
        json_path = out_dir / f"{output_prefix}_{method}_{name}.json"
        json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        paths[f"{method}_{name}_json"] = str(json_path)

    return paths


def print_paths(paths: dict[str, str]) -> None:
    print("\n脚本输出文件:")
    for name, path in paths.items():
        print(f"  {name}: {path}")
