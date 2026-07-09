# CleanND — Cleaned, Credible News from X/Twitter

A real-time news aggregator that pulls posts from X/Twitter, runs each one through a 6-stage
cleaning pipeline, scores it for credibility, and serves a dashboard with a human-in-the-loop
review queue that retrains the underlying models. Built so the demo dashboard has something
real to show without anyone manually clicking "+15 mock" every five minutes.

---

## TL;DR — what this is

| | |
|---|---|
| **What it does** | Pulls tweets from twitterapi.io, classifies them as news vs. noise, scores credibility, surfaces the good ones. |
| **Where it runs** | FastAPI on `:8000` (uvicorn), Next.js dashboard served from the same port. Open `http://localhost:8000`. |
| **Active feed** | ~60 real-news items right now (Muse from Meta, GPT-5.6 Sol from OpenAI, MistralAI robostral, NVIDIA Grok 4.5, AnthropicAI export-controls, …). |
| **Self-sustaining** | Background poller runs every 5 min, top-up mock auto-seeder runs every 60s when the feed drops below 3 items. |
| **Self-improving** | Human labels in the Review queue feed a nightly retrain of the bot classifier. F1 score climbs per cycle. |

---

## Motivation

The original ask: *"Why doesn't real news like Meta releasing Muse or China banning
overseas AI models show up in the feed?"* The answer was structural, not data-quality:

1. **The front door was too narrow.** `NEWS_QUERIES` used single keywords (`breaking`,
   `world`, `tech`) that returned 0 results from twitterapi.io. Multi-word product names
   (Muse, Claude 4, DeepSeek-R1, China AI ban) sailed past.
2. **There was no background poller for the real API.** Only the mock auto-seeder ran
   automatically; the real `/api/ingest` needed a manual `curl` every time.
3. **The known-news-handle credibility boost was underweighted** (+0.2), so authoritative
   sources sat at MEDIUM when they should be HIGH.

Once those were fixed, important real news started flowing. The "how do we get important
news without flooding with BS" question has the same answer: **feed the pipeline enough
high-signal input that the existing layers can do their job.** Most of the noise filtering
was already built — it was starved for input.

---

## How it works

```
   X/Twitter API                ┌─────────────────────────────────────┐
       (or mock) ──────────────▶│  STAGE 0: Software Focus           │
                                │   AI/ML + SW-sphere gate           │
                                │  STAGE 1: API Filter                │
                                │  STAGE 2: Text Clean + MinHash dedup│
                                │  STAGE 3: Bot Detection (RF + heur)│
                                │  STAGE 3.5: Noise / opinion filter  │
                                │  STAGE 4: Relevance + burst detect  │
                                │  STAGE 5: Credibility               │
                                └────────┬────────────────────────────┘
                                         │
              ┌──────────────────────────┼───────────────────────────────┐
              ▼                          ▼                               ▼
       ┌────────────┐             ┌──────────────┐                ┌─────────────────┐
       │ Postgres / │             │ Review queue │                │ WebSocket live  │
       │ SQLite     │             │              │                │ feed            │
       └─────┬──────┘             └──────┬───────┘                └────────┬────────┘
             ▼                            ▼                                 ▼
      ┌─────────────────────────────────────────────────────────────────────────┐
      │              FastAPI backend (uvicorn :8000)                            │
      │   /api/feed  /api/ingest  /api/ingest/mock  /api/review/*  /api/ml/*    │
      └─────────────────────────────────┬───────────────────────────────────────┘
                                        ▼
                          ┌──────────────────────────┐
                          │   Next.js dashboard      │
                          │   (served at /)         │
                          └──────────────────────────┘
                                        │
                              Human labels in
                              Review queue →
                              POST /api/ml/retrain →
                              model_metrics table
```

Three background tasks run inside the FastAPI lifespan:

- `_ws_pusher` — WebSocket broadcast loop (existing).
- `_mock_autoseed_task` — every 60s, if surfaced feed < 3 items, run a small mock ingest.
- `_real_ingest_task` — every 5 min, cycle through `["ai_news", "china_ai", "tech"]`
  with a 7s QPS gap between beats, run each through the pipeline, persist.

All three cancel cleanly on shutdown.

---

## The 6-stage cleaning pipeline

Each stage returns `StageResult(passed, rejected, stats)`. A tweet rejected by any stage
is dropped (or demoted at the surface floor). All stages are independently unit-testable.

| # | Stage | Module | What it does | Tunable |
|---|---|---|---|---|
| 0 | **Software focus** | `stage_software_focus.py` | Bio / handle / tweet must be AI/ML or software sphere; ≥100 followers, ≥30 days old, no scam terms (giveaway/airdrop/crypto), ≥5 engagement. Loads `data/known_software_accounts.json` for the curated pass. | `software_focus_enabled`, `software_min_followers`, `software_min_account_age_days`, `software_min_engagement`, `check_scam`, `check_retweets`, `check_engagement` |
| 1 | **API filter** | `stage1_api_filter.py` | Language, follower count, account age, hashtag/URL spam, RT/quote detection. | `min_followers`, `min_account_age_days`, `max_hashtags`, `max_urls`, `allowed_languages` |
| 2 | **Text clean** | `stage2_text_clean.py` | Strip URLs/mentions, lowercase, NFC, emoji→text, MinHash near-dup, tokenize, lemmatize (spaCy → NLTK → regex). | `num_perm`, dedup window, tokenizer backend |
| 3 | **Bot detection** | `stage3_bot_detect.py` | Hand-crafted features → RandomForest + heuristic score + optional DistilBERT. Ensemble: `0.5·clf + 0.3·heuristic + 0.2·bert`. Reject if `bot_score ≥ 1 - reject_threshold` (default 0.50). | `reject_threshold`, `uncertain_band`, model paths |
| 3.5 | **Noise filter** | `stage3b_noise.py` | Opinion / engagement-bait / political commentary / medical conspiracy / celebratory greetings; rejects hard, demotes borderline via `credibility_penalty`. | `noise_reject_threshold` |
| 4 | **Relevance** | `stage4_relevance.py` | sentence-transformers embedding (`all-MiniLM-L6-v2`) + cosine to news centroid; burst detection; quality score (length, media, engagement). | `relevance_threshold`, `burst_window_seconds`, `burst_min_count` |
| 5 | **Credibility** | `stage5_credibility.py` | Whitelisted domains, source verification, propagation (burst), account age, **known-news-handle boost** (loaded from `data/known_news_handles.json`, +0.30). | `whitelist`, `blacklist`, `credibility_known_news_handles_path`, `high_t`, `medium_t` |

Stage 0 is opt-in (`software_focus_enabled` setting). Stage 3.5 is between Bot and Relevance
because noise is a different signal than bot probability — humans can be noisy too.

The `Pipeline` orchestrator wires Stages 0 → 5 in order, then applies a surface floor
(`surface_min_credibility` = `medium` by default — both HIGH and MEDIUM items show).

---

## Self-sustaining feed

Once started, the system keeps itself populated. Three background tasks share the FastAPI
lifespan (in `app/main.py`):

```python
async def lifespan(app: FastAPI):
    push_task     = asyncio.create_task(_ws_pusher())
    autoseed_task = asyncio.create_task(_mock_autoseed_task())  # if mock_auto_seed_enabled
    real_task     = asyncio.create_task(_real_ingest_task())    # if real_ingest_enabled
    try: yield
    finally: cancel all three
```

### `_mock_autoseed_task` (kiosk mode — no credits required)

Every 60s, check the surfaced feed count. If below `MOCK_AUTO_SEED_MIN_FEED_SIZE` (3),
run `_run_mock_ingest(n=15)`. The check uses the *same filter as the feed endpoint*
(`passed_all_stages=True AND credibility_score >= min_credibility`) so the count reflects
what's actually visible. Otherwise the autoseed skips.

### `_real_ingest_task` (twitterapi.io — needs credits)

Every 5 min, cycle through the curated queries:

```python
for q in s.real_ingest_queries:           # ["ai_news", "china_ai", "tech"]
    n = await _ingest_real_to_db(q, ...)   # API → pipeline → DB upsert
    persisted += n
    await asyncio.sleep(s.real_ingest_query_delay_seconds)  # 7s QPS gap
```

The 7-second gap is because twitterapi.io's free tier caps at **1 request every 5
seconds**. On `TwitterAPIError` (402/429/5xx), the poller backs off for the rest of the
cycle. There's also a per-cycle `max_persist_per_cycle` (30) to bound credit use.

### Curated query beats (`app/services/twitter_client.py`)

| Beat | Query | What it catches |
|---|---|---|
| `ai_news` | `(OpenAI OR Anthropic OR "Claude" OR "GPT" OR "Meta AI" OR "Google DeepMind" OR Mistral OR "Hugging Face" OR NVIDIA OR PyTorch OR "image generation" OR "video model") lang:en min_faves:3` | Model releases, lab announcements. *Catches the Meta Muse story.* |
| `china_ai` | `("DeepSeek" OR "Qwen" OR "ERNIE" OR "Pangu" OR "HunYuan" OR "Hunyuan" OR "Baichuan" OR "GLM" OR "ChatGLM" OR "kimi" OR "Moonshot" OR "Zhipu" OR "AI model" OR "AI act" OR "AI ban" OR "AI export" OR "AI policy" OR "AI regulation") (China OR Chinese OR Alibaba OR Baidu OR Tencent OR Huawei OR "state council" OR Beijing OR Shanghai) lang:en min_faves:3` | Chinese AI labs + policy + export controls. *Catches the Anthropic export-controls lift + China AI policy.* |
| `tech` | `(AI OR "machine learning" OR OpenAI OR Anthropic OR NVIDIA OR PyTorch OR kubernetes OR rustlang OR React) lang:en min_faves:5` | General tech/software news. |
| `breaking` / `world` / `finance` / `science` | Multi-keyword topical ORs | Available beats, not currently polled (credit-conservative). |

**Why multi-keyword queries?** Empirically: a single-word query like `breaking` returns 0
tweets from twitterapi.io. `AI OR "machine learning" OR OpenAI OR ...` returns 15-30. The
broader front door lets the downstream pipeline apply its 9-stage noise filter and surface
the same 2-5 high-quality items.

**Why `min_faves:N` at the API level?** Cheapest place to filter is before the data ever
reaches our pipeline. One served tweet with 50 likes is worth more than 50 served tweets
with 0 likes (and the latter is most of what twitterapi.io returns without the filter).

---

## The "no BS" defense — 10 filtering layers

The end-to-end funnel applies noise filtering at every layer; each catches what the
previous missed:

| # | Layer | What it kills | Where |
|---|---|---|---|
| 0 | **API-level operators** | low-engagement noise, off-topic | `min_faves:N` in the query string |
| 1 | **Query keywords** | off-topic garbage | multi-keyword ORs (above) |
| 2 | **Stage 0** — software focus | non-software tweets | `stage_software_focus.py` |
| 3 | **Stage 1** — API filter | spam-link dumps, gibberish | `stage1_api_filter.py` |
| 4 | **Stage 2** — text clean + MinHash dedup | near-duplicates within a batch | `stage2_text_clean.py` |
| 5 | **Stage 3** — bot detection | spam bots | `stage3_bot_detect.py` |
| 6 | **Stage 3.5** — noise filter | opinion, engagement-bait | `stage3b_noise.py` |
| 7 | **Stage 4** — relevance + burst | single-source claims vs. corroborated events | `stage4_relevance.py` (burst flag wired) |
| 8 | **Stage 5** — credibility | author reputation, domain, known-news-handle boost | `stage5_credibility.py` |
| 9 | **Surface floor** | everything below MEDIUM credibility | `surface_min_credibility` setting |

Cumulative effect: ~100 raw tweets → ~5 surfaced. Each layer costs almost nothing
(a regex match or a numeric comparison). Widening layer 1 (queries) without changing
layers 2-9 produces noisy output; trust the layers.

---

## Human-in-the-loop active learning

```
1. Pipeline runs → borderline tweets queued to Review queue
   - bot scores in 0.55-0.95 band
   - tweets rejected by credibility but with relevance ≥ 0.6
   - all items with bot_label = UNCERTAIN

2. UI shows uncertain items first (uncertainty_margin = 1 - 2·|p - 0.5|)

3. You label them: approved / rejected / needs_more_info + category + notes

4. POST /api/ml/retrain (or nightly Celery beat):
   - pulls all labelled reviews
   - retrains the RandomForest bot classifier
   - records precision / recall / F1 to model_metrics table

5. Next pipeline run uses the smarter model
```

The bot classifie is retrained with whatever bootstrap data is in
`app/ml/train_bot.py` plus all labelled reviews. Diversity sampling
(`active_learning.diversity_sample`) keeps the retraining data from being dominated
by one cluster — k-means++ over embedded reviews.

---

## Dashboard

Open `http://localhost:8000/`. Three tabs:

- **Feed** — cards ranked by `final_score` (composite of credibility + relevance +
  quality + inverse bot). Each card shows the author, headline, summary, media grid,
  credibility badge, **why_shown** chips (`verified_account`, `known_news_handle`,
  `low_bot_probability`, `domain_whitelisted`, `co_corroborated_burst`, `trending_now`),
  "View on X" link.
- **Review queue** — side-by-side model prediction bars (bot, credibility, relevance) +
  uncertainty margin + reasons on the left, raw tweet + category dropdown + notes on the
  right. Approve / Reject / Needs more info buttons. **Retrain** button at the top.
- **Metrics** — per-model metric history (F1, precision, recall) with version + sample size.
  Ready for recharts/d3 wiring via the `/api/ml/metrics` JSON endpoint.

Sidebar shows live pipeline counts (`/api/stats`), a beat launcher for one-shot ingests,
and the "why this is clean" explainer.

---

## API surface

| Method | Path | Description |
|---|---|---|
| GET  | `/api/health` | Liveness probe |
| GET  | `/api/feed?limit&min_credibility&handle` | Paginated news cards |
| GET  | `/api/feed/card/{id}` | Single card |
| POST | `/api/ingest` | Real API + pipeline (`{"query":"..."}` or `{"beat":"ai_news"}`). `poll=true` starts a background loop. |
| POST | `/api/ingest/mock?n=15&seed=42` | Synthetic pipeline run (no credits needed) |
| GET  | `/api/stats` | Pipeline aggregates + last-run stats |
| GET  | `/api/review/queue?limit=25` | Unlabeled items, sorted by uncertainty |
| POST | `/api/review/{id}/label` | `{label, category, notes, labeler_id}` |
| GET  | `/api/review/stats` | Counts of labeled/unlabeled/approved/rejected |
| GET  | `/api/ml/metrics` | Recent metric history per model |
| POST | `/api/ml/retrain` | Trigger retrain pass |
| WS   | `/ws` | Real-time feed updates |
| GET  | `/docs` | Swagger UI |

---

## Tech choices and the trade-offs

| Decision | Rationale |
|---|---|
| **FastAPI** | Async-native, OpenAPI for free, easy WebSocket support, lifespan context manager for the background-task pattern. |
| **SQLite default / Postgres prod** | Zero-setup local; switch via `DATABASE_URL=postgresql://...`. Single-connection lock doesn't matter at this scale. |
| **scikit-learn RandomForest** | CPU-friendly, fast retrain, decent baseline, easy to swap. The seed dataset is synthetic so F1 is ~0.5; retrain-on-labels is what makes it better. |
| **DistilBERT (optional)** | Stronger bot detection, ~250 MB. Lazy-loaded so 1.6 GB RAM boxes still work. Default: not loaded. |
| **sentence-transformers `all-MiniLM-L6-v2`** | 80 MB embedding model, great quality/size ratio for the relevance centroid. |
| **MinHash dedup** | O(n) near-dup detection on streaming input. No O(n²) comparisons. Disabled if `datasketch` isn't installed (warning logged). |
| **Celery for retraining** | Production-scale; `BackgroundTasks` in dev is enough. |
| **SWR** | Polling + cache + revalidation; replaces well with WebSocket later. |
| **TwitterAPI.io (not X direct)** | Easier signup than waiting for X API approval. Caveats: free tier caps at 1 request / 5 seconds, credits expire. The two background tasks are independently tunable so you can disable real-ingest without losing the mock autoseed. |
| **Multi-keyword curated queries** | Empirically single-word queries (`breaking`, `world`) return 0 tweets. Multi-keyword ORs return 15-30. The downstream pipeline still filters aggressively. |
| **`min_faves:N` at API layer** | Cheapest place to filter. Drops low-engagement noise before we ever see it. |
| **Known-news-handle boost (JSON, not hardcoded)** | Editors curate `data/known_news_handles.json` without code changes. Bumped +0.2 → +0.30 because Muse from @AIatMeta was originally at MEDIUM credibility despite being a known account. |
| **Stage 0 as opt-in topic gate** | Keeps general-news ingest possible (`software_focus_enabled=False`) while defaulting to AI/ML + software. |
| **Stage 3.5 (noise filter) separate from Stage 3 (bot)** | Humans can be noisy too. Opinion + engagement-bait ≠ bot. |
| **Stage 4 burst detection** | When 3+ similar tweets cluster in 5 min, the corroboration lifts credibility slightly (corroborated events > single-source claims). |
| **Kiosk-mode mock autoseed** | When credits are out, the feed still has *something* on the dashboard. The check uses the same filter as `/api/feed` so "healthy" matches what's actually visible. |
| **Two background tasks for ingest (mock + real)** | Independently tunable intervals and enables. Real-ingest can be disabled in prod (paid-plan-only) without losing the mock fallback. |
| **QPS-aware delay between beats** | TwitterAPI.io's free tier is 1 req / 5 sec. 7-second gap gives margin. |
| **`Pipeline` constructor injects per-stage config** | Tests can swap in a `SoftwareFocusFilter(known_accounts_path=tmp)` without touching global settings. |
| **Settings loaded via pydantic-settings** | 12-factor: env vars override `.env` override code defaults. |

---

## Quick start

```bash
git clone https://github.com/krish191204/CleanND1.git
cd CleanND1/backend
pip install --break-system-packages -r requirements.txt

# Copy the env template, set your twitterapi.io key
cp .env.example .env
# edit .env: TWITTER_API_KEY=your_key (the dev key in this repo is gitignored)

# Train the initial bot classifier (uses synthetic seed data)
python -m scripts.train_initial_model

# Start the API + background tasks
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Dashboard: `http://localhost:8000/`. API: `http://localhost:8000/docs`.

### "I want real news on the dashboard"

The system runs `ai_news`, `china_ai`, and `tech` automatically every 5 min. To force a
one-shot refresh:

```bash
curl -X POST http://localhost:8000/api/ingest \
  -H 'Content-Type: application/json' \
  -d '{"beat":"ai_news","max_results":25}'
```

### "I don't have API credits"

The mock auto-seeder runs every 60s, topping up the feed with synthetic software-sphere
tweets. The `+15 mock` button in the dashboard does the same on-demand. The full pipeline
(Dashboard + Review queue + Retrain) is exercisable end-to-end with mock data.

---

## Files of interest

```
backend/
  app/
    config.py                            # env loading + 30+ settings
    main.py                              # FastAPI entrypoint + 3 background tasks
    pipeline/
      base.py                            # Stage[I, O] / StageResult interface
      stage_software_focus.py            # Stage 0 — topical gate
      stage1_api_filter.py
      stage2_text_clean.py
      stage3_bot_detect.py               # RF + heuristic + optional DistilBERT
      stage3b_noise.py                   # Stage 3.5 — opinion / engagement-bait
      stage4_relevance.py                # sentence-transformers + burst
      stage5_credibility.py              # known-news-handle boost + domain whitelist
      orchestrator.py                    # Pipeline.run + active-learning gate
    services/
      db.py                              # SQLAlchemy wrapper
      twitter_client.py                  # twitterapi.io HTTP client + NEWS_QUERIES
      review_queue.py                    # queue facade
      cards.py                           # ScoredTweet → NewsCard
    api/
      routes.py                          # all REST endpoints + _run_mock_ingest + _ingest_real_to_db helpers
      websocket.py                       # live feed broadcaster
    ml/
      features.py                        # shared feature extractor
      train_bot.py                       # seed data + RF trainer
      retrain.py                         # pulls labels, refits, records metrics
      active_learning.py                 # uncertainty + diversity sampling
      celery_app.py                      # nightly beat (prod)
    models/
      schemas.py                         # Pydantic contracts
      db_models.py                       # SQLAlchemy ORM
  data/
    known_software_accounts.json         # Stage 0 whitelist
    known_news_handles.json              # Stage 5 credibility boost (77 handles)
  scripts/
    train_initial_model.py               # bootstrap trainer
  tests/
    test_api.py                          # 41 passing + 1 pre-existing failure
    test_pipeline.py
    test_software_focus.py
frontend/
  app/page.tsx                           # tab container
  components/                            # NewsCard, FeedList, ReviewQueueView, ...
  lib/api.ts                              # typed client
infra/
  docker-compose.yml
  Dockerfile.backend
```

---

## Running the test suite

```bash
cd backend
pytest -q
```

Current state: **41 passed, 1 pre-existing failure** in
`test_software_focus.py::test_from_settings_classmethod` (the test assumes
`get_settings.cache_clear()` lets mutated settings propagate, but Pydantic-settings
re-reads from `.env` on cache-clear — fix in a separate pass).

---

## What's running right now

| Component | State |
|---|---|
| Backend | uvicorn on `:8000` (PID `ba4xktbrm`) |
| Mock auto-seed | running (60s interval, feed is healthy so skipping) |
| Real-ingest poller | running (5 min interval, 7s QPS gap, 3 beats) |
| API key | twitterapi.io free-tier (`new1_4bea...d455`) — 1 req / 5 sec |
| Feed | ~60 real-news items: Muse, GPT-5.6 Sol, gpt-live, robostral, Grok 4.5, AnthropicAI export-controls, etc. |
| Review queue | thousands of borderline items (active-learning accumulators) |

---

## Roadmap

### MVP (✅ done)
- 6-stage pipeline (Stage 0 software-focus + Stages 1-5 + Stage 3.5 noise filter)
- Real + mock ingest (HTTP endpoint + background poller + mock auto-seeder)
- FastAPI with full REST surface
- SQLite persistence, Next.js dashboard (Feed / Review / Metrics tabs)
- Active-learning selection + label endpoint
- Initial bot-classifier training script (bootstrap F1 ~0.5)
- Retraining pipeline that ingests human labels
- Curated queries for `ai_news`, `china_ai`, `tech`, plus `breaking`/`world`/`finance`/`science`
- Known-news-handle whitelist (77 handles, +0.30 credibility boost)
- Background poller with QPS-aware delays and per-cycle flood guard

### v1
- Train the DistilBERT bot classifier on a labelled real-world corpus (currently disabled by default to save memory)
- Postgres + Alembic migration
- Auth (reviewers only)
- Per-user label history & agreement stats
- Burst-driven query expansion: when many tweets cluster on a topic, auto-poll for more on that topic
- Better MinHash dedup (hashed clustering → event IDs)

### v2
- Multi-source ingestion (Mastodon, Bluesky, RSS, Reddit)
- Claim-level extraction + LLM-assisted fact-check overlay
- Topic-aware retrieval (dense + BM25 hybrid) for "related coverage"
- Personalization with on-device preference vectors
- Trust graph: per-source reliability over time
