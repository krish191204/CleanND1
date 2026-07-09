# CleanND — Project State

Real-time, AI/software-focused news aggregator. Pulls posts from X/Twitter
(via `twitterapi.io`), runs each through a **6-stage cleaning pipeline**,
groups the survivors into **topic clusters**, and serves a topic-grouped
feed on a Next.js dashboard at `http://localhost:8000/`.

This file describes the project as it stands right now so any new session
can pick up without re-reading every commit.

---

## What was built in this session (in order)

| Phase | What landed | Commit |
|---|---|---|
| Initial | 6-stage pipeline + FastAPI + dashboard + mock auto-seeder | `8b3b0ae` |
| Real ingest | better queries, known-news-handle boost (+0.30), real-ingest poller, `?view=flat` compat | `f7b12b9` |
| Kiosk | mock autoseed (kiosk mode) — every 60s, top up if < 5 items | `c216c2e` |
| README | full README rewrite with motivation, 10-layer defense table, runtime state | `119cd56` |
| 6 recall fixes | Issue 1 (parallel `from:Handle` query), 2 (Stage 0 known-news bypass), 3 (Stage 3.5 soft penalty), 4 (MinHash dedup with corroboration_group_id), 5 (per-beat persist cap + known-handle priority), 6 (burst credit + corroboration_group_id consumption) | `d37458f` |
| Product pivot | 6 topic-clustering additions: known-individuals whitelist, Stage 0/3/3.5 bypass for individuals, `TweetType` classifier, reactive expansion, `TopicORM` + migration, `/api/topics` endpoints, frontend `TopicFeedList` + topic detail | `d37458f` |
| Tuning | fix embedding propagation to `ScoredTweet`, fix `dict(rows)` 3-tuple, fix ISO-string vs `DateTime` in `cluster_and_persist`, lower `clustering_distance_threshold` to 0.65, add near-duplicate mock templates | `bb97146` |
| Frontend | inline topic expansion (compatible with `output: 'export'` static build) | `e3d4f04` |
| UX | `is_clustered` flag on `NewsCard` + "Single source" warning chip + tweet_type badge in UI | uncommitted (just landed) |

---

## Current state of the system

**API key:** `new1_2e56d7a22e144e2a8768f68a0231c8ca` (in `backend/.env` line 3).
twitterapi.io free tier — capped at 1 request / 5 seconds.

**Running:** `cd backend && python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000`
(Cmd-Shift-R in the browser to pick up frontend rebuilds.)

**Background tasks in `app/main.py` lifespan:**
- `_ws_pusher` — WebSocket broadcast loop
- `_mock_autoseed_task` — every 60s, top up if surfaced feed < 5 items
- `_real_ingest_task` — every 5 min, runs 3 curated queries (ai_news, china_ai, tech) with 7s QPS gap

**Tests:** 60 passing / 1 pre-existing failure (test_from_settings_classmethod — settings cache issue, not ours).

---

## Architecture

```
backend/
  app/
    config.py                            # pydantic-settings, ~30 flags
    main.py                              # FastAPI entrypoint + 3 lifespan tasks
    pipeline/
      orchestrator.py                    # wires stages 0→5 in order
      stage_software_focus.py            # Stage 0 — known-news bypass
      stage1_api_filter.py
      stage2_text_clean.py               # MinHash dedup + corroboration_group_id
      stage3_bot_detect.py               # known-handle bot_score=0 bypass
      stage3b_noise.py                   # soft-penalty for known handles
      stage4_relevance.py                # burst credit + corroboration consumption
      stage5_credibility.py              # known-news_handle boost (Layer B)
      topic_grouper.py                   # AgglomerativeClustering (Layer B)
      tweet_type.py                      # heuristic tweet-type classifier
    services/
      db.py                              # SQLAlchemy Database wrapper + accessors
      known_handles.py                   # singleton cache for 3 whitelists
      twitter_client.py                  # twitterapi.io wrapper + NEWS_QUERIES
      topic_grouper_wrapper.py           # cluster+classify+persist glue + reactive expansion
      review_queue.py, cards.py
    api/
      routes.py                          # all REST endpoints
    models/
      schemas.py                         # Pydantic contracts (incl. TweetType, TopicSummary)
      db_models.py                       # SQLAlchemy ORM (incl. TopicORM)
  data/
    known_software_accounts.json         # Stage 0 whitelist
    known_news_handles.json              # Stage 5 boost + Stage 0 bypass
    known_credible_individuals.json       # Stage 0/3/3.5 bypass + tweet_type=OPINION
frontend/
  app/page.tsx                           # tabs: Topics | Flat | Review | Metrics
  components/TopicFeedList.tsx           # topic cards + inline expansion
  components/NewsCard.tsx                # adds "Single source" + tweet_type chips
  lib/api.ts                             # typed client (NewsCard.is_clustered, tweet_type, topic_id)
```

### 6-stage pipeline

Stage 0 (software focus) → Stage 1 (API filter) → Stage 2 (text clean + MinHash dedup) → Stage 3 (bot detect) → Stage 3.5 (noise filter) → Stage 4 (relevance + burst) → Stage 5 (credibility) → topic clustering → DB upsert.

### Singleton: `app/services/known_handles.py`

Module-level singleton with `@functools.cache`-decorated loaders. Reads paths from `os.environ` first (so test monkeypatch works) then falls back to pydantic-settings. Exposes `is_known_news`, `is_known_software`, `is_known_individual`, `is_known_any`, plus `known_news_handles()` etc. for building the parallel query.

### Topic clustering (Layer B Addition 1)

`topic_grouper.cluster_tweets()` runs `sklearn.AgglomerativeClustering` on the Stage 4 embeddings with cosine-distance threshold (default 0.65). Each cluster gets a TF-IDF label. Singletons stay as solo cards. `topic_grouper_wrapper.cluster_and_persist` ties this to DB upsert + tweet_type classification. Reactive expansion fires fire-and-forget ingests for any cluster with ≥ 2 tweets whose last_expansion_at is older than the cooldown (1 hour default).

---

## Key config flags (in `config.py` + `.env`)

| Flag | Default | What it does |
|---|---|---|
| `MOCK_AUTO_SEED_ENABLED` | true | mock auto-seeder on/off |
| `REAL_INGEST_ENABLED` | true | real-ingest poller on/off |
| `REAL_INGEST_INTERVAL_SECONDS` | 600 | 10 min polling cycle |
| `REAL_INGEST_QUERY_DELAY_SECONDS` | 7 | pause between beats (twitterapi.io 1-req/5s limit) |
| `REAL_INGEST_QUERIES` | `[ai_news, china_ai, tech]` | beats the poller cycles through |
| `stage2_skip_dedup_for_known_handles` | true | Issue 4: keep near-dup twins if both known |
| `noise_skip_known_handles` | true | Issue 3: known handles bypass Stage 3.5 reject |
| `known_handle_burst_credit` | 2 | Issue 6: known-handle tweets count as N toward burst |
| `bypass_stages_for_known_individuals` | true | Layer B: karpathy/ylecun/... bypass Stage 0/3/3.5 |
| `clustering_enabled` | true | Layer B |
| `clustering_distance_threshold` | 0.65 | cosine distance cutoff |
| `clustering_min_cluster_size` | 2 | below this → singleton |
| `reactive_topic_expansion_enabled` | true | Layer B: fire-and-forget on cluster |
| `reactive_expansion_cooldown_seconds` | 3600 | per-topic cooldown |

---

## DB schema (5 tables)

```sql
tweets(
  id PK, author_id, author_handle, text, clean_text, lang,
  created_at, processed_at, bot_score, bot_label,
  relevance_score, quality_score, credibility_score, credibility_level,
  final_score, passed_all_stages, software_focus_passed, software_focus_meta,
  embedding JSON, payload JSON,
  topic_id, tweet_type
)
topics(
  id PK (UUID), label, anchor_tweet_id,
  first_seen_at, last_activity_at, tweet_count, extras JSON,
  last_expansion_at
)
reviews(
  id PK, tweet_id, snapshot JSON, model_bot_score, model_credibility,
  model_relevance, uncertainty_margin, label, category, notes,
  labeler_id, labeled_at, created_at
)
feedback(
  id PK, tweet_id, signal, category, notes, user_id, snapshot JSON, created_at
)
model_metrics(
  id PK, model_name, version, metric_name, metric_value, sample_size,
  recorded_at, extras JSON
)
```

**Migration:** `Database.init()` runs `PRAGMA table_info(tweets)` to check which columns exist, then issues `ALTER TABLE tweets ADD COLUMN ...` for `topic_id` and `tweet_type` if missing. Idempotent.

---

## Known bugs and gotchas

1. **datasketch 2.0+ requires `scheme="affine32"` (or `"lean"`)** when constructing MinHash from existing hash values. Bytes-roundtrip loses scheme metadata. The Stage 2 code stores the live `MinHash` object directly on `CleanedTweet.minhash_object` (excluded from serialization via `Field(exclude=True)`).
2. **scoped settings cache** — `get_settings` is `@lru_cache(maxsize=1)`. Test fixtures that monkeypatch env vars need to also call `get_settings.cache_clear()`, otherwise the singleton's path lookup ignores the patched values. `known_handles.py` works around this by reading `os.environ` directly.
3. **cwd matters** — uvicorn must be launched from `backend/`. The `Database` uses `sqlite:///./data/cleannd.db` (relative path). `--app-dir backend` works too but cwd-based is more reliable.
4. **twitterapi.io free tier QPS** — 1 request / 5 seconds. `real_ingest_query_delay_seconds=7` is the safe margin. Burst through the rate limit and you get 429s.
5. **The mock generator** uses a fixed pool of 14 templates + 2 near-duplicate Claude templates. With `clustering_distance_threshold=0.65` you get 1-2 small clusters per 8-surfaced batch. The rest are singletons — flagged "Single source" in the UI.
6. **Pre-existing test failure** — `test_from_settings_classmethod` fails because Pydantic-settings re-reads from `.env` on `cache_clear()`, so the test's mutated settings don't propagate. Outside the scope of any work done in this session.

---

## How to develop

```bash
# Backend
cd backend
pip install --break-system-packages -r requirements.txt
python -m scripts.train_initial_model        # bootstrap the bot classifier
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000

# Frontend
cd frontend
npm install
npm run dev          # dev server on :3000
# OR
npm run build        # static export to ./out (served by FastAPI on /)

# Tests
cd backend
MOCK_AUTO_SEED_ENABLED=false REAL_INGEST_ENABLED=false python3 -m pytest -q

# Manual demo
curl -X POST http://localhost:8000/api/ingest/mock?n=30
curl http://localhost:8000/api/topics
curl http://localhost:8000/api/feed
```

---

## When you need to make changes

- **Add a new pipeline stage** → add a `process()` method to a new class in `app/pipeline/`, wire it into `app/pipeline/orchestrator.py`
- **Add a new field to tweets table** → add to `app/models/db_models.py:TweetORM`, add a migration in `Database.init()` (PRAGMA + ALTER TABLE pattern), add to `_serialize_tweet` in `db.py`
- **Add a new beat / query** → add to `app/services/twitter_client.py:NEWS_QUERIES`, optionally add to `real_ingest_queries` in `.env`
- **Tune a stage** → the relevant file is the one in `app/pipeline/stageN_*.py`. Most have a `__init__(...)` with tunable params; pass them through `Pipeline.__init__` in `orchestrator.py`
- **Add a new API endpoint** → `app/api/routes.py`, decorated with `@router.get` / `@router.post`
- **Tweak the dashboard** → `frontend/app/page.tsx` for the nav/tabs, `frontend/components/TopicFeedList.tsx` for topic cards, `frontend/components/NewsCard.tsx` for individual cards, `frontend/lib/api.ts` for type definitions

---

## Git log (relevant)

```
e3d4f04  fix(frontend): inline topic expansion (compatible with output:export)
bb97146  fix: topic clustering — propagate embedding, fix datetime, fix dict
d37458f  feat: 6 recall fixes + topic-clustering product pivot
119cd56  docs: full README rewrite — architecture, motivation, runtime state
fdae2a3  tune: switch to working API key + tighten china_ai + 7s QPS gap
5ffe666  tune: credit-conscious real-ingest + add china_ai beat
f7b12b9  feat: real-news ingestion via better queries + known-handle boost + auto-poller
3699f39  docs: clarify API key handling in README
c216c2e  feat: kiosk-mode mock auto-seed keeps the dashboard populated
8b3b0ae  chore: initial commit + align mock + README with 6-stage pipeline
```

---

## The full motivation (from the user's "why" question)

The original complaint was: *"Meta Muse and China AI ban stories don't surface in the feed."* The structural fix was: the front door (queries) was too narrow, no background poller for the real API, and known-news-handle credibility boost was underweighted. The product pivot to topic clustering came from: *"how do we get important news without flooding with BS?"* — the answer is multi-layer filtering (10 layers, layers 0-9) plus the topic grouping that flags unclustered tweets as "Single source" so users can tell at a glance which items have corroboration.
