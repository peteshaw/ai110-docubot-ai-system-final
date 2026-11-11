"""
Offline tests for the agentic loop. No API key, no network, no LLM.

Run: python3 -m unittest discover -s tests -v
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import (  # noqa: E402
    MAX_ATTEMPTS,
    REFUSAL,
    TraceWriter,
    content_words,
    expand_query,
    is_refusal,
    run_agentic,
    should_retry,
    verify_answer,
)
from docubot import DocuBot  # noqa: E402

DOCS = Path(__file__).resolve().parents[1] / "docs"


class FakeLLM:
    """Returns whatever it is told to, so verification can be tested honestly."""

    def __init__(self, answer):
        self.answer = answer
        self.calls = 0

    def answer_from_snippets(self, query, snippets):
        self.calls += 1
        return self.answer


class TestContentWords(unittest.TestCase):
    def test_strips_stopwords_and_punctuation(self):
        self.assertEqual(content_words("How do I authenticate?"), {"authenticate"})
        self.assertIn("token", content_words("Where is the token, exactly?"))
        self.assertNotIn("the", content_words("the the the"))

    def test_folds_plurals(self):
        self.assertEqual(content_words("tokens"), content_words("token"))


class TestVerifier(unittest.TestCase):
    def test_refusal_is_grounded(self):
        grounded, ratio, reason = verify_answer(REFUSAL, [])
        self.assertTrue(grounded)
        self.assertEqual(ratio, 0.0)
        self.assertEqual(reason, "refusal")

    def test_grounded_answer_passes(self):
        snippets = ["AUTH.md: authentication uses a bearer token issued at login"]
        answer = "Authentication uses a bearer token issued at login."
        grounded, ratio, reason = verify_answer(answer, snippets)
        self.assertTrue(grounded)
        self.assertGreaterEqual(ratio, 0.6)
        self.assertEqual(reason, "grounded")

    def test_hallucination_is_caught(self):
        snippets = ["DATABASE.md: the users table stores id, email and created_at"]
        answer = "The payment gateway retries failed charges every fifteen minutes."
        grounded, ratio, reason = verify_answer(answer, snippets)
        self.assertFalse(grounded)
        self.assertEqual(reason, "ungrounded")

    def test_empty_answer_fails(self):
        grounded, _, reason = verify_answer("   ", ["anything"])
        self.assertFalse(grounded)
        self.assertEqual(reason, "empty_answer")


class TestExpandQuery(unittest.TestCase):
    def setUp(self):
        self.bot = DocuBot(docs_folder=str(DOCS))

    def test_authenticate_gains_second_term(self):
        from agent import Attempt

        expanded = expand_query("How do I authenticate?", Attempt(index=0, query="unused"), self.bot)
        terms = set(self.bot.tokenize(expanded))
        self.assertGreaterEqual(len(terms), 2)
        self.assertTrue({"auth", "token", "login"} & terms)


class TestRetryPolicy(unittest.TestCase):
    def test_retries_when_ungrounded(self):
        from agent import Attempt

        self.assertTrue(should_retry(Attempt(index=0, query="q", grounded=False, reason="ungrounded")))
        self.assertFalse(
            should_retry(Attempt(index=MAX_ATTEMPTS - 1, query="q", grounded=False, reason="ungrounded"))
        )
        self.assertFalse(should_retry(Attempt(index=0, query="q", grounded=True, reason="grounded")))


class TestAgenticLoop(unittest.TestCase):
    def setUp(self):
        self.bot = DocuBot(docs_folder=str(DOCS))

    def test_retrieval_only_mode_answers(self):
        result = run_agentic(self.bot, None, "Where is the auth token generated?")
        self.assertEqual(result.status, "answered")
        self.assertIn("AUTH.md", ",".join(result.attempts[0].retrieved))

    def test_offtopic_query_refuses(self):
        result = run_agentic(self.bot, None, "What is the capital of France?")
        self.assertEqual(result.status, "refused")
        self.assertTrue(is_refusal(result.answer))

    def test_hallucination_triggers_retry_then_caveat(self):
        llm = FakeLLM("Bitcoin mining uses specialised ASIC hardware in Iceland.")
        result = run_agentic(self.bot, llm, "Where is the auth token generated?")
        self.assertGreaterEqual(len(result.attempts), 2)
        self.assertTrue(result.retried)
        self.assertEqual(result.status, "answered_with_caveat")
        self.assertEqual(llm.calls, len(result.attempts))

    def test_single_term_query_is_rescued_by_expansion(self):
        """The documented failure: "How do I authenticate?" used to refuse."""
        result = run_agentic(self.bot, None, "How do I authenticate?")
        self.assertEqual(result.status, "answered")
        self.assertTrue(result.retried)
        self.assertIn("AUTH.md", result.attempts[-1].retrieved)
        self.assertGreaterEqual(result.attempts[-1].grounding_ratio, 0.6)

    def test_trace_is_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            trace = TraceWriter(Path(tmp) / "trace.jsonl")
            run_agentic(self.bot, None, "How do I connect to the database?", trace=trace)
            lines = trace.path.read_text(encoding="utf-8").strip().splitlines()
            self.assertGreaterEqual(len(lines), 2)
            self.assertIn('"event": "result"', lines[-1])


if __name__ == "__main__":
    unittest.main()
