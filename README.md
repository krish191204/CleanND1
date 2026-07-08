# CleanND — Cleaned, Credible News from X/Twitter

A production-grade real-time news aggregation system that ingests posts from X/Twitter, runs
them through a 6-stage cleaning pipeline, scores credibility, and serves a modern dashboard
with a human-in-the-loop review queue that continuously improves the underlying models.

> **Note on the included API key.** A twitterapi.io key was provided in this conversation
> for development. The current balance reads `Credits is not enough. Please recharge.` —
> the real API is wired up and works as soon as credits are added. The system ships with a
> `/api/ingest/mock` endpoint that runs the full pipeline on synthetic tweets so the entire
> stack (pipeline → DB → dashboard → review queue → retrain) is demonstrable today.
> The dev key is in your local `.env` (gitignored) — override at runtime via
> `TWITTER_API_KEY=...` or rotate it from the twitterapi.io dashboard.

---

## Architecture

```
   X / Twitter API               ┌──────────────────────────────┐
       (or mock) ───────────────▶│  STAGE 0: Software Focus     │
                                  │   AI/ML + SW-sphere gate     │
                                  │  STAGE 1: API Filter          │
                                  │  STAGE 2: Text Clean          │
                                  │   ↓ MinHash dedup             │
                                  │  STAGE 3: Bot Detection       │
                                  │   RF + heuristics (+ DistilBERT)│
                                  │  STAGE 3.5: Noise Filter     │
                                  │   opinion / engagement-bait  │
                                  │  STAGE 4: Relevance + Burst   │
                                  │   sentence-transformers       │
                                  │  STAGE 5: Credibility         │
                                  │   domain / source / burst     │
                                  └────────┬─────────────────────┘
                                           │
              ┌────────────────────────────┼────────────────────────────┐
              ▼                            ▼                            ▼
       ┌────────────┐               ┌────────────┐              ┌─────────────┐
       │ Postgres / │               │ Review     │              │ WebSocket   │
       │ SQLite     │               │ Queue      │              │ live feed   │
       └─────┬──────┘               └────┬───────┘              └──────┬──────┘
             │                            │                             │
             ▼                            ▼                             ▼
      ┌──────────────────────────────────────────────────────────────────────┐
      │                  FastAPI backend (8000)                             │
      │   /api/feed  /api/ingest  /api/review/*  /api/ml/*  /api/stats     │
      └─────────────────────────────┬──────────────────────────────────────┘
                                    ▼
                          ┌────────────────────┐
                          │  Next.js dashboard │
                          │   (port 3000 dev / │
                          │   served at /)      │
                          └────────────────────┘
                                    │
                          ┌─────────┴──────────┐
                          ▼                    ▼
                   Active learning       Celery beat
                   → retrain nightly      (or trigger)
```

---

## Quick start

### 1. Backend

```bash
cd backend
pip install --break-system-packages -r requirements.txt
# Train the initial bot classifier (uses synthetic seed data)
python -m scripts.train_initial_model
# Start the API
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

API docs at `http://localhost:8000/docs`.

### 2. Frontend

```bash
cd frontend
npm install
npm run build            # produces /out (static export)
# OR
npm run dev              # dev server on :3000
```

If you built the static export, the FastAPI server picks it up automatically and serves
the dashboard at `http://localhost:8000/`.

### 3. Try the system

Open `http://localhost:8000/` → click **+15 mock** to inject synthetic tweets through
the full pipeline → see them in the feed → switch to **Review queue** → label a few →
**Retrain model** → watch the F1 improve in **Metrics**.

The mock generator emits software-sphere tweets (Anthropic/Claude, PyTorch, Kubernetes,
React, Stripe-engineering style) so they pass the Stage 0 focus gate and exercise every
downstream stage end-to-end. A minority of synthetic tweets use crypto/airdrop
templates and are visibly rejected at Stage 0's `tweet_scam_terms` sub-check — useful
for demo contrast.

**Auto-seed (kiosk mode).** By default the backend runs a background task that
checks every `MOCK_AUTO_SEED_CHECK_INTERVAL_SECONDS` (60s) whether the surfaced feed
is below `MOCK_AUTO_SEED_MIN_FEED_SIZE` (5). If so, it runs a small mock ingest
(`MOCK_AUTO_SEED_BATCH_SIZE` = 15) to top it up. This keeps the dashboard populated
during long demos without you having to click "+15 mock" repeatedly. Disable with
`MOCK_AUTO_SEED_ENABLED=false` (the test suite does this automatically).

For the real X API once your twitterapi.io account has credits:

```bash
curl -X POST http://localhost:8000/api/ingest \
  -H 'Content-Type: application/json' \
  -d '{"beat":"breaking","max_results":20}'
```

---

## The 6-stage cleaning pipeline

Stage 0 is an opt-in soft gate that restricts the feed to the broader software
sphere (AI/ML + programming + tooling). It is enabled by default
(`software_focus_enabled = True`) and can be turned off via env to ingest
unfiltered X traffic.

| Stage | Module | What it does | Tunable |
|---|---|---|---|
| 0. Software focus | `app/pipeline/stage_software_focus.py` | Bio/handle/content must be AI/ML or software sphere; ≥100 followers, ≥30 days old, no scam terms (giveaway/airdrop/crypto), ≥5 engagement | `software_focus_enabled`, `software_min_followers`, `software_min_account_age_days`, `software_min_engagement`, `check_scam`, `check_retweets`, `check_engagement` |
| 1. API filter | `app/pipeline/stage1_api_filter.py` | Language, follower count, account age, hashtag/URL spam, RT/quote detection | `min_followers`, `min_account_age_days`, `max_hashtags`, `max_urls`, `allowed_languages` |
| 2. Text clean | `app/pipeline/stage2_text_clean.py` | Strip URLs/mentions, lowercase, NFC, emoji→text, MinHash near-dup, tokenize, lemmatize (spaCy → NLTK → regex) | `num_perm`, dedup window, tokenizer backend |
| 3. Bot detect | `app/pipeline/stage3_bot_detect.py` | Hand-crafted features → RandomForest + heuristic score + optional DistilBERT | `reject_threshold`, `uncertain_band`, model paths |
| 3.5. Noise filter | `app/pipeline/stage3b_noise.py` | Opinion / engagement-bait / political commentary / medical conspiracy / celebratory greetings; rejects hard, demotes borderline via `credibility_penalty` | `noise_reject_threshold` |
| 4. Relevance | `app/pipeline/stage4_relevance.py` | Sentence-transformers embedding + cosine to news centroid; burst detection; quality score (length, media, engagement) | `relevance_threshold`, `burst_window_seconds`, `burst_min_count` |
| 5. Credibility | `app/pipeline/stage5_credibility.py` | Whitelisted domains, source verification, propagation (burst), account age | `whitelist`, `blacklist`, `known_handles`, `high_t` / `medium_t` |

Each stage:
- implements the `Stage[I, O]` interface
- returns `StageResult(passed, rejected, stats)`
- logs pass / reject / elapsed time
- is independently unit-testable

---

## The data model

```python
RawTweet        # raw API output
   ↓
CleanedTweet    # normalized text + tokens + MinHash
   ↓            # stages 3,4 add bot_score, relevance, quality
ScoredTweet     # stage 5 adds credibility, final_score
   ↓
NewsCard        # lean DTO for the frontend
ReviewItem      # queued for human review
```

Pydantic schemas in `app/models/schemas.py`, SQLAlchemy ORM in `app/models/db_models.py`.

---

## Human-in-the-loop review

The active-learning loop is the heart of the system:

1. **Selection.** After each pipeline run, the orchestrator pushes borderline items to the
   review queue:
   - Bot scores in the **0.55–0.95** band (uncertain but probably one or the other)
   - Tweets rejected by credibility but with **relevance ≥ 0.6** (false-negatives we may
     want to rescue)
   - All items with `bot_label = UNCERTAIN` that pass other stages

2. **Prioritisation.** `uncertainty_margin = 1 - 2·|p - 0.5|` averaged across bot and
   credibility. The review queue is sorted by uncertainty descending — humans see the
   most informative items first.

3. **Labelling.** The `/review` UI offers Approve (human) / Reject (bot/spam) plus a
   category tag (breaking / world / tech / finance / science / …) and free-form notes.

4. **Retraining.** `POST /api/ml/retrain` (or nightly Celery beat) pulls all labelled
   reviews, optionally augments with the seed dataset, retrains the RandomForest, evaluates
   on a stratified holdout, and records `precision`, `recall`, `f1` to the
   `model_metrics` table — surfaced in the **Metrics** tab.

5. **Diversity sampling.** `app/ml/active_learning.py:diversity_sample` does k-means++
   over the embedded tweets so that retraining data isn't dominated by one cluster.

---

## API surface

| Method | Path | Description |
|---|---|---|
| GET  | `/api/health` | Liveness probe |
| GET  | `/api/feed?limit&min_credibility&handle` | Paginated news cards |
| GET  | `/api/feed/card/{id}` | Single card |
| POST | `/api/ingest` | Run the real API + pipeline (`{"beat":"breaking"}` or `{"query":"…"}`) |
| POST | `/api/ingest/mock?n=30&seed=42` | Synthetic pipeline run |
| GET  | `/api/stats` | Pipeline aggregates |
| GET  | `/api/review/queue?limit=25` | Unlabeled items, sorted by uncertainty |
| POST | `/api/review/{id}/label` | `{label, category, notes, labeler_id}` |
| GET  | `/api/review/stats` | Counts of labeled/unlabeled/approved/rejected |
| GET  | `/api/ml/metrics` | Recent metric history per model |
| POST | `/api/ml/retrain` | Trigger a retraining pass |
| WS   | `/ws` | Real-time feed updates (10s tick) |

Open `http://localhost:8000/docs` for the interactive Swagger UI.

---

## Dashboard

- **Header.** Logo, tab switcher (Feed / Review / Metrics), version chip.
- **Feed tab.** Filters (min credibility, handle, refresh), inline ingest buttons. Each card
  shows avatar (initials fallback), handle, ✓ verified badge, headline, summary, media grid,
  color-coded **credibility badge** with the model score, "why shown" chips
  (Trending now / Verified account / Trusted source / Corroborated), and a "View on X" link.
- **Review queue tab.** Side-by-side: model prediction bars (bot, credibility, relevance) +
  uncertainty margin + reasons on the left, raw tweet + category dropdown + notes on the
  right. Approve/Reject buttons. **Retrain** button at the top.
- **Metrics tab.** Per-model metric history (F1, precision, recall) with version + sample
  size. Renders as a compact table; the wire includes a `/ml/metrics` JSON endpoint ready for
  charting libraries.
- **Sidebar.** Live pipeline counts, beat launcher (breaking / world / tech / markets /
  science), "why this is clean" explainer.

---

## Tech choices and trade-offs

| Decision | Rationale |
|---|---|
| **FastAPI** | Async-native, OpenAPI for free, easy WebSocket support |
| **SQLite default / Postgres prod** | Zero-setup local; switch via `DATABASE_URL` |
| **scikit-learn RandomForest** | CPU-friendly, fast retrain, decent baseline, easy to swap |
| **DistilBERT (optional)** | Stronger bot detection, ~250 MB; lazy-loaded so 1.6 GB RAM boxes still work |
| **sentence-transformers all-MiniLM-L6-v2** | 80 MB, great quality/size ratio for the relevance centroid |
| **MinHash** | O(n) near-dup detection on streaming input — no O(n²) comparisons |
| **Celery** | Production retraining; in dev we use `BackgroundTasks` |
| **SWR** | Polling + cache + revalidation; drops in easily to WebSocket later |
| **Tailwind + lucide-react** | Fast iteration, consistent look, dark-by-default |
| **Mock auto-seed (kiosk mode)** | Background task that tops the feed up with synthetic tweets whenever the surfaced count drops below `MOCK_AUTO_SEED_MIN_FEED_SIZE`, so the demo dashboard stays populated without manual clicks. Disable via `MOCK_AUTO_SEED_ENABLED=false`. |

---

## Implementation roadmap

### MVP (✅ done)
- 6-stage pipeline with mock data (Stage 0 software-focus + Stages 1–5 + Stage 3.5 noise filter)
- All 6 stages execute end-to-end on synthetic and real (paid) data
- FastAPI with full REST surface
- SQLite persistence
- Next.js dashboard with Feed / Review / Metrics tabs
- Active-learning selection + label endpoint
- Initial bot-classifier training script
- Retraining pipeline that ingests human labels

### v1
- Train a DistilBERT bot classifier on a real labelled dataset
- Postgres migration + Alembic
- Auth (reviewers only)
- Per-user label history & agreement stats
- Better burst detection (HASHED MinHash clustering → event IDs)
- A/B different credibility weights in the UI

### v2
- Multi-source ingestion (Mastodon, Bluesky, RSS, Reddit)
- Claim-level extraction + LLM-assisted fact-check overlay
- Topic-aware retrieval (dense + BM25 hybrid) for "related coverage"
- Personalization with on-device preference vectors
- Trust graph: per-source reliability over time

---

## Evaluation metrics

For each model retrain we record to `model_metrics`:

- **Bot classifier:** precision, recall, F1, sample size, version timestamp
- **Credibility** (future): ROC-AUC vs. human-verified labels
- **Relevance** (future): NDCG@10 on a held-out topical set
- **Pipeline funnel:** ingested → passed_api → passed_clean → passed_bot → passed_rel →
  passed_cred → surfaced; review_queue depth

The Metrics tab shows the per-run history. Wire a charting lib (e.g. recharts) to
`/api/ml/metrics` to plot F1 over time.

---

## Continuous improvement plan

1. **Daily** — review queue depth, label distribution, top reasons for bot rejection.
2. **Weekly** — re-evaluate on a held-out human-labelled set; record drift metrics.
3. **Nightly** — retrain bot classifier on the union of {all labels ∪ seed}; record
   metrics; if F1 regresses > 5%, keep the previous model and alert.
4. **On-demand** — click "Retrain" in the dashboard after a labelling sprint.
5. **Active learning** — beyond simple uncertainty, incorporate diversity sampling
   (`active_learning.diversity_sample`) every K labels so the model sees varied examples.
6. **Drift detection** — track daily distributions of bot_score, credibility_score,
   and pipeline pass rates. Page on > 2σ shifts.

---

## API caveats & best practices

- **twitterapi.io rate limits.** A 402 response with `Credits is not enough` means the
  account is out of credits. The system surfaces the error cleanly; in production add
  exponential backoff and a token-bucket client.
- **X/Twitter terms of service.** Stay inside the official v2 API's `search/recent` and
  `users/:id/tweets` endpoints for production deployments. Third-party aggregators
  (twitterapi.io, Apify, Bright Data) are fine for prototyping but review the ToS for
  redistribution.
- **Privacy.** Hash or omit `author_id` from logs; rotate handles that look like PII.
- **Credibility ≠ truth.** The pipeline is a heuristic signal, not a fact-check. Always
  show the model's reasons, never a binary "true/false".
- **Bot detection bias.** Heuristics + supervised models can over-fire on accounts that
  tweet in non-English languages, that link to small personal sites, or that are simply new.
  The human-in-the-loop queue is the safety valve — make sure reviewers are diverse.

---

## Running the test suite

```bash
cd backend
pytest -q
```

Tests are scaffolded in `backend/tests/` — add new tests next to the code they cover.

---

## Files of interest

```
backend/
  app/
    config.py                 # env loading
    main.py                   # FastAPI entrypoint + WS + static frontend
    pipeline/
      base.py                 # Stage[T] / StageResult
      stage_software_focus.py # Stage 0: AI/ML + SW-sphere gate
      stage1_api_filter.py
      stage2_text_clean.py
      stage3_bot_detect.py
      stage3b_noise.py        # Stage 3.5: opinion / engagement-bait
      stage4_relevance.py
      stage5_credibility.py
      orchestrator.py         # Pipeline.run + active-learning gate
    services/
      db.py                   # SQLAlchemy wrapper
      twitter_client.py       # twitterapi.io HTTP client
      review_queue.py         # queue facade
      cards.py                # ScoredTweet -> NewsCard
    api/
      routes.py               # all REST endpoints
      websocket.py            # live feed broadcaster
    ml/
      features.py             # shared feature extractor
      train_bot.py            # seed data + RF trainer
      retrain.py              # pulls labels, refits, records metrics
      active_learning.py      # uncertainty + diversity sampling
      celery_app.py           # nightly beat (prod)
    models/
      schemas.py              # Pydantic contracts
      db_models.py            # SQLAlchemy ORM
  scripts/
    train_initial_model.py    # bootstrap entry point
frontend/
  app/page.tsx                # tab container
  components/                 # NewsCard, FeedList, ReviewQueueView, ...
  lib/api.ts                  # typed client
```

---

## License & disclaimer

This is a research/educational system. The credibility scores are **heuristic signals**, not
factual assessments. Do not use as a sole basis for editorial decisions. The dashboard is
designed to **show its work** — every card exposes the model's reasons, and every blocked
item is recoverable through the human review queue.
