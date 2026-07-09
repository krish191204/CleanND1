"""Stage 3: Bot & spam detection.

Ensemble of:
  (a) Hand-crafted account + content features -> RandomForest baseline.
  (b) Optional fine-tuned DistilBERT text classifier (loaded lazily).
  (c) Heuristic spam regex score.

Final bot_score = 0.5 * (a) + 0.3 * (c) + 0.2 * (b_if_loaded)
If no model is trained yet, we fall back to a heuristic-only score that is
still useful as a pre-filter.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import numpy as np
from loguru import logger

from ..config import get_settings
from ..models.schemas import BotPrediction, CleanedTweet
from .base import Stage, StageResult


_SPAM_PATTERNS = [
    r"\b(?:buy now|click here|free\s+money|make\s+\$?\d+\s*(?:/|\s+per)?\s*(?:day|hour|week))\b",
    r"\b(?:dm\s+me|dm\s+for|link\s+in\s+bio|telegram\s+me)\b",
    r"\b(?:onlyfans|sex\s+for|crypto\s+signal|pump\s+signal)\b",
    r"(?:🚀){3,}",
    r"(?:💰){2,}",
]


class BotDetector(Stage[CleanedTweet, CleanedTweet]):
    """Adds bot_score + bot_label to each cleaned tweet."""

    name = "stage3_bot_detect"

    def __init__(
        self,
        model_path: Optional[str] = None,
        bert_model_path: Optional[str] = None,
        reject_threshold: float = 0.50,
        uncertain_band: float = 0.20,
    ) -> None:
        super().__init__()
        settings = get_settings()
        self.model_path = Path(model_path or settings.bot_model_path)
        self.bert_model_path = Path(bert_model_path) if bert_model_path else None
        self.reject_threshold = reject_threshold
        self.uncertain_band = uncertain_band
        self._clf = None
        self._bert = None
        self._load_models()

    # ------------------------------------------------------------------
    def _load_models(self) -> None:
        # classical model
        if self.model_path.exists():
            try:
                import joblib

                self._clf = joblib.load(self.model_path)
                logger.info(f"[bot] loaded classifier from {self.model_path}")
            except Exception as e:  # pragma: no cover
                logger.warning(f"[bot] failed to load classifier: {e}")
        else:
            logger.info("[bot] no classifier yet — using heuristic-only scoring")

        # BERT (optional)
        if self.bert_model_path and self.bert_model_path.exists():
            try:
                from transformers import AutoTokenizer, AutoModelForSequenceClassification

                self._bert = (
                    AutoTokenizer.from_pretrained(self.bert_model_path),
                    AutoModelForSequenceClassification.from_pretrained(self.bert_model_path),
                )
                logger.info(f"[bot] loaded BERT from {self.bert_model_path}")
            except Exception as e:  # pragma: no cover
                logger.warning(f"[bot] failed to load BERT: {e}")

    # ------------------------------------------------------------------
    def process(self, items: list[CleanedTweet]) -> StageResult[CleanedTweet]:
        passed: list[CleanedTweet] = []
        rejected: list[tuple[CleanedTweet, str]] = []

        # Layer B Addition 3: known handles get bot_score=0 directly,
        # skipping the classifier entirely. They're verified humans; we
        # don't want the model to second-guess that on a noisy text signal.
        from ..services.known_handles import is_known_any

        for ct in items:
            # Known-handle bypass
            if is_known_any(ct.raw.author_handle):
                ct.bot_score = 0.0
                ct.bot_label = BotPrediction.HUMAN
                ct.bot_reasons = ["known_handle_bypass"]
                passed.append(ct)
                continue

            score, reasons = self._score(ct)
            ct.bot_score = float(np.clip(score, 0.0, 1.0))
            ct.bot_reasons = reasons

            # decide label
            if ct.bot_score >= 1.0 - self.reject_threshold:
                ct.bot_label = BotPrediction.BOT if ct.bot_score >= 0.90 else BotPrediction.LIKELY_BOT
                rejected.append((ct, f"bot_score={ct.bot_score:.2f}"))
                continue
            if ct.bot_score >= 0.5 - self.uncertain_band:
                ct.bot_label = BotPrediction.UNCERTAIN
                # pass through to next stages; review queue will pick it up later
            elif ct.bot_score <= 0.3:
                ct.bot_label = BotPrediction.HUMAN
            else:
                ct.bot_label = BotPrediction.LIKELY_HUMAN

            passed.append(ct)

        return StageResult(
            passed=passed,
            rejected=rejected,
            stats={"input": len(items), "passed": len(passed), "rejected": len(rejected)},
        )

    # ------------------------------------------------------------------
    def _score(self, ct: CleanedTweet) -> tuple[float, list[str]]:
        """Combine heuristic + ML signals into one bot probability."""
        reasons: list[str] = []

        # ---- heuristic features ----
        feats = self._extract_features(ct)
        h_score = self._heuristic_score(feats, reasons)

        # ---- classical model ----
        clf_score = 0.0
        if self._clf is not None:
            try:
                proba = self._clf.predict_proba([feats])[0]
                # class 1 = bot
                clf_score = float(proba[1]) if len(proba) > 1 else 0.0
            except Exception as e:  # pragma: no cover
                logger.warning(f"[bot] clf predict failed: {e}")

        # ---- BERT model ----
        bert_score = 0.0
        if self._bert is not None:
            try:
                tok, model = self._bert
                import torch

                inputs = tok(
                    ct.clean_text,
                    return_tensors="pt",
                    truncation=True,
                    max_length=128,
                )
                with torch.no_grad():
                    logits = model(**inputs).logits
                    probs = torch.softmax(logits, dim=-1).cpu().numpy()[0]
                bert_score = float(probs[1]) if len(probs) > 1 else 0.0
            except Exception as e:  # pragma: no cover
                logger.warning(f"[bot] bert predict failed: {e}")

        # ---- weighted ensemble ----
        if self._clf is None and self._bert is None:
            score = h_score
        elif self._bert is None:
            score = 0.7 * clf_score + 0.3 * h_score
        elif self._clf is None:
            score = 0.6 * bert_score + 0.4 * h_score
        else:
            score = 0.5 * clf_score + 0.3 * h_score + 0.2 * bert_score

        return float(np.clip(score, 0.0, 1.0)), reasons

    # ------------------------------------------------------------------
    def _extract_features(self, ct: CleanedTweet) -> list[float]:
        tw = ct.raw
        text = tw.text
        n_chars = max(len(text), 1)
        n_tokens = max(len(ct.tokens), 1)

        n_hashtags = len(tw.hashtags)
        n_urls = len(tw.urls)
        n_mentions = len(tw.mentions)
        n_excl = text.count("!")
        caps_chars = sum(1 for c in text if c.isupper())
        digit_chars = sum(1 for c in text if c.isdigit())
        emoji_count = sum(1 for c in text if ord(c) > 0x1F000)

        # engagement ratios
        eng_total = tw.like_count + tw.retweet_count + tw.reply_count + tw.quote_count
        eng_ratio = (
            tw.like_count / max(tw.author_followers, 1) if tw.author_followers else 0.0
        )
        rt_ratio = (
            tw.retweet_count / max(tw.like_count + 1, 1)
        )

        # spam regex hits
        spam_hits = 0
        for pat in _SPAM_PATTERNS:
            spam_hits += len(re.findall(pat, text, re.IGNORECASE))

        # account-level signals
        followers = tw.author_followers
        following = tw.author_following
        ff_ratio = (following / max(followers, 1)) if followers else 0.0
        no_bio = 1.0 if not (tw.author_description and tw.author_description.strip()) else 0.0
        no_avatar = 1.0 if not tw.author_profile_image_url else 0.0

        # tweet-level repetition (cheap proxy)
        uniq_ratio = len(set(ct.tokens)) / n_tokens

        feats = [
            n_hashtags / n_chars * 100,
            n_urls / n_chars * 100,
            n_mentions / n_chars * 100,
            n_excl / n_chars * 100,
            caps_chars / n_chars,
            digit_chars / n_chars,
            emoji_count,
            followers,
            np.log1p(followers),
            following,
            ff_ratio,
            no_bio,
            no_avatar,
            int(tw.author_verified),
            eng_total,
            np.log1p(eng_total),
            eng_ratio,
            rt_ratio,
            spam_hits,
            uniq_ratio,
            1.0 if tw.lang is None else 0.0,
        ]
        return feats

    @staticmethod
    def _heuristic_score(feats: list[float], reasons: list[str]) -> float:
        """Quick hand-tuned bot-score from features."""
        (
            ht_pct, url_pct, men_pct, ex_pct,
            caps_pct, dig_pct, emoji,
            followers, log_followers, following, ff_ratio,
            no_bio, no_avatar, verified,
            eng_total, log_eng, eng_ratio, rt_ratio,
            spam_hits, uniq_ratio, lang_none,
        ) = feats

        score = 0.0

        # spam patterns are decisive
        if spam_hits >= 1:
            score += 0.4
            reasons.append(f"spam_pattern_hits={spam_hits}")

        # excessive hashtags
        if ht_pct > 0.15:
            score += 0.2
            reasons.append("hashtag_spam")

        # excessive urls
        if url_pct > 0.2:
            score += 0.15
            reasons.append("url_spam")

        # SHOUTING
        if caps_pct > 0.5 and len(feats) > 0:
            score += 0.1
            reasons.append("all_caps")

        # emoji spam
        if emoji >= 5:
            score += 0.1
            reasons.append("emoji_spam")

        # low followers + low engagement
        if followers < 200:
            score += 0.1
            reasons.append("low_followers")
        if eng_total < 2 and followers < 1000:
            score += 0.05
            reasons.append("low_engagement")

        # follow/follower ratio: bots follow many, are followed by few
        if ff_ratio > 5:
            score += 0.15
            reasons.append("high_ff_ratio")

        # missing bio or avatar
        if no_bio:
            score += 0.05
            reasons.append("no_bio")
        if no_avatar:
            score += 0.05
            reasons.append("no_avatar")

        # unverified account with non-trivial text gets a small penalty
        if not verified:
            score += 0.02

        # low lexical diversity
        if uniq_ratio < 0.4:
            score += 0.1
            reasons.append("low_lexical_diversity")

        return min(score, 1.0)