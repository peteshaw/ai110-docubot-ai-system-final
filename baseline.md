# Baseline — DocuBot as imported (before any changes)

**Recorded:** 2026-11-11
**Purpose:** the "before" half of every before/after claim in the README and model card.
Nothing in this file may change after the first functional commit.

## Environment

| Item | Value |
|---|---|
| Python | 3.11.2 |
| venv | `.venv/` (gitignored) |
| google-genai | 3.1.0 |
| python-dotenv | 1.2.4 |
| certifi | 2026.10.23 |
| Install | `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt` — clean, no conflicts |
| Baseline commit | `b0e4b3f` — "Import DocuBot (AI110 Module 4 tinker) as untouched baseline" |

No API key was needed: `evaluation.py` exercises retrieval only.

## Command

```bash
.venv/bin/python evaluation.py
```

## Output

```text
Running retrieval evaluation...

Evaluation Results
------------------
Hit rate: 0.88
```

## Per-query results (8 queries)

| # | Query | Expected file(s) | Top-3 retrieved | Hit |
|---|---|---|---|---|
| 1 | Where is the auth token generated? | AUTH.md | AUTH.md, AUTH.md, SETUP.md | ✅ |
| 2 | What environment variables are required for authentication? | AUTH.md | SETUP.md, AUTH.md, AUTH.md | ✅ |
| 3 | How do I connect to the database? | DATABASE.md | DATABASE.md ×3 | ✅ |
| 4 | Which endpoint lists all users? | API_REFERENCE.md | API_REFERENCE.md, API_REFERENCE.md, DATABASE.md | ✅ |
| 5 | What does the `/api/projects/<project_id>` route return? | API_REFERENCE.md | API_REFERENCE.md, DATABASE.md, API_REFERENCE.md | ✅ |
| 6 | How does a client refresh an access token? | AUTH.md | AUTH.md, API_REFERENCE.md, AUTH.md | ✅ |
| 7 | Which fields are stored in the users table? | API_REFERENCE.md, DATABASE.md | DATABASE.md ×3 | ✅ |
| 8 | Is there any mention of payment processing in these docs? | *(none — negative control)* | *(none)* | ❌ by design |

**Reading:** 7 of 8 hit. The single "miss" is the negative control, where returning
nothing is the correct behaviour — the harness scores empty-expected rows as misses.
So retrieval is 7/7 on answerable queries and 1/1 on the refusal control.

This is why the headline number stays at 0.88 and must not be quietly restated as
"100%": the harness, not the model, defines the score.

## Known failures carried in from the Module 4 model card

Three failure cases were already root-caused in `model_card.md`. They are the work
items for this project, not new discoveries:

1. **Off-topic question answered** because "docs" matched a heading — guardrail too weak.
2. **Right document ranked second** (`get_project_details` beat the route) — heading
   weighting / length normalisation.
3. **"How do I authenticate?" refused** — only one meaningful term, so
   `MIN_MATCHED_TERMS=2` fires. Needs query expansion or verify-then-retry.

## Repo state at baseline

- `assets/` and `diagrams/` created, empty.
- No git remote yet (public repo not created).
- Original archive under `courses/ai110-base/project/` untouched.
