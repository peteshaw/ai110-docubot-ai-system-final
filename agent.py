"""
Agentic extension of DocuBot: verify, expand, retry.

The Module 4 tinker answered in one shot. Its model card documented the cost of
that: an unanswerable-looking query ("How do I authenticate?") was refused even
though the docs cover it, because only one query term survived the tokenizer and
the guardrail requires two.

This module wraps the existing retriever in a bounded loop:

    retrieve -> answer -> verify -> (expand query, retry) -> answer or refuse

Deliberate constraints:
- No new dependencies. The verifier is deterministic term overlap, not a second
  LLM call, so it is cheap, offline-testable and explainable.
- Every attempt is recorded, so the README can show reproducible evidence.
- The loop is bounded. Unbounded self-correction is how agents spiral: two
  retries, then an honest refusal.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# Total attempts (first try + retries) before giving up.
MAX_ATTEMPTS = 3

# Fraction of an answer's content words that must appear in the retrieved
# snippets for the answer to count as grounded.
MIN_GROUNDING_RATIO = 0.60

REFUSAL = "I do not know based on these docs."

# Words a verifier should ignore when measuring grounding.
VERIFIER_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being", "to",
    "of", "and", "or", "in", "on", "at", "for", "with", "by", "as", "it",
    "this", "that", "these", "those", "you", "your", "yours", "we", "our",
    "can", "could", "should", "would", "will", "may", "might", "must", "do",
    "does", "did", "how", "what", "which", "who", "when", "where", "why",
    "if", "not", "no", "yes", "from", "into", "using", "use", "used", "set",
    "there", "their", "them", "they", "i", "me", "my", "s", "t",
}

# Retrieval-quality vocabulary. The retriever is lexical, so a query phrased
# with one word ("authenticate") cannot clear MIN_MATCHED_TERMS=2 even though
# the docs are full of "auth", "token" and "login". This table is the cheapest
# honest fix: it widens the query, not the guardrail.
SYNONYMS = {
    "authenticate": ["auth", "token", "login"],
    "authentication": ["auth", "token", "login"],
    "auth": ["token", "login"],
    "login": ["auth", "token"],
    "sign": ["auth", "login"],
    "token": ["auth", "refresh"],
    "refresh": ["token", "expire"],
    "database": ["table", "query"],
    "db": ["database", "table"],
    "users": ["user", "table"],
    "user": ["users", "account"],
    "endpoint": ["route", "api"],
    "route": ["endpoint", "api"],
    "project": ["projects", "route"],
    "config": ["environment", "variables"],
    "setup": ["install", "environment"],
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

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "query": self.query,
            "retrieved": self.retrieved,
            "answer": self.answer,
            "grounded": self.grounded,
            "grounding_ratio": round(self.grounding_ratio, 3),
            "reason": self.reason,
        }


@dataclass
class AgentResult:
    """Final outcome plus the trail of attempts that produced it."""

    query: str
    answer: str
    status: str  # "answered" | "answered_with_caveat" | "refused"
    attempts: list[Attempt] = field(default_factory=list)

    @property
    def retried(self) -> bool:
        return len(self.attempts) > 1


class TraceWriter:
    """Append-only JSONL trace. One line per attempt, flushed immediately.

    Writing before the next attempt matters: if the model call hangs or the
    process dies, the evidence up to that point still exists on disk.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event: dict[str, Any]) -> None:
        payload = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **event}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def content_words(text: str) -> set[str]:
    """Lowercase alphabetic tokens with verifier stopwords removed."""
    words = set()
    for raw in "".join(ch if ch.isalnum() else " " for ch in text.lower()).split():
        if len(raw) < 2 or raw in VERIFIER_STOPWORDS:
            continue
        # Light plural folding so "tokens" and "token" compare equal.
        if len(raw) > 4 and raw.endswith("s"):
            raw = raw[:-1]
        words.add(raw)
    return words


def is_refusal(answer: str) -> bool:
    """True when the answer is the fixed refusal string."""
    return answer.strip().rstrip(".").lower() == REFUSAL.rstrip(".").lower()


def verify_answer(answer: str, snippets: list[str]) -> tuple[bool, float, str]:
    """Check that an answer is supported by the snippets it was given.

    Returns (grounded, ratio, reason).

    A refusal counts as grounded: with no evidence, refusing is correct.
    """
    if is_refusal(answer):
        return True, 0.0, "refusal"

    if not answer.strip():
        return False, 0.0, "empty_answer"

    answer_words = content_words(answer)
    if not answer_words:
        return False, 0.0, "no_content_words"

    snippet_words = content_words(" ".join(snippets))
    overlap = answer_words & snippet_words
    ratio = len(overlap) / len(answer_words)

    if not overlap:
        return False, ratio, "ungrounded"

    if ratio < MIN_GROUNDING_RATIO:
        return False, ratio, "weak_grounding"

    return True, ratio, "grounded"


def expand_query(query: str, attempt: Attempt, bot: Any) -> str:
    """Widen an under-supported query using the synonym table.

    Deterministic and inspectable: for every token in the query we add its
    synonyms that are not already present, capped at two extra terms so the
    query does not drift off topic.
    """
    # The retriever's tokenizer stems aggressively ("authenticate" -> "authenticat"),
    # so consult both its stems and the clean content words when looking up
    # synonyms. Missing this was a real bug caught by the test suite.
    tokens = [tok for tok in bot.tokenize(query) if tok]
    lookup = list(dict.fromkeys(tokens + sorted(content_words(query))))
    added: list[str] = []

    for token in lookup:
        for synonym in SYNONYMS.get(token, []):
            if synonym in tokens or synonym in added:
                continue
            added.append(synonym)
            if len(added) >= 2:
                break
        if len(added) >= 2:
            break

    if not added:
        # Nothing to widen with: try the filenames the retriever did surface.
        for name in attempt.retrieved:
            stem = name.rsplit(".", 1)[0].lower().replace("_", " ")
            for part in stem.split():
                if part not in tokens and part not in added:
                    added.append(part)
                    break
            if added:
                break

    if not added:
        return query
    return f"{query} {' '.join(added)}"


def should_retry(attempt: Attempt, max_attempts: int = MAX_ATTEMPTS) -> bool:
    """True when another pass could plausibly change the outcome."""
    if attempt.grounded:
        return False
    if attempt.index + 1 >= max_attempts:
        return False
    # An empty answer is worth one more try; a refusal with no evidence is not.
    return attempt.reason in {"ungrounded", "weak_grounding", "empty_answer", "no_results"}


def run_agentic(
    bot: Any,
    llm_client: Optional[Any],
    query: str,
    max_attempts: int = MAX_ATTEMPTS,
    trace: Optional[TraceWriter] = None,
    top_k: int = 3,
) -> AgentResult:
    """Run the bounded verify-and-retry loop and return the final result.

    `bot` is a DocuBot instance. `llm_client` may be None, in which case the
    loop still exercises retrieval, verification and refusal, so the path is
    demonstrable without an API key.
    """
    result = AgentResult(query=query, answer="", status="refused")
    current_query = query

    for index in range(max_attempts):
        rows = bot.retrieve_with_scores(current_query, top_k=top_k)
        filenames = [row["filename"] for row in rows]
        snippets = [row["text"] for row in rows]

        if llm_client is None:
            if snippets:
                answer = "Retrieved from " + ", ".join(sorted(set(filenames))) + ":\n" + "\n\n".join(snippets)
            else:
                answer = REFUSAL
        elif not snippets:
            answer = REFUSAL
        else:
            answer = llm_client.answer_from_snippets(
                current_query, [(row["filename"], row["text"]) for row in rows]
            )

        grounded, ratio, reason = verify_answer(answer, snippets)
        if not snippets:
            reason = "no_results"
            grounded = False
        # A refusal is an honest *statement* but not a terminal success: an
        # empty retrieval is exactly the case query expansion exists to fix.
        refused = is_refusal(answer)
        if refused:
            grounded = False

        attempt = Attempt(
            index=index,
            query=current_query,
            retrieved=filenames,
            answer=answer,
            grounded=grounded,
            grounding_ratio=ratio,
            reason=reason,
        )
        result.attempts.append(attempt)

        if trace is not None:
            trace.write({"event": "attempt", "original_query": query, **attempt.as_dict()})

        if grounded and not refused:
            result.answer = answer
            result.status = "answered"
            break

        if not should_retry(attempt, max_attempts):
            result.answer = answer
            result.status = "refused" if refused else "answered_with_caveat"
            break

        widened = expand_query(current_query, attempt, bot)
        if widened == current_query:
            # Nothing to widen with: more attempts would be identical.
            result.answer = answer
            result.status = "refused" if refused else "answered_with_caveat"
            break
        current_query = widened

    if trace is not None:
        trace.write(
            {
                "event": "result",
                "query": query,
                "status": result.status,
                "attempts": len(result.attempts),
                "answer": result.answer,
            }
        )

    return result
