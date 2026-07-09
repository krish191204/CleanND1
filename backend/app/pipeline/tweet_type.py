"""Heuristic tweet-type classifier (Layer B Addition 4).

Assigns one of five `TweetType` values to a scored tweet based on:

  - Is the author a known-news / known-software / known-individual handle?
  - Does the tweet text contain release-launch language?
  - Does it contain opinion / hedging markers?
  - Is it long with technical vocabulary?

This is a heuristic — it's not ML-grade. It's good enough to drive UI
badges ("Announcement" / "Opinion" / "Report" / "Analysis") and to let
users filter within a topic ("show me only the opinions on this GPT-5 release").
"""
from __future__ import annotations

import re

from ..models.schemas import ScoredTweet, TweetType
from ..services.known_handles import (
    is_known_individual,
    is_known_news,
    is_known_software,
)


# Release-launch language — matches stage3b_noise's product_announce keywords
# plus a few extras (release, available, today we).
_RELEASE_LANG_TOKENS = (
    "releasing",
    "announcing",
    "introducing",
    "today we",
    "we built",
    "now available",
    "we're launching",
    "now in",
    "shipped",
    "launched",
    "is now available",
)

# Opinion / first-person-hedging language.
_OPINION_MARKERS = (
    "i think",
    "my take",
    "hot take",
    "unpopular opinion",
    "honestly",
    "in my view",
    "imo ",
    "imho ",
    "fwiw ",
    "tldr ",
    "my read ",
)

# Tech vocabulary for the ANALYSIS bucket.
_TECH_VOCAB = {
    "model", "api", "benchmark", "training", "inference", "transformer",
    "embedding", "weights", "gradient", "kernel", "tensor", "loss",
    "dataset", "epoch", "fine-tuning", "finetuning", "rag",
    "agent", "agents", "latency", "throughput", "rlhf",
}


def classify_tweet_type(scored: ScoredTweet) -> TweetType:
    """Return a `TweetType` for this scored tweet. Mutates `scored.tweet_type`."""
    text = (scored.raw.text or "").lower()
    handle = scored.raw.author_handle or ""

    is_news_handle    = is_known_news(handle)
    is_software_handle = is_known_software(handle)
    is_indiv_handle   = is_known_individual(handle)
    is_release_author = is_news_handle or is_software_handle or is_indiv_handle

    has_release_lang = any(tok in text for tok in _RELEASE_LANG_TOKENS)
    has_opinion_lang = any(m in text for m in _OPINION_MARKERS)

    # ANNOUNCEMENT: known-news/known-software author + release language.
    # A known-individual authoring what looks like a release announcement
    # also qualifies (they might be announcing their own framework).
    if has_release_lang and (is_news_handle or is_software_handle):
        scored.tweet_type = TweetType.ANNOUNCEMENT
        return TweetType.ANNOUNCEMENT
    if has_release_lang and is_indiv_handle and len(text) > 200:
        # A long tweet from a known researcher with release-style language
        # is also an announcement (e.g. releasing their own paper).
        scored.tweet_type = TweetType.ANNOUNCEMENT
        return TweetType.ANNOUNCEMENT

    # OPINION: known-individual OR first-person hedging language.
    if is_indiv_handle or has_opinion_lang:
        scored.tweet_type = TweetType.OPINION
        return TweetType.OPINION

    # ANALYSIS: long tweet with tech vocabulary and no strong opinion markers.
    if len(text) > 200 and _looks_technical(text):
        scored.tweet_type = TweetType.ANALYSIS
        return TweetType.ANALYSIS

    # NEWS_REPORT: known-news author without release language.
    if is_news_handle:
        scored.tweet_type = TweetType.NEWS_REPORT
        return TweetType.NEWS_REPORT

    scored.tweet_type = TweetType.UNKNOWN
    return TweetType.UNKNOWN


def _looks_technical(text: str) -> bool:
    """True if `text` contains at least 3 tech-vocabulary tokens."""
    hits = sum(1 for v in _TECH_VOCAB if re.search(rf"\b{re.escape(v)}\b", text))
    return hits >= 3
