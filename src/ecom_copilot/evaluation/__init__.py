"""评测包。"""

from .ragas_like import (  # noqa: F401
    answer_relevancy,
    citation_coverage,
    context_precision,
    context_recall,
    evaluate_sample,
    faithfulness,
    hit_at_k,
)
from .runner import (  # noqa: F401
    ensure_corpus_indexed,
    load_golden,
    report_markdown,
    run_evaluation,
    save_report,
)

__all__ = [
    "answer_relevancy", "citation_coverage", "context_precision", "context_recall",
    "evaluate_sample", "faithfulness", "hit_at_k",
    "ensure_corpus_indexed", "load_golden", "report_markdown", "run_evaluation",
    "save_report",
]
