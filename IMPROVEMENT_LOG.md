# Improvement Log

Autonomous self-improvement loop for CleanND. Three parallel sub-agents work
on scoped remits; coordinator (this Claude session) merges when each branch
passes the test suite.

**Baseline:** 60 passed / 1 pre-existing failure (`test_giveaway_rejected` — out of scope per the
no-touch-the-known-broken-test constraint; the test fails on a regex assertion
unrelated to any of this work).

**Shared constraints:**
- Never touch `test_from_settings_classmethod` (pre-existing known failure).
- Never hardcode the API key (it lives in `backend/.env`).
- Never lower `REAL_INGEST_QUERY_DELAY_SECONDS` below 5.
- Never remove a config flag without `grep -r "flag_name" app/` confirming it is unused.
- `uvicorn` must remain launchable from `backend/` with no flags beyond `--host` and `--port`.
- After any change to `known_handles.py`, add or update a `cache_clear()` call in the affected test fixture.

**Stop conditions:**
- Total passing tests drop below baseline (60) → revert + log `REVERTED`.
- 20 total commits across all branches → pause + print log + wait for human.
- An import error in `app/main.py` a sub-agent can't fix in one iteration → revert that agent only.

---

## Log

| Agent | Iteration | Problem | Fix | Metric before → after |
|-------|-----------|---------|-----|----------------------|
| prep  | bb70643  | NewsCard `topic_id` was implicit None | Wire `topic_id=st.cluster_id` in to_card() | baseline locked at 60 passed |
| A | iter-1 | `cards.to_card()`, `credibility_color()`, `stage3b_noise.credibility_penalty()`, `stage5_credibility._host_of()` / `_load_known_news_handles()`, and `Pipeline._uncertainty_margin()` had no direct unit tests — regressions could silently break the dashboard headline/URL/why_shown mapping, the burst badge, or the noise-penalty tier logic | Added 10 tests in `backend/tests/test_coverage_a.py`: topic_id propagation, headline sentence-split, status-URL fallback, burst_in_why_shown, credibility_color mapping (4 levels), 4-tier penalty boundaries, www-prefix/lowercase URL parsing, comment-key JSON skip, missing-file fallback, max-margin-at-(0.5, 0.5) | 60 passed → 70 passed (+10) |
| (awaiting sub-agent B) | | | | |
| (awaiting sub-agent C) | | | | |
