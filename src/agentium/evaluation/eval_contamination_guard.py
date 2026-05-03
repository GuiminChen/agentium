"""Detection of evaluation contamination from leaked benchmark artifacts."""

from __future__ import annotations

from typing import List, Tuple

from pydantic import BaseModel, Field


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
            ("browsecomp", "swe-bench", "terminal-bench", "eval dataset dump"),
        ),
        (
            "suspicious_decryption_path",
            ("decrypt", "xor", "public mirror", "huggingface mirror"),
        ),
    ]

    def inspect_transcript(
        self, task_prompt: str, transcript: str
    ) -> EvalContaminationResult:
        """Inspect transcript content for benchmark contamination clues."""

        del task_prompt  # Reserved for future task-aware checks.
        normalized = transcript.lower()
        reasons: List[str] = []
        for reason, keywords in self._REASON_RULES:
            if any(keyword in normalized for keyword in keywords):
                reasons.append(reason)
        return EvalContaminationResult(suspected=bool(reasons), reasons=reasons)

