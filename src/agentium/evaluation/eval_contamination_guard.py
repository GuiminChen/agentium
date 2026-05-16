"""Detection of evaluation contamination from leaked benchmark artifacts."""

from __future__ import annotations

from typing import List, Tuple

from pydantic import BaseModel, Field


class EvalContaminationError(RuntimeError):
    """Raised when ``fail_hard`` contamination checks fire on an eval transcript."""

    def __init__(self, reasons: List[str]) -> None:
        self.reasons = list(reasons)
        joined = ",".join(reasons)
        super().__init__(f"eval_contamination_blocked:{joined}")


class EvalContaminationResult(BaseModel):
    """Contamination inspection result for one transcript."""

    suspected: bool = False
    reasons: List[str] = Field(default_factory=list)


class EvalContaminationGuard:
    """Guard that flags likely benchmark leakage in eval traces."""

    _REASON_RULES: List[Tuple[str, Tuple[str, ...]]] = [
        (
            "answer_key_reference",
            ("answer key", "gold answers", "benchmark answers", "decoded answers"),
        ),
        (
            "benchmark_source_leakage",
            (
                "browsecomp",
                "browse comp",
                "swe-bench",
                "terminal-bench",
                "eval dataset dump",
                "leaderboard answers",
            ),
        ),
        (
            "suspicious_decryption_path",
            ("decrypt", "xor", "public mirror", "huggingface mirror"),
        ),
        (
            "dataset_hub_pull",
            ("huggingface.co/datasets", "datasets.load_dataset", "load_dataset("),
        ),
        (
            "unrestricted_code_fetch",
            ("git clone http", "wget https://raw.githubusercontent.com"),
        ),
    ]

    def inspect_transcript(
        self,
        task_prompt: str,
        transcript: str,
        *,
        fail_hard: bool = False,
    ) -> EvalContaminationResult:
        """Inspect transcript content for benchmark contamination clues."""

        del task_prompt  # Reserved for future task-aware checks.
        normalized = transcript.lower()
        reasons: List[str] = []
        for reason, keywords in self._REASON_RULES:
            if any(keyword in normalized for keyword in keywords):
                reasons.append(reason)
        result = EvalContaminationResult(suspected=bool(reasons), reasons=reasons)
        if fail_hard and result.suspected:
            raise EvalContaminationError(list(result.reasons))
        return result

