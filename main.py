"""
CLI runner for the DocuBot tinker activity.

Supports three modes:
1. Naive LLM generation over all docs (Phase 0)
2. Retrieval only (Phase 1)
3. RAG: retrieval plus LLM generation (Phase 2)
"""

import os

from dotenv import load_dotenv
load_dotenv()
# Personal key store, kept outside the repo so a static file server can never
# serve it. Optional: the repo-local .env still works on its own.
load_dotenv(os.path.expanduser("~/.config/docubot/env"))

from docubot import DocuBot
from llm_client import GeminiClient
from dataset import SAMPLE_QUERIES
from agent import TraceWriter, run_agentic


def try_create_llm_client():
    """
    Tries to create a GeminiClient.
    Returns (llm_client, has_llm: bool).
    """
    try:
        client = GeminiClient()
        return client, True
    except RuntimeError as exc:
        print("Warning: LLM features are disabled.")
        print(f"Reason: {exc}")
        print("You can still run retrieval only mode.\n")
        return None, False


def choose_mode(has_llm):
    """
    Asks the user which mode to run.
    Returns "1", "2", "3", or "q".
    """
    print("Choose a mode:")
    if has_llm:
        print("  1) Naive LLM over full docs (no retrieval)")
    else:
        print("  1) Naive LLM over full docs (unavailable, no GEMINI_API_KEY)")
    print("  2) Retrieval only (no LLM)")
    if has_llm:
        print("  3) RAG (retrieval + LLM)")
    else:
        print("  3) RAG (unavailable, no GEMINI_API_KEY)")
    print("  4) Agentic RAG (retrieve → answer → verify → retry)")
    print("  q) Quit")

    choice = input("Enter choice: ").strip().lower()
    return choice


def get_query_or_use_samples():
    """
    Ask the user if they want to run all sample queries or a single custom query.

    Returns:
        queries: list of strings
        label: short description of the source of queries
    """
    print("\nPress Enter to run built in sample queries.")
    custom = input("Or type a single custom query: ").strip()

    if custom:
        return [custom], "custom query"
    else:
        return SAMPLE_QUERIES, "sample queries"


def run_naive_llm_mode(bot, has_llm):
    """
    Mode 1:
    Naive LLM generation over the full docs corpus.
    """
    if not has_llm or bot.llm_client is None:
        print("\nNaive LLM mode is not available (no GEMINI_API_KEY).\n")
        return

    queries, label = get_query_or_use_samples()
    print(f"\nRunning naive LLM mode on {label}...\n")

    all_text = bot.full_corpus_text()

    for query in queries:
        print("=" * 60)
        print(f"Question: {query}\n")
        answer = bot.llm_client.naive_answer_over_full_docs(query, all_text)
        print("Answer:")
        print(answer)
        print()


def run_retrieval_only_mode(bot):
    """
    Mode 2:
    Retrieval only answers. No LLM involved.
    """
    queries, label = get_query_or_use_samples()
    print(f"\nRunning retrieval only mode on {label}...\n")

    for query in queries:
        print("=" * 60)
        print(f"Question: {query}\n")
        answer = bot.answer_retrieval_only(query)
        print("Retrieved snippets:")
        print(answer)
        print()


def run_rag_mode(bot, has_llm):
    """
    Mode 3:
    Retrieval plus LLM generation.
    """
    if not has_llm or bot.llm_client is None:
        print("\nRAG mode is not available (no GEMINI_API_KEY).\n")
        return

    queries, label = get_query_or_use_samples()
    print(f"\nRunning RAG mode on {label}...\n")

    for query in queries:
        print("=" * 60)
        print(f"Question: {query}\n")
        answer = bot.answer_rag(query)
        print("Answer:")
        print(answer)
        print()


def run_agentic_mode(bot, has_llm):
    """
    Mode 4:
    Agentic RAG. Retrieves, answers, verifies the answer against the snippets,
    and retries with an expanded query when the answer is not grounded.
    Writes a JSONL trace to assets/agentic_trace.jsonl for the evidence trail.
    """
    queries, label = get_query_or_use_samples()
    print(f"\nRunning agentic RAG mode on {label}...\n")

    trace = TraceWriter("assets/agentic_trace.jsonl")
    note = "" if has_llm else " (retrieval only — no GEMINI_API_KEY, so no generated answer)"

    for query in queries:
        print("=" * 60)
        print(f"Question: {query}")
        result = run_agentic(bot, bot.llm_client, query, trace=trace)
        print(f"Status: {result.status} | attempts: {len(result.attempts)}"
              f"{' (retried)' if result.retried else ''}{note}")
        for attempt in result.attempts:
            print(f"  attempt {attempt.index}: query={attempt.query!r} "
                  f"retrieved={attempt.retrieved} grounded={attempt.grounded} "
                  f"ratio={attempt.grounding_ratio:.2f} reason={attempt.reason}")
        print("\nAnswer:")
        print(result.answer)
        print()


def main():
    print("DocuBot Tinker Activity")
    print("=======================\n")

    llm_client, has_llm = try_create_llm_client()
    bot = DocuBot(llm_client=llm_client)

    while True:
        choice = choose_mode(has_llm)

        if choice == "q":
            print("\nGoodbye.")
            break
        elif choice == "1":
            run_naive_llm_mode(bot, has_llm)
        elif choice == "2":
            run_retrieval_only_mode(bot)
        elif choice == "3":
            run_rag_mode(bot, has_llm)
        elif choice == "4":
            run_agentic_mode(bot, has_llm)
        else:
            print("\nUnknown choice. Please pick 1, 2, 3, 4, or q.\n")


if __name__ == "__main__":
    main()
