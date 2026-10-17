# EduMind AI: Misconception Detective (fractions MVP)

A working implementation of the first release described in the EduMind AI project plan:
middle-school **fractions**, with the **TRACE** loop (Trace reasoning, Rank causes, Apply a
targeted repair, Check on a *fresh* question).

When a learner gets a question wrong, EduMind does **not** just show the answer. It asks one or
two short questions about their thinking, scores the evidence against a reviewed library of six
misconceptions, explains the idea in a way that targets that mistake (with bar pictures), and
then checks understanding on a brand-new question. If the evidence is weak or contradictory it
**abstains** and gives a general explanation instead of guessing.

## Run it

Python 3.10 or newer.

```bash
cd edumind
pip install -r requirements.txt
uvicorn app.asgi:app --port 8000        # then open http://127.0.0.1:8000
```

The app serves its own front-end (no build step) and stores data in `data/edumind.db` (SQLite).

```bash
python -m pytest -q                     # 210+ tests: maths, bank integrity, diagnostics, API, ML
python tests/e2e_browser.py http://127.0.0.1:8000   # optional real-browser check (needs playwright)
```

| Environment variable | Default | Meaning |
|---|---|---|
| `EDUMIND_DB` | `data/edumind.db` | SQLite file |
| `EDUMIND_RATE_LIMIT` | `300` | requests per minute per IP on `/api` (`0` = off) |
| `EDUMIND_MODEL` | `models/misconception_clf.joblib` | optional ML second opinion (`""` = off) |

Deploying: any host that runs Python works, e.g. `uvicorn app.asgi:app --host 0.0.0.0 --port $PORT`.
Put the SQLite file on a persistent disk, or move to PostgreSQL before real use.

## What is implemented (and what is not)

| Plan item | Status |
|---|---|
| FR-01 to FR-08 (topics, attempts, deterministic fraction checking, diagnostic follow-ups, ranked misconception scoring with uncertainty, targeted correction, fresh mastery check, progress history) | **Done** |
| FR-10 Quiz generation | **Done** (`/api/quiz`, `/api/quiz/grade`) |
| FR-12 Revision planner | **Basic**: a failed mastery check queues the topic for review the next day |
| Privacy: minimal data, deletion | **Done**: random learner ID, no name or email, "Delete my data" erases everything |
| Safety: rate limiting, input validation, CSP and security headers | **Done** |
| FR-09 PDF upload and Q&A, FR-11 Hindi and Marathi, FR-13 teacher review console, accounts and login | **Not built** (P1/P2 in the plan) |
| LLM-written explanations | **Deliberately not used**: every explanation is reviewed, fixed text. An LLM can be added later behind the same `intervention` shape |

The question bank has 53 generated, filtered questions (5 skills). Every question is checked by
tests so that each misconception's predicted wrong answer is distinct from the correct one.

## How the diagnosis works

1. The answer is parsed and marked by plain Python (`app/fraction_engine.py`). No AI decides correctness.
2. A wrong answer is compared with the answers each misconception would produce (`app/misconceptions.py`). A match gives +3.
3. The learner answers up to two multiple-choice follow-ups. Each option adds or subtracts points (`app/diagnostic.py`). "I guessed" lowers confidence.
4. Confident means: top score of at least 3, at least 60% of the evidence, and a lead of at least 2 over the runner-up. Otherwise ask again, and after two follow-ups give a **general** explanation.
5. A targeted explanation (`app/interventions.py`) uses a worked example that is *not* the learner's own question.
6. A fresh question from the same skill checks transfer. Two misses schedule a review and reveal the answer.

The confidence number is an engineering indicator, **not a calibrated probability**, and the UI says so.

## API

| Method and path | Purpose |
|---|---|
| `POST /api/learners` | create an anonymous learner |
| `DELETE /api/learners/{id}` | erase all of that learner's data |
| `GET /api/next-question?learner_id=&skill=` | next practice question (never includes the answer) |
| `POST /api/attempts` | submit an answer; returns correct/incorrect and the first follow-up |
| `POST /api/diagnostics/{session_id}/responses` | answer a follow-up; returns another follow-up or the repair and a fresh question |
| `POST /api/mastery-checks` | answer the fresh question |
| `GET /api/progress?learner_id=` | skills, recurring misconceptions, review queue |
| `GET /api/quiz`, `POST /api/quiz/grade` | stand-alone quiz mode |
| `GET /api/patterns/{id}/intervention?question_id=` | the reviewed explanation for a pattern |
| `GET /api/health`, `GET /api/skills` | health and topic list |

Interactive docs: `/docs` while the server runs.

## Machine learning

See **[ml/README.md](ml/README.md)** for dataset links and how each fits this project.

```bash
python -m ml.train --n 20000                       # simulated learners -> models/misconception_clf.joblib
python -m ml.eedi_baseline --data-dir path/to/eedi # after downloading the Kaggle files
```

The shipped model is trained on **simulated** learners, so its scores (see `models/metrics.json`)
describe the simulator, not real students. It only adds a clearly labelled "Experimental model"
line inside "Why am I seeing this?". The rules always make the decision. Retrain on real data
before giving the model any more authority.

## Layout

```
app/        FastAPI app: fraction_engine, misconceptions, questions, diagnostic, interventions, service, db, main
frontend/   index.html, styles.css, app.js (no build step, no dependencies)
ml/         features, synthetic learners, training, Eedi baseline
tests/      pytest suite + optional browser e2e
models/     trained model + metrics
```

## Before showing this to real students

* Have a maths teacher review the question bank, the misconception patterns, and every explanation.
* Get school and guardian permission for any pilot, and follow your institution's rules.
* Replace SQLite with a managed database and add authentication if learners need accounts.
* Model files are pickles: only load files you trained yourself.
