"""Feature extraction helpers shared by training + inference."""
from __future__ import annotations

import re
from typing import Any

import numpy as np

from ..models.schemas import CleanedTweet

_SPAM_PATTERNS = [
    r"\b(?:buy now|click here|free\s+money|make\s+\$?\d+\s*(?:/|\s+per)?\s*(?:day|hour|week))\b",
    r"\b(?:dm\s+me|dm\s+for|link\s+in\s+bio|telegram\s+me)\b",
    r"\b(?:onlyfans|sex\s+for|crypto\s+signal|pump\s+signal)\b",
    r"(?:🚀){3,}",
    r"(?:💰){2,}",
]


def extract_bot_features(ct: CleanedTweet | dict[str, Any]) -> list[float]:
    """Identical to stage3._extract_features but standalone for training.

    Accepts either a CleanedTweet or a dict with the same field names.
    """
    if isinstance(ct, dict):
        raw = ct.get("raw", {})
        clean_text = ct.get("clean_text", "")
        tokens = ct.get("tokens", [])
    else:
        raw = ct.raw.model_dump()
        clean_text = ct.clean_text
        tokens = ct.tokens

    text = raw.get("text", "")
    n_chars = max(len(text), 1)
    n_tokens = max(len(tokens), 1)

    feats = [
        len(raw.get("hashtags", [])) / n_chars * 100,
        len(raw.get("urls", [])) / n_chars * 100,
        len(raw.get("mentions", [])) / n_chars * 100,
        text.count("!") / n_chars * 100,
        sum(1 for c in text if c.isupper()) / n_chars,
        sum(1 for c in text if c.isdigit()) / n_chars,
        sum(1 for c in text if ord(c) > 0x1F000),
        raw.get("author_followers", 0),
        np.log1p(raw.get("author_followers", 0)),
        raw.get("author_following", 0),
        (raw.get("author_following", 0) / max(raw.get("author_followers", 1), 1)),
        1.0 if not (raw.get("author_description") or "").strip() else 0.0,
        1.0 if not raw.get("author_profile_image_url") else 0.0,
        int(raw.get("author_verified", False)),
        raw.get("like_count", 0) + raw.get("retweet_count", 0) + raw.get("reply_count", 0) + raw.get("quote_count", 0),
        np.log1p(
            raw.get("like_count", 0) + raw.get("retweet_count", 0) +
            raw.get("reply_count", 0) + raw.get("quote_count", 0)
        ),
        (raw.get("like_count", 0) / max(raw.get("author_followers", 1), 1)) if raw.get("author_followers") else 0.0,
        (raw.get("retweet_count", 0) / max(raw.get("like_count", 1), 1)),
        sum(len(re.findall(p, text, re.IGNORECASE)) for p in _SPAM_PATTERNS),
        len(set(tokens)) / n_tokens if tokens else 0.0,
        1.0 if raw.get("lang") is None else 0.0,
    ]
    return feats