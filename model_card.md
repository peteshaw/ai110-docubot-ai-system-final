# DocuBot Model Card

AI 110 · Module 4 Tinker · filled in after implementing retrieval and running the three modes.

---

## 1. System Overview

**What is DocuBot trying to do?**
Answer developer questions about a codebase using the project's own documentation instead of the
model's memory. It exists to make the difference between "fluent" and "grounded" visible, by running
the same question through naive generation, retrieval only, and RAG.

**What inputs does DocuBot take?**
A user question (typed at the CLI or taken from `dataset.py`), the markdown files in `docs/`,
and — for modes 1 and 3 — a `GEMINI_API_KEY` from the environment.

**What outputs does DocuBot produce?**
Mode 1: a paragraph of prose with no citations. Mode 2: the top matching doc sections with their
filename and score, or an explicit refusal. Mode 3: a short answer that cites the files it used,
or the exact refusal line "I do not know based on the docs I have."

---

## 2. Retrieval Design

**How does your retrieval system work?**

- **Chunking.** Each document is cut at markdown headings, so one chunk = one section. Every chunk is
  prefixed with its full heading path (`Database Guide > Tables > users`) so a short section still
  carries the words that identify it — without this, the users-table chunk never contains the word
  "table" and loses to unrelated sections. Sections over 700 characters are split again at blank
  lines (paragraph boundaries), keeping the same heading path.
- **Index (`build_index`).** A plain inverted index: `{token: {chunk_id: term_frequency}}`.
  Tokenizing = lowercase, split on non-alphanumerics, drop stopwords, light stemming
  (`users→user`, `generated→generat`, `connection→connect`), and index snake_case identifiers both
  whole and in parts, because developers ask about "the user" while the docs only say `user_id`.
  The number of chunks a token appears in is its document frequency, used for IDF.
- **Scoring (`score_document`).** tf-idf per chunk:
  `score = Σ over query terms present of (1 + log(tf)) × idf`, with heading words counted twice.
  The log damps repetition (ten mentions are worth more than one, not ten times more); IDF makes
  rare words like `AUTH_SECRET_KEY` worth more than `api` or `database`.
- **Selection (`retrieve`).** Score every chunk, sort descending, return the top 3 that clear the
  guardrail. A chunk's **coverage** = `score / (Σ idf of every query term)`, i.e. what fraction of
  what the user asked about this chunk actually explains.

**What tradeoffs did you make?**
Keyword retrieval is fast, offline, and explainable line by line, but it has no idea that "connect"
and "connection string" mean the same thing unless the stemming rule catches it, and no idea that
"auth" ≈ "authentication" beyond shared characters. Chunking by section gives readable snippets and
good precision on a 4-file corpus, but a 700-character cap is arbitrary and would need real tuning on
a bigger corpus. Coverage-based refusal is cheap and predictable, but it is a lexical proxy for
relevance — it cannot tell "the docs discuss this" from "the docs use the same words".

---

## 3. Use of the LLM (Gemini)

**When does DocuBot call the LLM and when does it not?**

- **Naive LLM mode:** calls the LLM for every question and never sends it the docs. The starter code
  builds `all_text` and then deliberately ignores it, so this mode is the "model's memory" baseline —
  it cannot cite a file even when it happens to be right.
- **Retrieval only mode:** never calls the LLM. Fully offline, deterministic, shows exactly which
  sections matched and why (score + coverage).
- **RAG mode:** calls the LLM only after retrieval has produced snippets that clear the guardrail.
  If retrieval refuses, the LLM is never called — the refusal is decided before generation.

**What instructions do you give the LLM to keep it grounded?**
The RAG prompt shows each snippet labelled with its filename and says: answer using only the
snippets; do not invent functions, endpoints, or config values; briefly name the files relied on;
and if the snippets are not enough, reply exactly `I do not know based on the docs I have.` The
retrieval guardrail in front of it is the stronger of the two controls — a prompt can be argued
with, an empty snippet list cannot.

---

## 4. Experiments and Comparisons

Same queries, same wording, all three modes, one run each. Raw transcript kept out of the repo;
these are the observed behaviours.

| Query | Naive LLM: helpful or harmful? | Retrieval only: helpful or harmful? | RAG: helpful or harmful? | Notes |
|------|---------------------------------|--------------------------------------|---------------------------|-------|
| Where is the auth token generated? | Harmful-by-omission — asked me for the codebase instead of answering | Helpful — `AUTH.md > Token Generation` first, coverage 1.00, names `generate_access_token` in `auth_utils.py` | Helpful — one sentence, correct, cites `AUTH.md` | The naive model had no way to know; it asked for context rather than guessing. |
| Which endpoint returns all users? | Harmful-by-omission — asked for the OpenAPI spec | Helpful — `API Reference > GET /api/users`, includes "Only accessible to admins" | Helpful — `GET /api/users`, cites `API_REFERENCE.md` | Retrieval ranked it first even though `get_all_users()` in `DATABASE.md` also matched. |
| How do I connect to the database? | **Harmful and confident** — invented `DB_HOST` / `DB_PORT` / `DB_USER` / `DB_PASSWORD` plus Prisma, SQLAlchemy and Laravel snippets. The real project uses a single `DATABASE_URL` with SQLite by default | Helpful — `DATABASE.md > Connection Configuration`, `DATABASE_URL`, sqlite and postgres examples | Helpful — explains `DATABASE_URL`, the SQLite default, and `db.py`, cites `DATABASE.md` | The clearest case of fluent-and-wrong: every line of the naive answer is standard practice and none of it is this project. |
| How does a client refresh an access token? | **Harmful and confident** — produced a full OAuth2 refresh-token grant (`grant_type=refresh_token`, `/oauth/token`, `client_secret`, rotating refresh tokens). This project has no refresh tokens and no OAuth | Helpful — `AUTH.md > Client Workflow` (call `/api/refresh`) then `POST /api/refresh` with a Bearer header | Helpful — both steps, correct format, cites `AUTH.md` + `API_REFERENCE.md` | Naive was not vague, it was specific and wrong, which is worse. |
| Is there any mention of payment processing in these docs? | Harmful-by-omission — asked me to paste the docs | Helpful — refuses: "I do not know based on these docs." | Helpful — same refusal, without calling the model | The only query where "I don't know" is the correct answer, and both grounded modes gave it. |
| How do I authenticate? | **Harmful and confident** — "include your API key in the Authorization header", which is not this system | **Wrong** — refuses, although `AUTH.md > Client Workflow` answers it | **Wrong** — inherits the refusal, never reaches the model | Both grounded modes are beaten by the naive model here. Retrieval sees one query term and the two-term guardrail blocks it. |

**What patterns did you notice?**

- **Naive generation fails in two different ways, and only one is obvious.** On four of six questions
  it asked for the codebase — honest but useless. On the two questions where it had strong priors
  (database setup, token refresh) it produced authoritative, well-formatted, completely wrong answers
  with no signal that anything was missing. Those are the dangerous ones: the database answer is more
  convincing than the true one, because `DB_HOST`-style `.env` files are what most projects use.
- **Retrieval only is accurate and unreadable.** It returns the winning section first every time, but
  it hands back markdown tables, fenced code and heading lines. It is evidence, not an answer — you
  still have to read it, and on "which endpoint returns all users" it also returns the `db.py` helper
  list, which is a different layer of the system.
- **RAG is the only mode that is both readable and checkable**, because it inherits retrieval's
  citations while the model does the summarising. Its answers were shorter than retrieval's output
  and each named the file it used.
- **RAG's failure is retrieval's failure, not the model's.** On "How do I authenticate?" RAG refused.
  The model was never called; the guardrail decided. Every bad result in this table traces to the
  retrieval layer, not to Gemini.
- **The naive mode's weakness is structural, not prompt-tunable.** `naive_answer_over_full_docs`
  receives `all_text` and drops it — no amount of prompt wording gives it access to `docs/`.

## 5. Failure Cases and Guardrails

**Failure case 1 — the off-topic question that got an answer.**
Question: "Is there any mention of payment processing in these docs?" With only a coverage threshold
of 0.30, `Setup Guide > Using the Docs Assistant (DocuBot)` scored 0.37 and was returned, because
the word "Docs" in the heading matched "docs" in the question. Four query words, one incidental
match, and the system answered a question the corpus cannot answer. It should have refused.
Fix: a second, independent guardrail — a chunk must match at least **two** distinct query terms.

**Failure case 2 — the right document ranked second.**
Question: "What does the /api/projects/<project_id> route return?" `DATABASE.md > Query Helpers`
outranked the actual endpoint section, because `get_project_details(project_id)` mentions
`project_id` more times than the endpoint description does. It should have surfaced the route.
Fix: count heading words twice — the route is literally in the heading of the correct chunk.

**Failure case 3 — the guardrail refuses a question the docs answer.**
Question: "How do I authenticate?" Tokenizing leaves one meaningful term (`authenticat`), so the
"at least two matched terms" rule can never be satisfied and the system refuses — even though
`AUTH.md` explains exactly that, in a section literally called *Client Workflow*. The two-term rule
that fixed failure case 1 is a blunt instrument: it eliminates coincidence matches and short
single-topic questions in the same stroke. The fix is not a lower threshold (that re-opens failure
case 1) but better query expansion or embeddings, so one term can carry enough confidence on its own.

A second, related weakness: "How does auth work?" returns `Setup Guide > Troubleshooting` ahead of
`AUTH.md`, because the troubleshooting heading mentions `AUTH_SECRET_KEY` and the word "work" also
appears in "if LLM features do not work" — two cheap matches beat one strong one. Flat tf-idf cannot
tell a heading about auth from a heading mentioning auth.

**When should DocuBot say "I do not know based on the docs I have"?**
1. When no chunk matches at least two distinct query terms (nothing in the corpus is about this).
2. When the best chunk covers less than 35% of the question's weighted terms (the corpus touches the
   topic but does not answer the question).
3. In RAG, when the model itself judges that the retrieved snippets are insufficient — the prompt
   makes that an explicit, allowed output rather than a failure.

**What guardrails did you implement?**
- Minimum two distinct matched query terms per returned chunk.
- Minimum coverage of 0.35 of the query's IDF weight.
- Refusal decided before the LLM is called: empty retrieval means no generation at all.
- `top_k=3` with de-duplication, so the model sees a bounded, focused context instead of whole files.
- A RAG prompt restricted to snippet content, with a fixed refusal string and a requirement to name
  the files used.

---

## 6. Limitations and Future Improvements

**Current limitations**

1. Lexical only — no synonyms, no embeddings, so "auth" / "authentication" and "connect" /
   "connection string" only match by character overlap and hand-written stemming rules.
2. Fixed 700-character chunks ignore semantics: a table can be split from the paragraph that
   explains it, and a 40-character section with no body is dropped entirely.
3. Scoring is unnormalised tf-idf, so long chunks and repeated column names (`project_id`) win ties
   they should not; ties are broken by insertion order, which is arbitrary.
4. No conversation memory — each question is answered from scratch, so follow-ups like "and the
   second one?" retrieve nothing useful.
5. The evaluation set is 8 hand-written queries against 4 documents. A 0.88 hit rate (the one miss is
   the negative control, which the harness scores as a miss by design) is close to meaningless at
   this size.

**Future improvements**

1. Swap the scorer for BM25 (term-frequency saturation + length normalisation) — same index, better
   ranking, and it fixes limitation 3.
2. Embed chunks with a small local model and blend vector similarity with keyword score, so
   paraphrase and synonyms work without a hosted service.
3. Grow the evaluation set to 30–50 real questions with expected files, and tune the two thresholds
   against it instead of by inspection.

---

## 7. Responsible Use

**Where could this system cause real world harm if used carelessly?**
A developer asks "how do I connect to production?", gets a confident answer grounded in a doc that
describes *local* SQLite, and points a service at the wrong database. More generally: the refusal
guardrail protects against questions the docs never mention, but not against questions the docs
answer *badly or out of date* — retrieval will confidently return stale guidance, and RAG will
summarise it faithfully. On a corpus with secrets in it, retrieval also means anyone who can ask a
question can surface any file that matches, so access control has to sit in front of the index, not
behind the prompt.

**What instructions would you give real developers who want to use DocuBot safely?**

- Treat every answer as a pointer to a file, then open the file. The citation is the useful part;
  the prose is a convenience.
- Never let a docs assistant answer questions about production state (credentials, live schema,
  deployment topology) from static documentation.
- Keep the corpus current — a grounded answer from stale docs is more dangerous than an ungrounded
  one, because it comes with a citation that looks like proof.
- Check the refusal rate after any docs change: a sudden drop usually means new content is matching
  loosely, not that the bot got smarter.
