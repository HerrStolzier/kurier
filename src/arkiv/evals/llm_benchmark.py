"""End-to-end LLM benchmark runner for Kurier tasks."""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from arkiv.core.classifier import DEFAULT_CATEGORIES, Classifier
from arkiv.core.config import ArkivConfig, DatabaseConfig, LLMConfig
from arkiv.core.engine import Engine
from arkiv.core.hardware import default_eval_ollama_model
from arkiv.core.llm import completion
from arkiv.core.search_assistant import _build_prompt as build_search_prompt
from arkiv.evals.ai_search_benchmark import (
    EvaluationResult,
    evaluate_output,
    load_benchmark_cases,
    parse_model_output,
)

TaskName = Literal["classifier", "search", "retrieval"]

DEFAULT_REPORT_DIR = Path("eval-results")
DEFAULT_HUGGINGFACE_MODEL = "huggingface:openai/gpt-oss-20b:fastest"


@dataclass(frozen=True)
class ModelSpec:
    provider: str
    model: str
    label: str

    @property
    def uses_llm(self) -> bool:
        return self.provider != "baseline"


@dataclass(frozen=True)
class CaseDetail:
    task: str
    model: str
    case_id: str
    score: float
    expected: str
    actual: str
    error: str | None = None


@dataclass(frozen=True)
class TaskSummary:
    task: str
    model: str
    provider: str
    status: str
    cases: int
    overall_score: float
    accuracy: float | None = None
    top1: float | None = None
    top3: float | None = None
    mrr: float | None = None
    hallucination_rate: float | None = None
    json_valid_rate: float | None = None
    avg_latency_ms: float | None = None
    error_rate: float | None = None
    error: str | None = None


@dataclass(frozen=True)
class ModelRecommendation:
    model: str
    provider: str
    overall_score: float
    successful_tasks: int
    total_tasks: int
    avg_latency_ms: float | None
    reason: str


@dataclass(frozen=True)
class BenchmarkReport:
    created_at: str
    results: list[TaskSummary]
    details: list[CaseDetail]
    recommendation: ModelRecommendation | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "created_at": self.created_at,
            "results": [asdict(result) for result in self.results],
            "details": [asdict(detail) for detail in self.details],
            "recommendation": (
                asdict(self.recommendation) if self.recommendation is not None else None
            ),
        }


def default_models() -> list[str]:
    """Return conservative defaults; paid cloud APIs are opt-in via --models."""
    return ["baseline", f"ollama:{default_eval_ollama_model()}", DEFAULT_HUGGINGFACE_MODEL]


def parse_model_spec(raw: str) -> ModelSpec:
    """Parse provider:model model specs used by the eval CLI."""
    if raw == "baseline":
        return ModelSpec(provider="baseline", model="baseline", label="baseline")
    provider, separator, model = raw.partition(":")
    if not separator or not provider or not model:
        raise ValueError(f"Invalid model spec '{raw}'. Use provider:model or baseline.")
    return ModelSpec(provider=provider, model=model, label=raw)


def llm_config_for_model(spec: ModelSpec) -> LLMConfig:
    """Build an LLMConfig for a benchmark model spec."""
    api_key: str | None = None
    base_url: str | None = None
    if spec.provider == "huggingface":
        api_key = os.environ.get("HF_TOKEN")
        base_url = "https://router.huggingface.co/v1"
    elif spec.provider == "openai":
        api_key = os.environ.get("OPENAI_API_KEY")
    elif spec.provider == "anthropic":
        api_key = os.environ.get("ANTHROPIC_API_KEY")

    return LLMConfig(
        provider=spec.provider,
        model=spec.model,
        base_url=base_url,
        api_key=api_key,
        temperature=0.1,
        max_tokens=1024,
    )


def credentials_available(spec: ModelSpec) -> bool:
    """Return whether a model can be called in the current environment."""
    if spec.provider in ("baseline", "ollama"):
        return True
    if spec.provider == "huggingface":
        return bool(os.environ.get("HF_TOKEN"))
    if spec.provider == "openai":
        return bool(os.environ.get("OPENAI_API_KEY"))
    if spec.provider == "anthropic":
        return bool(os.environ.get("ANTHROPIC_API_KEY"))
    return True


def load_json_fixture(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"Fixture must contain a list: {path}")
    return [item for item in data if isinstance(item, dict)]


def _fixture_path(name: str) -> Path:
    return Path.cwd() / "tests" / "fixtures" / name


def _model_id(config: LLMConfig) -> str:
    if config.provider == "ollama":
        return f"ollama_chat/{config.model}"
    if config.provider == "openai":
        return config.model
    return f"{config.provider}/{config.model}"


def _avg(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _skipped_summary(task: TaskName, spec: ModelSpec, reason: str) -> TaskSummary:
    return TaskSummary(
        task=task,
        model=spec.label,
        provider=spec.provider,
        status="skipped_missing_credentials",
        cases=0,
        overall_score=0.0,
        error=reason,
    )


def run_classifier_eval(
    spec: ModelSpec, fixture_path: Path | None = None
) -> tuple[TaskSummary, list[CaseDetail]]:
    """Evaluate document classification and filename quality."""
    if not spec.uses_llm:
        summary = TaskSummary(
            task="classifier",
            model=spec.label,
            provider=spec.provider,
            status="skipped_baseline_not_applicable",
            cases=0,
            overall_score=0.0,
        )
        return summary, []
    if not credentials_available(spec):
        return _skipped_summary("classifier", spec, f"Missing credentials for {spec.provider}"), []

    cases = load_json_fixture(fixture_path or _fixture_path("classifier_benchmark.json"))
    classifier = Classifier(llm_config_for_model(spec))
    latencies: list[float] = []
    scores: list[float] = []
    category_hits = 0
    hallucinations = 0
    errors = 0
    details: list[CaseDetail] = []

    for case in cases:
        started = time.perf_counter()
        try:
            result = classifier.classify(str(case["content"]))
            latencies.append((time.perf_counter() - started) * 1000)
            expected = case["expected"]
            expected_category = str(expected["category"])
            required_terms = [str(term) for term in expected.get("filename_must_include", [])]
            forbidden_terms = [str(term) for term in expected.get("filename_forbidden", [])]

            category_ok = result.category == expected_category
            filename = result.suggested_filename.casefold()
            missing_terms = [term for term in required_terms if term.casefold() not in filename]
            forbidden_hits = [term for term in forbidden_terms if term.casefold() in filename]
            filename_ok = (
                not missing_terms and not forbidden_hits and bool(result.suggested_filename)
            )
            metadata_ok = bool(result.summary.strip()) and bool(result.tags)

            category_hits += int(category_ok)
            hallucinations += int(bool(missing_terms or forbidden_hits))
            score = (
                (0.5 if category_ok else 0.0)
                + (0.35 if filename_ok else 0.0)
                + (0.15 if metadata_ok else 0.0)
            )
            scores.append(score)
            details.append(
                CaseDetail(
                    task="classifier",
                    model=spec.label,
                    case_id=str(case["id"]),
                    score=score,
                    expected=f"{expected_category} / {', '.join(required_terms)}",
                    actual=f"{result.category} / {result.suggested_filename}",
                )
            )
        except Exception as exc:
            errors += 1
            scores.append(0.0)
            details.append(
                CaseDetail(
                    task="classifier",
                    model=spec.label,
                    case_id=str(case.get("id", "unknown")),
                    score=0.0,
                    expected=str(case.get("expected", {})),
                    actual="",
                    error=str(exc),
                )
            )

    case_count = len(cases)
    summary = TaskSummary(
        task="classifier",
        model=spec.label,
        provider=spec.provider,
        status="ok",
        cases=case_count,
        overall_score=sum(scores) / case_count if case_count else 0.0,
        accuracy=category_hits / case_count if case_count else 0.0,
        hallucination_rate=hallucinations / case_count if case_count else 0.0,
        avg_latency_ms=_avg(latencies),
        error_rate=errors / case_count if case_count else 0.0,
    )
    return summary, details


def run_search_eval(
    spec: ModelSpec, fixture_path: Path | None = None
) -> tuple[TaskSummary, list[CaseDetail]]:
    """Evaluate query-assist JSON quality, rewrites, and filters."""
    if not spec.uses_llm:
        summary = TaskSummary(
            task="search",
            model=spec.label,
            provider=spec.provider,
            status="skipped_baseline_not_applicable",
            cases=0,
            overall_score=0.0,
        )
        return summary, []
    if not credentials_available(spec):
        return _skipped_summary("search", spec, f"Missing credentials for {spec.provider}"), []

    cases = load_benchmark_cases(fixture_path)
    results: list[EvaluationResult] = []
    latencies: list[float] = []
    details: list[CaseDetail] = []

    config = llm_config_for_model(spec)
    for case in cases:
        prompt = build_search_prompt(DEFAULT_CATEGORIES, case.query)
        started = time.perf_counter()
        try:
            response = completion(
                model=_model_id(config),
                messages=[{"role": "user", "content": prompt}],
                temperature=min(config.temperature, 0.2),
                max_tokens=min(config.max_tokens, 400),
                timeout=30,
                api_base=config.base_url,
                api_key=config.api_key,
            )
            elapsed_ms = (time.perf_counter() - started) * 1000
            raw = response.choices[0].message.content or ""
            result = evaluate_output(case, parse_model_output(raw), elapsed_ms=elapsed_ms)
            results.append(result)
            details.append(
                CaseDetail(
                    task="search",
                    model=spec.label,
                    case_id=case.id,
                    score=result.overall_score,
                    expected=", ".join(case.expected.rewrites),
                    actual=raw[:240],
                )
            )
            latencies.append(elapsed_ms)
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - started) * 1000
            result = EvaluationResult(
                case_id=case.id,
                json_valid=False,
                rewrites_present=False,
                filters_present=False,
                notes_present=False,
                rewrite_coverage=0.0,
                filter_coverage=0.0,
                format_score=0.0,
                overall_score=0.0,
                elapsed_ms=elapsed_ms,
                error=str(exc),
            )
            results.append(result)
            details.append(
                CaseDetail(
                    task="search",
                    model=spec.label,
                    case_id=case.id,
                    score=0.0,
                    expected=", ".join(case.expected.rewrites),
                    actual="",
                    error=str(exc),
                )
            )

    case_count = len(results)
    summary = TaskSummary(
        task="search",
        model=spec.label,
        provider=spec.provider,
        status="ok",
        cases=case_count,
        overall_score=(
            sum(result.overall_score for result in results) / case_count if case_count else 0.0
        ),
        json_valid_rate=sum(1 for result in results if result.json_valid) / case_count
        if case_count
        else 0.0,
        avg_latency_ms=_avg(latencies),
        error_rate=sum(1 for result in results if result.error) / case_count if case_count else 0.0,
    )
    return summary, details


def _populate_retrieval_engine(fixture: dict[str, Any], db_path: Path, config: LLMConfig) -> Engine:
    arkiv_config = ArkivConfig(
        llm=config,
        database=DatabaseConfig(path=db_path, store_content=True),
        classifier_retries=1,
        classifier_timeout=30,
    )
    engine = Engine(arkiv_config)
    for document in fixture.get("documents", []):
        if not isinstance(document, dict):
            continue
        engine.store.record_item(
            original_path=f"fixture://{document['id']}",
            destination=f"fixture://archive/{document['id']}.txt",
            category=str(document["category"]),
            confidence=1.0,
            summary=str(document["summary"]),
            tags=[str(tag) for tag in document.get("tags", [])],
            language=str(document.get("language", "de")),
            route_name="fixture",
            suggested_filename=str(document["title"]),
            content_text=str(document["content"]),
            status="routed",
        )
    return engine


def _rank_result(results: list[dict[str, Any]], expected_document_id: str) -> int | None:
    expected_path = f"fixture://{expected_document_id}"
    for index, item in enumerate(results, 1):
        if item.get("original_path") == expected_path:
            return index
    return None


def _score_ranking(
    results: list[dict[str, Any]], expected_document_id: str
) -> tuple[float, float, float]:
    rank = _rank_result(results, expected_document_id)
    top1 = 1.0 if rank == 1 else 0.0
    top3 = 1.0 if rank is not None and rank <= 3 else 0.0
    mrr = 1.0 / rank if rank else 0.0
    return top1, top3, mrr


def run_retrieval_eval(
    spec: ModelSpec, fixture_path: Path | None = None
) -> tuple[TaskSummary, list[CaseDetail]]:
    """Evaluate retrieval quality against a temporary fixture database."""
    if not credentials_available(spec):
        return _skipped_summary("retrieval", spec, f"Missing credentials for {spec.provider}"), []

    fixture = json.loads((fixture_path or _fixture_path("retrieval_benchmark.json")).read_text())
    if not isinstance(fixture, dict):
        raise ValueError("Retrieval benchmark fixture must contain an object")

    with tempfile.TemporaryDirectory(prefix="kurier-eval-") as temp_dir:
        config = llm_config_for_model(spec) if spec.uses_llm else LLMConfig()
        engine = _populate_retrieval_engine(fixture, Path(temp_dir) / "kurier.db", config)
        top1_scores: list[float] = []
        top3_scores: list[float] = []
        mrr_scores: list[float] = []
        latencies: list[float] = []
        details: list[CaseDetail] = []
        errors = 0

        for query_case in fixture.get("queries", []):
            if not isinstance(query_case, dict):
                continue
            started = time.perf_counter()
            try:
                results, assist = engine.search_with_assist(
                    str(query_case["query"]),
                    limit=5,
                    mode="fts",
                    memory=spec.uses_llm,
                )
                if spec.uses_llm and assist is not None and not assist.queries(""):
                    errors += 1
                latencies.append((time.perf_counter() - started) * 1000)
                expected_id = str(query_case["expected_document_id"])
                top1, top3, mrr = _score_ranking(results, expected_id)
                top1_scores.append(top1)
                top3_scores.append(top3)
                mrr_scores.append(mrr)
                rank = _rank_result(results, expected_id)
                details.append(
                    CaseDetail(
                        task="retrieval",
                        model=spec.label,
                        case_id=str(query_case["id"]),
                        score=(top1 * 0.5) + (top3 * 0.25) + (mrr * 0.25),
                        expected=expected_id,
                        actual=f"rank={rank}" if rank is not None else "not_found",
                    )
                )
            except Exception as exc:
                errors += 1
                top1_scores.append(0.0)
                top3_scores.append(0.0)
                mrr_scores.append(0.0)
                details.append(
                    CaseDetail(
                        task="retrieval",
                        model=spec.label,
                        case_id=str(query_case.get("id", "unknown")),
                        score=0.0,
                        expected=str(query_case.get("expected_document_id", "")),
                        actual="",
                        error=str(exc),
                    )
                )

    case_count = len(top1_scores)
    top1 = sum(top1_scores) / case_count if case_count else 0.0
    top3 = sum(top3_scores) / case_count if case_count else 0.0
    mrr = sum(mrr_scores) / case_count if case_count else 0.0
    summary = TaskSummary(
        task="retrieval",
        model=spec.label,
        provider=spec.provider,
        status="ok",
        cases=case_count,
        overall_score=(top1 * 0.5) + (top3 * 0.25) + (mrr * 0.25),
        top1=top1,
        top3=top3,
        mrr=mrr,
        avg_latency_ms=_avg(latencies),
        error_rate=errors / case_count if case_count else 0.0,
    )
    return summary, details


def run_benchmark(
    *,
    tasks: list[TaskName],
    model_specs: list[str],
) -> BenchmarkReport:
    """Run all requested benchmark tasks for all requested models."""
    specs = [parse_model_spec(raw) for raw in model_specs]
    results: list[TaskSummary] = []
    details: list[CaseDetail] = []
    for spec in specs:
        for task in tasks:
            if task == "classifier":
                summary, case_details = run_classifier_eval(spec)
            elif task == "search":
                summary, case_details = run_search_eval(spec)
            elif task == "retrieval":
                summary, case_details = run_retrieval_eval(spec)
            results.append(summary)
            details.extend(case_details)
    return BenchmarkReport(
        created_at=datetime.now(UTC).isoformat(),
        results=results,
        details=details,
        recommendation=recommend_model(results),
    )


def recommend_model(results: list[TaskSummary]) -> ModelRecommendation | None:
    """Pick the best usable model from benchmark results."""
    grouped: dict[str, list[TaskSummary]] = {}
    for result in results:
        if result.provider == "baseline" or result.status != "ok" or result.cases <= 0:
            continue
        grouped.setdefault(result.model, []).append(result)

    if not grouped:
        return None

    recommendations: list[ModelRecommendation] = []
    for model, model_results in grouped.items():
        score = sum(result.overall_score for result in model_results) / len(model_results)
        latencies = [
            result.avg_latency_ms for result in model_results if result.avg_latency_ms is not None
        ]
        avg_latency = sum(latencies) / len(latencies) if latencies else None
        provider = model_results[0].provider
        recommendations.append(
            ModelRecommendation(
                model=model,
                provider=provider,
                overall_score=score,
                successful_tasks=len(model_results),
                total_tasks=len(results),
                avg_latency_ms=avg_latency,
                reason=_recommendation_reason(score, avg_latency),
            )
        )

    return max(
        recommendations,
        key=lambda item: (
            item.successful_tasks,
            item.overall_score,
            -(item.avg_latency_ms or 999_999.0),
        ),
    )


def _recommendation_reason(score: float, avg_latency_ms: float | None) -> str:
    quality = "sehr gute" if score >= 0.85 else "solide" if score >= 0.65 else "noch schwache"
    if avg_latency_ms is None:
        return f"{quality} Qualität im Kurier-Test."
    if avg_latency_ms <= 2_500:
        speed = "schnell genug für den Alltag"
    elif avg_latency_ms <= 10_000:
        speed = "brauchbar, aber nicht besonders schnell"
    else:
        speed = "eher langsam"
    return f"{quality} Qualität im Kurier-Test und {speed}."


def write_report(report: BenchmarkReport, output: Path | None = None) -> Path:
    """Write a benchmark report and return the final path."""
    if output is None:
        timestamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
        output = DEFAULT_REPORT_DIR / f"{timestamp}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    return output
