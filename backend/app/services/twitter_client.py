"""HTTP client for twitterapi.io — turns API JSON into RawTweet."""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import AsyncIterator, Iterable, Optional

import httpx
from loguru import logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ..config import get_settings
from ..models.schemas import RawTweet


class TwitterAPIError(Exception):
    """Raised on non-2xx responses."""


class TwitterClient:
    """Thin async wrapper around twitterapi.io's REST endpoints."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 30.0,
    ) -> None:
        s = get_settings()
        self.api_key = api_key or s.twitter_api_key
        self.base_url = (base_url or s.twitter_api_base).rstrip("/")
        self.header_name = s.twitter_api_key_header
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    # ------------------------------------------------------------------
    async def _client_(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
                headers={self.header_name: self.api_key, "Accept": "application/json"},
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    @retry(
        retry=retry_if_exception_type((httpx.HTTPError, TwitterAPIError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def _get(self, path: str, params: Optional[dict] = None) -> dict:
        client = await self._client_()
        resp = await client.get(path, params=params or {})
        if resp.status_code >= 400:
            raise TwitterAPIError(f"{resp.status_code} {resp.text[:200]}")
        try:
            return resp.json()
        except Exception as e:
            raise TwitterAPIError(f"bad json: {e}") from e

    # ------------------------------------------------------------------
    async def search_tweets(
        self,
        query: str,
        max_results: int = 100,
        cursor: Optional[str] = None,
        search_type: str = "Top",
    ) -> list[RawTweet]:
        """
        GET /twitter/tweet/advanced_search on twitterapi.io.

        `query` is a full advanced-search query string, e.g.
          "breaking news lang:en -filter:replies min_faves:10"
        """
        params: dict[str, str | int] = {"query": query, "queryType": search_type}
        if cursor:
            params["cursor"] = cursor
        if max_results:
            params["limit"] = min(max_results, 100)

        data = await self._get("/twitter/tweet/advanced_search", params)
        tweets_raw = data.get("tweets", data.get("data", []))
        out: list[RawTweet] = []
        for t in tweets_raw[:max_results]:
            try:
                out.append(self._to_rawtweet(t))
            except Exception as e:
                logger.warning(f"failed to parse tweet: {e}")
        return out

    async def stream_topic(
        self,
        query: str,
        poll_seconds: int = 60,
        max_iterations: int = 10,
    ) -> AsyncIterator[list[RawTweet]]:
        """Simple polling loop - for a real-time stream use webhook/SSE later."""
        cursor: Optional[str] = None
        for _ in range(max_iterations):
            tweets = await self.search_tweets(query, max_results=100, cursor=cursor)
            if tweets:
                yield tweets
            cursor = tweets[-1].raw.get("id_str") if tweets else cursor
            await asyncio.sleep(poll_seconds)

    async def get_user(self, handle: str) -> dict:
        return await self._get("/twitter/user/info", {"userName": handle.lstrip("@")})

    # ------------------------------------------------------------------
    @staticmethod
    def _to_rawtweet(d: dict) -> RawTweet:
        """Map a twitterapi.io tweet JSON to RawTweet.

        The exact field names depend on the API tier; we map the common subset
        and stuff anything else into `raw` for inspection.
        """
        author = d.get("author") or d.get("user") or {}
        created = d.get("createdAt") or d.get("created_at") or d.get("timestamp")
        if isinstance(created, (int, float)):
            ts = datetime.fromtimestamp(created)
        elif isinstance(created, str):
            try:
                # twitterapi.io format: 2025-01-01T12:00:00.000Z
                ts = datetime.fromisoformat(created.replace("Z", "+00:00"))
            except Exception:
                ts = datetime.utcnow()
        else:
            ts = datetime.utcnow()

        return RawTweet(
            id=str(d.get("id") or d.get("id_str") or d.get("tweet_id")),
            text=d.get("text") or d.get("full_text") or "",
            author_id=str(author.get("id") or author.get("id_str") or ""),
            author_handle=author.get("userName") or author.get("screen_name") or "",
            author_display_name=author.get("name") or author.get("display_name") or "",
            author_followers=int(author.get("followers") or author.get("followers_count") or 0),
            author_following=int(author.get("following") or author.get("following_count") or 0),
            author_verified=bool(author.get("isVerified") or author.get("verified")),
            author_created_at=None,
            author_profile_image_url=author.get("profilePicture"),
            author_description=author.get("description"),
            lang=d.get("lang"),
            created_at=ts,
            hashtags=[
                h.get("text", "") if isinstance(h, dict) else str(h)
                for h in (d.get("entities", {}).get("hashtags", []) or [])
            ] or re_list(d.get("hashtags")),
            urls=[
                u.get("expanded_url", u.get("url", ""))
                if isinstance(u, dict)
                else str(u)
                for u in (d.get("entities", {}).get("urls", []) or [])
            ],
            mentions=[
                m.get("screen_name", "") if isinstance(m, dict) else str(m)
                for m in (d.get("entities", {}).get("user_mentions", []) or [])
            ],
            media=[
                m.get("media_url_https") or m.get("url", "")
                if isinstance(m, dict)
                else str(m)
                for m in (d.get("entities", {}).get("media", []) or d.get("media", []) or [])
            ],
            like_count=int(d.get("likeCount") or d.get("favorite_count") or 0),
            retweet_count=int(d.get("retweetCount") or d.get("retweet_count") or 0),
            reply_count=int(d.get("replyCount") or d.get("reply_count") or 0),
            quote_count=int(d.get("quoteCount") or d.get("quote_count") or 0),
            source=d.get("source"),
            raw=d,
        )


def re_list(x):
    if not x:
        return []
    if isinstance(x, list):
        return x
    if isinstance(x, str):
        return x.split()
    return []


# Convenience helper: query templates for common news beats.
#
# Each query combines:
#   - multi-keyword topical ORs (catches product names, model releases,
#     policy stories — single-word queries miss too much)
#   - `lang:en` to keep the feed English-only by default
#   - `min_faves:N` to drop low-engagement noise at the API layer
#
# NOTE on operators: twitterapi.io does NOT support the standard Twitter
# `-filter:replies` or `-filter:retweets` operators (returns 0 results).
# Only `lang:`, `min_faves:`, and `filter:verified` work. Reply/retweet
# filtering happens downstream in Stage 2 (MinHash dedup) + Stage 3 (bot
# detection). The pipeline has 9 more filtering layers downstream
# (Stage 0 software focus, bot detection, noise filter, credibility,
# surface floor, etc.) so these queries are deliberately generous —
# the layers do the rest.
NEWS_QUERIES = {
    "breaking": (
        "(AI OR \"machine learning\" OR OpenAI OR Anthropic OR Meta OR Google "
        "OR \"Claude\" OR \"GPT\" OR transformer OR \"Nvidia\" OR "
        "\"deep learning\" OR PyTorch OR kubernetes) "
        "lang:en min_faves:5"
    ),
    "world": (
        "(China OR \"European Union\" OR Russia OR Ukraine OR Israel OR Taiwan "
        "OR NATO OR \"United Nations\" OR sanctions OR election OR referendum) "
        "lang:en min_faves:5"
    ),
    "tech": (
        "(AI OR \"machine learning\" OR OpenAI OR Anthropic OR NVIDIA OR "
        "PyTorch OR kubernetes OR rustlang OR React) "
        "lang:en min_faves:5"
    ),
    "finance": (
        "(earnings OR IPO OR \"Federal Reserve\" OR \"interest rate\" OR "
        "\"stock market\" OR \"S&P\" OR Nasdaq OR inflation OR GDP OR "
        "\"central bank\") "
        "lang:en min_faves:5"
    ),
    "science": (
        "(study OR research OR arxiv OR neurips OR \"peer reviewed\" OR "
        "\"clinical trial\" OR Nature OR Science OR PNAS OR breakthrough) "
        "lang:en min_faves:5"
    ),
    # New: catch AI/ML product releases + lab announcements.
    "ai_news": (
        "(OpenAI OR Anthropic OR \"Claude\" OR \"GPT\" OR \"Meta AI\" OR "
        "\"Google DeepMind\" OR Mistral OR \"Hugging Face\" OR NVIDIA OR "
        "PyTorch OR \"image generation\" OR \"video model\") "
        "lang:en min_faves:3"
    ),
    # New: catch AI policy / regulation stories.
    "ai_policy": (
        "(regulation OR ban OR \"executive order\" OR \"AI safety\" OR "
        "\"AI act\" OR \"white house\" OR congress OR parliament OR "
        "\"EU AI\" OR \"export controls\") "
        "lang:en min_faves:3"
    ),
    "verified_only": "lang:en filter:verified",
}


async def quick_search(
    client: TwitterClient, beat: str, max_results: int = 50
) -> list[RawTweet]:
    q = NEWS_QUERIES.get(beat, beat)
    return await client.search_tweets(q, max_results=max_results)