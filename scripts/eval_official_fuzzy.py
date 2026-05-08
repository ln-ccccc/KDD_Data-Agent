#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd


def _normalize_value(value: Any) -> str:
    if pd.isna(value):
        return "__nan__"
    text = str(value).strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def _normalize_df(df: pd.DataFrame) -> pd.DataFrame:
    normalized = df.copy()
    normalized.columns = [str(c).strip().lower() for c in normalized.columns]
    for col in normalized.columns:
        normalized[col] = normalized[col].map(_normalize_value)
    return normalized


def _series_equal(gold_series: pd.Series, pred_series: pd.Series, atol: float = 1e-5) -> bool:
    g = gold_series.reset_index(drop=True)
    p = pred_series.reset_index(drop=True)
    if len(g) != len(p):
        return False
    for gv, pv in zip(g, p):
        if gv == pv:
            continue
        try:
            g_num = float(gv)
            p_num = float(pv)
            if math.isfinite(g_num) and math.isfinite(p_num) and abs(g_num - p_num) <= atol:
                continue
        except Exception:
            pass
        return False
    return True


def _column_as_vector(series: pd.Series) -> list[str]:
    return sorted(series.tolist())


def _column_to_text(series: pd.Series, max_items: int = 400) -> str:
    values = _column_as_vector(series)
    clipped = values[:max_items]
    return " | ".join(clipped)


def _token_jaccard(a: str, b: str) -> float:
    ta = set(a.split())
    tb = set(b.split())
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


@dataclass
class Embedder:
    enabled: bool
    model_name: str
    fallback_reason: str | None = None
    _model: Any = None

    @classmethod
    def create(cls, model_name: str, disable_embedding: bool) -> "Embedder":
        if disable_embedding:
            return cls(enabled=False, model_name=model_name, fallback_reason="embedding disabled")
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore

            model = SentenceTransformer(model_name)
            return cls(enabled=True, model_name=model_name, _model=model)
        except Exception as exc:  # noqa: BLE001
            return cls(enabled=False, model_name=model_name, fallback_reason=str(exc))

    def encode(self, texts: list[str]) -> list[list[float]] | None:
        if not self.enabled or self._model is None:
            return None
        matrix = self._model.encode(texts, normalize_embeddings=True)
        if hasattr(matrix, "tolist"):
            return matrix.tolist()
        return [list(row) for row in matrix]


def _dot(a: list[float], b: list[float]) -> float:
    return float(sum(x * y for x, y in zip(a, b)))


def _best_fuzzy_matches(
    gold_vectors: dict[str, pd.Series],
    pred_vectors: dict[str, pd.Series],
    similarity_threshold: float,
    embedder: Embedder,
) -> tuple[set[str], set[str], list[dict[str, Any]]]:
    gold_cols = list(gold_vectors.keys())
    pred_cols = list(pred_vectors.keys())
    if not gold_cols or not pred_cols:
        return set(), set(), []

    gold_texts = [_column_to_text(gold_vectors[c]) for c in gold_cols]
    pred_texts = [_column_to_text(pred_vectors[c]) for c in pred_cols]

    sim: list[list[float]] = [[0.0 for _ in pred_cols] for _ in gold_cols]
    gold_embeddings = embedder.encode(gold_texts)
    pred_embeddings = embedder.encode(pred_texts)

    if gold_embeddings is not None and pred_embeddings is not None:
        for i, g_vec in enumerate(gold_embeddings):
            for j, p_vec in enumerate(pred_embeddings):
                sim[i][j] = _dot(g_vec, p_vec)
    else:
        for i, gtxt in enumerate(gold_texts):
            for j, ptxt in enumerate(pred_texts):
                sim[i][j] = _token_jaccard(gtxt, ptxt)

    used_g = set()
    used_p = set()
    details: list[dict[str, Any]] = []

    flat: list[tuple[float, int, int]] = []
    for i in range(len(sim)):
        for j in range(len(sim[i])):
            flat.append((float(sim[i][j]), i, j))
    flat.sort(reverse=True, key=lambda x: x[0])

    for score, gi, pj in flat:
        if score < similarity_threshold:
            break
        if gi in used_g or pj in used_p:
            continue
        used_g.add(gi)
        used_p.add(pj)
        details.append(
            {
                "gold_column": gold_cols[gi],
                "pred_column": pred_cols[pj],
                "similarity": round(score, 6),
                "mode": "embedding" if embedder.enabled else "token_jaccard",
            }
        )

    matched_gold = {gold_cols[i] for i in used_g}
    matched_pred = {pred_cols[j] for j in used_p}
    return matched_gold, matched_pred, details


def compare_with_fuzzy(
    pred_df: pd.DataFrame,
    gold_df: pd.DataFrame,
    similarity_threshold: float,
    embedder: Embedder,
) -> dict[str, Any]:
    if pred_df.shape[0] != gold_df.shape[0]:
        return {
            "is_correct": False,
            "reason": f"row_count_mismatch(pred={pred_df.shape[0]}, gold={gold_df.shape[0]})",
            "fuzzy_matches": [],
        }

    pred = _normalize_df(pred_df)
    gold = _normalize_df(gold_df)

    pred_vectors = {c: pd.Series(_column_as_vector(pred[c])) for c in pred.columns}
    gold_vectors = {c: pd.Series(_column_as_vector(gold[c])) for c in gold.columns}

    matched_gold = set()
    matched_pred = set()

    for g_col, g_vec in gold_vectors.items():
        for p_col, p_vec in pred_vectors.items():
            if p_col in matched_pred:
                continue
            if _series_equal(g_vec, p_vec):
                matched_gold.add(g_col)
                matched_pred.add(p_col)
                break

    unmatched_gold = {c: v for c, v in gold_vectors.items() if c not in matched_gold}
    unmatched_pred = {c: v for c, v in pred_vectors.items() if c not in matched_pred}

    # Official rule: allow two gold columns being combined into one prediction column.
    if len(unmatched_gold) == 2 and unmatched_pred:
        ug_cols = list(unmatched_gold.keys())
        g1 = gold[ug_cols[0]].astype(str)
        g2 = gold[ug_cols[1]].astype(str)
        combo1 = pd.Series(sorted((g1 + " " + g2).tolist()))
        combo2 = pd.Series(sorted((g2 + " " + g1).tolist()))
        for p_col, p_vec in unmatched_pred.items():
            if _series_equal(combo1, p_vec) or _series_equal(combo2, p_vec):
                matched_gold.update(ug_cols)
                matched_pred.add(p_col)
                break

    unmatched_gold = {c: v for c, v in gold_vectors.items() if c not in matched_gold}
    unmatched_pred = {c: v for c, v in pred_vectors.items() if c not in matched_pred}

    fuzzy_details: list[dict[str, Any]] = []
    if unmatched_gold and unmatched_pred:
        fuzzy_gold, fuzzy_pred, fuzzy_details = _best_fuzzy_matches(
            gold_vectors=unmatched_gold,
            pred_vectors=unmatched_pred,
            similarity_threshold=similarity_threshold,
            embedder=embedder,
        )
        matched_gold |= fuzzy_gold
        matched_pred |= fuzzy_pred

    is_correct = len(matched_gold) == len(gold_vectors)
    reason = "" if is_correct else "unmatched_columns"
    return {"is_correct": is_correct, "reason": reason, "fuzzy_matches": fuzzy_details}


def evaluate_run(
    run_dir: Path,
    gold_dir: Path,
    similarity_threshold: float,
    embedder: Embedder,
) -> dict[str, Any]:
    summary_path = run_dir / "summary.json"
    if not summary_path.exists():
        raise FileNotFoundError(f"summary.json not found: {summary_path}")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    tasks = summary.get("tasks", [])
    total = len(tasks)
    correct = 0
    rows: list[dict[str, Any]] = []

    for task in tasks:
        task_id = task.get("task_id", "unknown")
        pred_csv = run_dir / task_id / "prediction.csv"
        gold_csv = gold_dir / task_id / "gold.csv"
        item: dict[str, Any] = {
            "task_id": task_id,
            "status": "Failed",
            "reason": "",
            "is_correct": False,
            "fuzzy_matches": [],
        }

        if not task.get("succeeded", False):
            item["status"] = "Execution Error"
            item["reason"] = task.get("failure_reason", "unknown")
            rows.append(item)
            continue
        if not pred_csv.exists():
            item["status"] = "Missing Prediction"
            item["reason"] = "prediction.csv not found"
            rows.append(item)
            continue
        if not gold_csv.exists():
            item["status"] = "Missing Gold"
            item["reason"] = "gold.csv not found"
            rows.append(item)
            continue

        try:
            pred_df = pd.read_csv(pred_csv)
            gold_df = pd.read_csv(gold_csv)
            cmp_result = compare_with_fuzzy(
                pred_df=pred_df,
                gold_df=gold_df,
                similarity_threshold=similarity_threshold,
                embedder=embedder,
            )
            item["is_correct"] = bool(cmp_result["is_correct"])
            item["reason"] = cmp_result["reason"]
            item["fuzzy_matches"] = cmp_result["fuzzy_matches"]
            if item["is_correct"]:
                item["status"] = "Correct"
                correct += 1
            else:
                item["status"] = "Wrong Answer"
        except Exception as exc:  # noqa: BLE001
            item["status"] = "Evaluation Error"
            item["reason"] = str(exc)

        rows.append(item)

    pass_rate = (correct / total) if total else 0.0
    return {
        "run_id": summary.get("run_id", run_dir.name),
        "total_tasks": total,
        "correct_tasks": correct,
        "pass_rate": pass_rate,
        "embedding_enabled": embedder.enabled,
        "embedding_model": embedder.model_name,
        "embedding_fallback_reason": embedder.fallback_reason,
        "similarity_threshold": similarity_threshold,
        "results": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Official-style fuzzy evaluator (column-as-vector + optional embedding similarity)."
    )
    parser.add_argument("--run-dir", type=Path, required=True, help="Run directory containing summary.json")
    parser.add_argument("--gold-dir", type=Path, default=Path("data/public/output"), help="Gold directory")
    parser.add_argument("--output-report", type=Path, default=None, help="Path to write JSON report")
    parser.add_argument(
        "--embedding-model",
        type=str,
        default="Alibaba-NLP/gte-Qwen2-1.5B-instruct",
        help="Sentence-transformers model name for fuzzy column matching",
    )
    parser.add_argument(
        "--disable-embedding",
        action="store_true",
        help="Disable embedding matching and use token jaccard fallback",
    )
    parser.add_argument("--similarity-threshold", type=float, default=0.82, help="Similarity threshold in [0,1]")
    args = parser.parse_args()

    embedder = Embedder.create(args.embedding_model, args.disable_embedding)
    report = evaluate_run(
        run_dir=args.run_dir,
        gold_dir=args.gold_dir,
        similarity_threshold=args.similarity_threshold,
        embedder=embedder,
    )
    if args.output_report:
        args.output_report.parent.mkdir(parents=True, exist_ok=True)
        args.output_report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({k: report[k] for k in ("run_id", "total_tasks", "correct_tasks", "pass_rate", "embedding_enabled", "embedding_model", "embedding_fallback_reason", "similarity_threshold")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
