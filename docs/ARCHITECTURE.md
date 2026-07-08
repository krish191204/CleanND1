# CleanND Architecture (deep dive)

This is the long-form version of the architecture overview in the README.

## Pipeline stages — detail

### Stage 1: API filter
Cheap rejections *before* we burn CPU on text processing. Each rule is a single function
call and the stage is O(n) over a list.

| Rule | Default | Notes |
|---|---|---|
| `lang in allowed_languages` | en/es/fr/de/pt | cheap |
| `len(text) ≥ min_text_length` | 20 | empty/quoted-only |
| `len(text) ≤ max_text_length` | 1000 | paste-bombs |
| `author_followers ≥ min_followers` | 500 | new accounts |
| `account_age ≥ 30d` | 30 | newly created bots |
| `len(hashtags) ≤ max_hashtags` | 5 | spam |
| `len(urls) ≤ max_urls` | 2 | SEO spam |
| not `RT`/`QT` prefix | configurable | retweet/quote chains |
| not quote-only | configurable | low signal |

### Stage 2: Text cleaning
- unicode normalize (NFC)
- strip `rt`/`qt` prefix
- remove URLs (incl. `t.co/...`)
- remove `@mentions`
- keep hashtag *words* (`#breaking` → `breaking`)
- emoji → textual description (e.g. `🚀` → ` :rocket: `)
- lowercase + collapse whitespace
- tokenize (spaCy → NLTK → regex fallback)
- lemmatize
- compute MinHash signature for near-dup detection

**MinHash dedup:** we keep a bounded window of the last 200 MinHash signatures. New tweets
with Jaccard ≥ 0.85 to any past signature are rejected as duplicates. This is
O(window × num_perm) per new tweet, ~constant in practice.

### Stage 3: Bot detection (ensemble)
Three signals combined:

```
score = 0.5 · RF.predict_proba(features)
      + 0.3 · heuristic_score(features)
      + 0.2 · bert_score(text)        # if loaded
```

**Features (22):** hashtag %, URL %, mention %, exclamation %, caps %, digit %, emoji
count, followers, log1p(followers), following, follow/follower ratio, no-bio, no-avatar,
verified flag, engagement total, log1p(engagement), engagement/follower ratio,
retweet/like ratio, spam-regex hits, lexical-diversity ratio, lang-unknown flag.

**Heuristic** sums hand-tuned weights for obvious red flags (spam regex, all-caps,
hashtag/URL spam, no-bio, no-avatar, very low engagement, FF ratio > 5, low lexical
diversity). Caps at 1.0.

**Random Forest** is trained on the seed dataset by default; on real data the human-labelled
reviews take over via nightly retrain.

**DistilBERT** (optional) is loaded from `bert_model_path` if present. Uses the same
`[CLS]` head trained against the same label scheme.

**Thresholds:** bot_score ≥ 0.90 → BOT, 0.80–0.90 → LIKELY_BOT (rejected), 0.30–0.50 →
UNCERTAIN (passed through, but flagged for review), ≤ 0.30 → HUMAN.

### Stage 4: Relevance + burst

We embed each cleaned tweet with `sentence-transformers/all-MiniLM-L6-v2` (384-dim). If a
news centroid has been fit (`fit_news_centroid`), we use cosine similarity (squashed to
[0,1]). Otherwise a cheap keyword-density fallback assigns a base score plus bumps for
media and length.

**Burst detection** keys on a hash of the first 8 tokens; if the same key appears ≥ 4
times in the last 5 minutes, the tweet is flagged `is_burst_event = True` and its relevance
score is bumped to ≥ 0.6. This is a cheap proxy for "many accounts are talking about the
same thing" — corroborated news is more likely to be real.

**Quality** is a separate score: length window, media presence, log1p(engagement), verified
flag.

### Stage 5: Credibility
- **Source verification** — verified flag, known-news-handle list, follower count
- **Domain reliability** — whitelist (reuters.com, bbc.co.uk, …) and blacklist
- **Bot penalty** — `score -= 0.2 · bot_score`
- **Burst bonus** — `score += 0.10` if `is_burst_event` (cross-account corroboration)
- **Account age** — older = more credible

Final level: HIGH (≥ 0.75), MEDIUM (≥ 0.45), LOW (≥ 0.20), UNVERIFIED (< 0.20).
Items below 0.20 are rejected from the surfaced feed but kept in DB for stats.

**Composite** `final_score = 0.45·cred + 0.30·rel + 0.15·qual + 0.10·(1-bot)` — used for
ranking.

## Active learning

Three selection criteria are unioned:

1. **Bot borderline** — bot_score ∈ [0.55, 0.95] (i.e. rejected by the bot stage but
   uncertain)
2. **Credibility false-negatives** — rejected by Stage 5 but relevance ≥ 0.6 (these are
   news that the model under-credited)
3. **Uncertain survivors** — bot_label == UNCERTAIN

Items are sorted by `uncertainty_margin` (1 − 2·|p − 0.5| averaged across bot and cred).
Reviewers see the most informative first.

## Retraining

`POST /api/ml/retrain` or nightly Celery beat:
1. Pull all rows from `reviews` where `label IS NOT NULL`
2. Map `approved → 0 (human)`, `rejected → 1 (bot)`, skip `needs_more_info`
3. Reconstruct features from the snapshot
4. Optionally augment with the seed dataset if labelled < 200
5. Train-test split 80/20 (stratified), refit RF
6. Compute precision/recall/F1, record to `model_metrics`
7. Persist the new model to `ml/artifacts/bot_classifier.joblib`

The next pipeline run picks up the new model automatically (loaded at construction).

## Deployment

For production:

- `DATABASE_URL=postgresql://...` (run Alembic migrations)
- `REDIS_URL=redis://...` for Celery + caching
- Celery worker + beat for retraining and the live feed broadcaster
- nginx in front of the FastAPI app for TLS
- twitterapi.io (or official X API) on the ingestion side
- Prometheus + Grafana on `/metrics` (not yet implemented; hooks are in place)

`infra/docker-compose.yml` (below) is a working v1 target.

## Scaling

- Each stage is a pure function over a list — trivially parallelizable
  (Ray/Dask/subprocess pool).
- For streaming, wrap each stage with a Redis Streams consumer/producer
  (input → API filter → Redis → text clean → Redis → …).
- The bot classifier is a 200-tree RF — < 1 ms per tweet. The bottleneck is
  sentence-transformers; use a GPU box or batch at 32 tweets per encode call.
- Embeddings can be cached (id → vec) — 384 floats ≈ 1.5 KB; 1 M tweets ≈ 1.5 GB.
