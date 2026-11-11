"""
Core DocuBot class responsible for:
- Loading documents from the docs/ folder
- Building a simple retrieval index (Phase 1)
- Retrieving relevant snippets (Phase 1)
- Supporting retrieval only answers
- Supporting RAG answers when paired with Gemini (Phase 2)

Design in one paragraph (so every line below can be explained):
documents are cut into small markdown sections ("chunks"); each chunk is
tokenized (lowercase, punctuation stripped, stopwords dropped, crude plural
folding); an inverted index records which chunks contain which token and how
often; a query is scored against a chunk with tf-idf; and a chunk is only
returned if it covers enough of the query's weighted terms, otherwise DocuBot
refuses instead of guessing.
"""

import os
import glob
import math
import re
from collections import Counter

# Words that carry no retrieval signal. Kept deliberately short and obvious.
STOPWORDS = {
    "a", "about", "after", "all", "also", "am", "an", "and", "any", "are", "as",
    "at", "be", "been", "before", "being", "between", "both", "but", "by", "can",
    "could", "did", "do", "does", "doing", "done", "during", "each", "few", "for",
    "from", "further", "had", "has", "have", "having", "he", "her", "here", "hers",
    "him", "his", "how", "i", "if", "in", "into", "is", "it", "its", "just", "me",
    "more", "most", "my", "no", "nor", "not", "of", "off", "on", "once", "only",
    "or", "other", "our", "out", "over", "own", "same", "she", "should", "so",
    "some", "such", "than", "that", "the", "their", "them", "then", "there",
    "these", "they", "this", "those", "through", "to", "too", "under", "until",
    "up", "very", "was", "we", "were", "what", "when", "where", "which", "while",
    "who", "whom", "why", "will", "with", "would", "you", "your",
}

# A chunk larger than this gets split further at paragraph boundaries.
MAX_CHUNK_CHARS = 700

# Guardrail: a chunk must explain at least this fraction of the query's
# weighted terms before DocuBot will answer. Tuned on the evaluation set.
MIN_COVERAGE = 0.35

# Guardrail: ...and it must match at least this many distinct query words, so
# one coincidental match ("docs" in a heading) cannot carry an off-topic answer.
MIN_MATCHED_TERMS = 2


class DocuBot:
    def __init__(self, docs_folder="docs", llm_client=None):
        """
        docs_folder: directory containing project documentation files
        llm_client: optional Gemini client for LLM based answers
        """
        self.docs_folder = docs_folder
        self.llm_client = llm_client

        # Load documents into memory
        self.documents = self.load_documents()  # List of (filename, text)

        # Split documents into small, retrievable chunks
        self.chunks = self.build_chunks(self.documents)

        # Build a retrieval index over chunks (Phase 1)
        self.index = self.build_index(self.chunks)
        self.n_chunks = len(self.chunks)

        # Filled in by retrieve(), useful for explaining a result
        self.last_results = []  # list of dicts: filename, text, score, coverage
        self.last_refused = False

    # -----------------------------------------------------------
    # Document Loading
    # -----------------------------------------------------------

    def load_documents(self):
        """
        Loads all .md and .txt files inside docs_folder.
        Returns a list of tuples: (filename, text)
        """
        docs = []
        pattern = os.path.join(self.docs_folder, "*.*")
        for path in sorted(glob.glob(pattern)):
            if path.endswith(".md") or path.endswith(".txt"):
                with open(path, "r", encoding="utf8") as f:
                    text = f.read()
                filename = os.path.basename(path)
                docs.append((filename, text))
        return docs

    # -----------------------------------------------------------
    # Chunking (Phase 3 precision work)
    # -----------------------------------------------------------

    @staticmethod
    def _clean_heading(line):
        """'## Token Generation' -> 'Token Generation'"""
        return line.lstrip("#").strip()

    def chunk_document(self, filename, text, max_chars=MAX_CHUNK_CHARS):
        """
        Cut one document into chunks, one per markdown section.

        Rule: a section starts at a line beginning with '#'. Each chunk is
        prefixed with its full heading path ("Database Guide > Tables > users")
        so a small section still carries the words that say what it belongs to
        — otherwise the users table chunk never contains the word "table".
        Sections longer than max_chars are split again at blank lines
        (paragraph boundaries), keeping the same heading path.
        Return: list of (filename, chunk_text).
        """
        sections = [s for s in re.split(r"(?m)^(?=#{1,6}\s)", text) if s.strip()]
        chunks = []
        stack = []  # [(level, title), ...] current heading ancestry

        for section in sections:
            lines = section.splitlines()
            match = re.match(r"^(#{1,6})\s+(.*)$", lines[0])
            if match:
                level = len(match.group(1))
                title = match.group(2).strip()
                while stack and stack[-1][0] >= level:
                    stack.pop()
                stack.append((level, title))
                body = "\n".join(lines[1:]).strip()
            else:
                body = section.strip()  # preamble before the first heading

            heading_path = " > ".join(title for _, title in stack)
            body = body.strip()
            if not body:
                continue

            if len(body) <= max_chars:
                chunks.append((filename, f"{heading_path}\n\n{body}"))
                continue

            buffer = ""
            for para in re.split(r"\n\s*\n", body):
                para = para.strip()
                if not para:
                    continue
                # +2 accounts for the "\n\n" we insert between paragraphs
                if buffer and len(buffer) + len(para) + 2 > max_chars:
                    chunks.append((filename, f"{heading_path}\n\n{buffer}"))
                    buffer = para
                else:
                    buffer = f"{buffer}\n\n{para}" if buffer else para
            if buffer:
                chunks.append((filename, f"{heading_path}\n\n{buffer}"))

        return chunks

    def build_chunks(self, documents):
        """Apply chunk_document to every loaded document."""
        chunks = []
        for filename, text in documents:
            chunks.extend(self.chunk_document(filename, text))
        return chunks

    # -----------------------------------------------------------
    # Index Construction (Phase 1)
    # -----------------------------------------------------------

    @staticmethod
    def normalize_token(token):
        """
        Crude stemming so related word forms match each other:
          users/users -> user, generated/generate -> generat, stores/stored -> stor
        Steps: drop plural -s, then -ing/-ed/-ion, then a trailing -e. Applied to
        both documents and queries, so it only has to be consistent, not perfect.
        """
        t = token
        if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
            t = t[:-1]
        for suffix in ("ing", "ed", "ion"):
            if len(t) - len(suffix) >= 3 and t.endswith(suffix):
                t = t[: -len(suffix)]
                break
        if len(t) > 4 and t.endswith("e"):
            t = t[:-1]
        return t

    def tokenize(self, text):
        """
        Lowercase, split on non-alphanumerics, drop stopwords, stem.

        snake_case identifiers are indexed twice: whole ("user_id") and as
        parts ("user", "id"), because developers ask about "the user" while
        the docs only ever say "user_id".
        """
        raw = re.findall(r"[a-z0-9_]+", text.lower())
        tokens = []
        for token in raw:
            if token in STOPWORDS or len(token) < 2:
                continue
            tokens.append(self.normalize_token(token))
            if "_" in token:
                for part in token.split("_"):
                    if len(part) > 1 and part not in STOPWORDS:
                        tokens.append(self.normalize_token(part))
        return tokens

    def build_index(self, documents):
        """
        Build an inverted index over chunks.

        Structure:
            { "token": { chunk_id: term_frequency, ... }, ... }

        chunk_id is a position in self.chunks, so the index stores only small
        integers rather than copies of the text. The number of keys in the
        inner dict is the document frequency used by score_document().
        """
        index = {}
        for chunk_id, (_, text) in enumerate(documents):
            for token, count in Counter(self.tokenize(text)).items():
                index.setdefault(token, {})[chunk_id] = count
        return index

    # -----------------------------------------------------------
    # Scoring and Retrieval (Phase 1)
    # -----------------------------------------------------------

    def idf(self, token):
        """
        Inverse document frequency: rare tokens weigh more than common ones.
        A token that appears in no chunk gets the highest weight, which is why
        an off-topic question cannot score well (see _query_weight).
        """
        df = len(self.index.get(token, {}))
        return math.log((self.n_chunks + 1) / (df + 1)) + 1.0

    def _score_chunk(self, query_terms, text):
        """
        Score one chunk and count how many distinct query terms it matched.
        Returns (score, matched_term_count).
        """
        counts = Counter(self.tokenize(text))
        # Heading words count twice: the heading is the section's own label, so
        # "GET /api/projects/<project_id>" should outrank a passing mention.
        heading = text.splitlines()[0] if text else ""
        heading_counts = Counter(self.tokenize(heading))

        score = 0.0
        matched = 0
        for term in query_terms:
            tf = counts.get(term, 0) + heading_counts.get(term, 0)
            if tf:
                matched += 1
                score += (1.0 + math.log(tf)) * self.idf(term)
        return score, matched

    def score_document(self, query, text):
        """
        tf-idf relevance score of one chunk against the query.

        score = sum over query terms present in the chunk of
                    (1 + log(term_frequency_in_chunk)) * idf(term)

        The log damps repetition: ten mentions are worth more than one, but
        not ten times more. Each query term contributes at most once.
        """
        query_terms = set(self.tokenize(query))
        if not query_terms:
            return 0.0
        return self._score_chunk(query_terms, text)[0]

    def retrieve_with_scores(self, query, top_k=3, min_coverage=MIN_COVERAGE,
                             min_matched=MIN_MATCHED_TERMS):
        """
        Score every chunk, sort by score, keep the top_k that clear the
        coverage guardrail.

        coverage = score / _query_weight(query), clamped to [0, 1]
        i.e. "how much of what the user asked about does this chunk explain?"
        """
        terms = set(self.tokenize(query))
        denominator = sum(self.idf(t) for t in terms)
        if denominator == 0:
            self.last_results = []
            self.last_refused = True
            return []

        scored = []
        for filename, text in self.chunks:
            raw, matched = self._score_chunk(terms, text)
            if raw <= 0 or matched < min_matched:
                continue
            coverage = min(1.0, raw / denominator)
            if coverage >= min_coverage:
                scored.append({
                    "filename": filename,
                    "text": text,
                    "score": raw,
                    "coverage": coverage,
                    "matched": matched,
                })

        scored.sort(key=lambda r: (r["score"], r["coverage"]), reverse=True)

        # Avoid returning the same document section twice
        results, seen = [], set()
        for row in scored:
            key = (row["filename"], row["text"][:80])
            if key in seen:
                continue
            seen.add(key)
            results.append(row)
            if len(results) >= top_k:
                break

        self.last_results = results
        self.last_refused = not results
        return results

    def retrieve(self, query, top_k=3):
        """
        Use the index and scoring function to select top_k relevant snippets.

        Return a list of (filename, text) sorted by score descending.
        Returns [] when nothing clears the guardrail, which both answer modes
        turn into an explicit "I do not know".
        """
        rows = self.retrieve_with_scores(query, top_k=top_k)
        return [(r["filename"], r["text"]) for r in rows]

    # -----------------------------------------------------------
    # Answering Modes
    # -----------------------------------------------------------

    def answer_retrieval_only(self, query, top_k=3):
        """
        Phase 1 retrieval only mode.
        Returns raw snippets and filenames with no LLM involved.
        """
        rows = self.retrieve_with_scores(query, top_k=top_k)

        if not rows:
            return "I do not know based on these docs."

        formatted = []
        for row in rows:
            header = (
                f"[{row['filename']}] "
                f"(score {row['score']:.2f}, coverage {row['coverage']:.0%})"
            )
            formatted.append(f"{header}\n{row['text']}\n")

        return "\n---\n".join(formatted)

    def answer_rag(self, query, top_k=3):
        """
        Phase 2 RAG mode.
        Uses student retrieval to select snippets, then asks Gemini
        to generate an answer using only those snippets.
        """
        if self.llm_client is None:
            raise RuntimeError(
                "RAG mode requires an LLM client. Provide a GeminiClient instance."
            )

        snippets = self.retrieve(query, top_k=top_k)

        if not snippets:
            return "I do not know based on these docs."

        return self.llm_client.answer_from_snippets(query, snippets)

    # -----------------------------------------------------------
    # Bonus Helper: concatenated docs for naive generation mode
    # -----------------------------------------------------------

    def full_corpus_text(self):
        """
        Returns all documents concatenated into a single string.
        This is used in Phase 0 for naive 'generation only' baselines.
        """
        return "\n\n".join(text for _, text in self.documents)
