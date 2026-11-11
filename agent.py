"""
Agentic extension of DocuBot: verify, expand, retry.

The Module 4 tinker answered in one shot. Its model card documented the cost of
that: an unanswerable-looking query ("How do I authenticate?") was refused even
though the docs cover it, because only one query term survived the tokenizer and
the guardrail requires two.

This module adds a small agentic loop around the existing retriever:

    retrieve -> answer -> verify -> (expand query, retry) -> answer or refuse

Design constraints, deliberately:
- No new dependencies. The verifier is deterministic string/term overlap, not a
  second LLM call, so it can be unit tested offline and costs nothing to run.
- Every step writes to a trace, because the README has to show reproducible
  evidence rather than screenshots.
- The loop is bounded. Unbounded self-correction is how agents spiral; two
  retries is the budget and the third failure is a refusal.

STATUS: scaffolding only (Session 2). `run_agentic` is intentionally not
implemented yet; Session 3 wires it end-to-end and turns the assertions in
`tests/` green.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# How many total attempts (first try + retries) before giving up.
MAX_ATTEMPTS = 3

# Fraction of the answer's content words that must appear in the retrieved
# snippets for the answer to count as grounded.
MIN_GROUNDING_RATIO = 0.60

# Words a verifier should ignore when measuring grounding.
VERIFIER_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "to", "of",
    "and", "or", "in", "on", "for", "with", "by", "as", "at", "it", "this",
    "that", "these", "those", "you", "your", "can", "do", "does", "how", "what",
    "which", "if", "not", "no", "yes", "from", "into", "using", "use", "will",
}


@dataclass
class Attempt:
    """One pass through retrieve -> answer -> verify."""

    index: int
    query: str
    retrieved: list[str] = field(default_factory=list)
    answer: str = ""
    grounded: bool = False
    grounding_ratio: float = 0.0
    reason: str = ""


@dataclass
class AgentResult:
    """Final outcome plus the trail of attempts that produced it."""

    query: str
    answer: str
    status: str  # "answered" | "answered_with_caveat" | "refused"
    attempts: list[Attempt] = field(default_factory=list)


def content_words(text: str) -> set[str]:
    """Lowercase alphabetic tokens with verifier stopwords removed.

    Deliberately crude: the point is a cheap, explainable grounding signal, not
    a parser. Kept separate so it can be tested on its own.
    """
    raise NotImplementedError("Session 3")


def verify_answer(answer: str, snippets: list[str]) -> tuple[bool, float, str]:
    """Check that an answer is supported by the snippets it was given.

    Returns (grounded, ratio, human_readable_reason).

    A refusal counts as grounded when the retriever genuinely found nothing:
    refusing with no evidence is correct behaviour, not a failure.
    """
    raise NotImplementedError("Session 3")


def expand_query(query: str, attempt: Attempt, bot: Any) -> str:
    """Widen a query that came back under-supported.

    Strategy is retrieval-driven rather than LLM-driven: look at the tokens the
    retriever did surface and add the highest-signal missing domain term. That
    keeps the retry deterministic and cheap, and it directly targets the
    documented failure where "authenticate" alone could not clear the two-term
    guardrail.
    """
    raise NotImplementedError("Session 3")


def should_retry(attempt: Attempt, max_attempts: int = MAX_ATTEMPTS) -> bool:
    """True when another pass could plausibly change the outcome."""
    raise NotImplementedError("Session 3")


def run_agentic(
    bot: Any,
    llm_client: Optional[Any],
    query: str,
    max_attempts: int = MAX_ATTEMPTS,
    trace: Optional[Any] = None,
) -> AgentResult:
    """Run the bounded verify-and-retry loop and return the final result.

    `bot` is a DocuBot instance (retrieval only); `llm_client` may be None, in
    which case the loop still exercises retrieval, verification and refusal so
    the path can be demonstrated without an API key.
    """
    raise NotImplementedError("Session 3")
