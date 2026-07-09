"""Convert ScoredTweet -> NewsCard DTO for the frontend."""
from __future__ import annotations

import re
from datetime import datetime, timezone

from ..models.schemas import CredibilityLevel, NewsCard, ScoredTweet


_SENT_SPLIT = re.compile(r"(?<=[\.\!\?])\s+")


def to_card(st: ScoredTweet, human_verified: bool = False) -> NewsCard:
    """Headline-first summary, deterministic on the cleaned text."""
    text = st.clean.clean_text.strip() or st.raw.text.strip()
    sents = _SENT_SPLIT.split(text)
    headline = sents[0][:160] if sents else text[:160]
    summary = " ".join(sents[1:3]) if len(sents) > 1 else text[:280]

    url = ""
    if st.raw.urls:
        url = st.raw.urls[0]
    elif st.raw.author_handle:
        url = f"https://x.com/{st.raw.author_handle}/status/{st.raw.id}"

    why = []
    if st.clean.is_burst_event:
        why.append("trending_now")
    if st.clean.bot_score < 0.3:
        why.append("low_bot_probability")
    if any("verified" in r.lower() for r in st.credibility_reasons):
        why.append("verified_account")
    if any("known_news_handle" in r for r in st.credibility_reasons):
        why.append("known_news_handle")
    if any("whitelisted" in r.lower() for r in st.credibility_reasons):
        why.append("domain_whitelisted")
    if any("burst" in r.lower() for r in st.credibility_reasons):
        why.append("co_corroborated_burst")

    return NewsCard(
        id=st.raw.id,
        headline=headline,
        summary=summary,
        handle=st.raw.author_handle,
        display_name=st.raw.author_display_name or st.raw.author_handle,
        profile_image_url=st.raw.author_profile_image_url,
        verified=st.raw.author_verified,
        timestamp=st.raw.created_at,
        media=st.raw.media,
        credibility_level=st.credibility_level,
        credibility_score=st.credibility_score,
        human_verified=human_verified,
        why_shown=why,
        url=url,
    )


_CREDIBILITY_COLOR = {
    CredibilityLevel.HIGH: "#16a34a",       # green
    CredibilityLevel.MEDIUM: "#eab308",     # yellow
    CredibilityLevel.LOW: "#f97316",        # orange
    CredibilityLevel.UNVERIFIED: "#9ca3af",  # gray
}


def credibility_color(level: CredibilityLevel) -> str:
    return _CREDIBILITY_COLOR[level]