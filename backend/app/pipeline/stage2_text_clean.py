"""Stage 2: Text cleaning.

Steps:
- Strip URLs, mentions, RT prefix
- Lowercase
- Normalize unicode (NFC) + emoji description
- Tokenize (lightweight regex tokenizer; spaCy if available)
- Lemmatize (spaCy if available; otherwise NLTK WordNet; otherwise raw tokens)
- MinHash signature for near-duplicate detection (uses datasketch)
"""
from __future__ import annotations

import re
import unicodedata
from typing import Optional

from loguru import logger

from ..models.schemas import CleanedTweet, RawTweet
from .base import Stage, StageResult


_URL_RE = re.compile(r"https?://\S+|t\.co/\S+", re.IGNORECASE)
_MENTION_RE = re.compile(r"@\w+")
_HASHTAG_KEEP_RE = re.compile(r"#(\w+)")
_RT_PREFIX_RE = re.compile(r"^\s*(rt|qt)\s+", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z']+|[0-9]+(?:[.,][0-9]+)?")


# Optional heavy deps - lazy load
_spacy_nlp = None
_spacy_attempted = False


def _get_spacy():
    global _spacy_nlp, _spacy_attempted
    if _spacy_nlp is not None or _spacy_attempted:
        return _spacy_nlp
    _spacy_attempted = True
    try:
        import spacy

        _spacy_nlp = spacy.load("en_core_web_sm", disable=["parser", "ner"])
    except Exception as e:  # pragma: no cover
        logger.warning(f"spaCy unavailable, falling back to regex tokenizer: {e}")
        _spacy_nlp = None
    return _spacy_nlp


def _strip_emoji_to_text(s: str) -> str:
    """Convert emojis to their textual description (e.g. 😂 -> ':face_with_tears_of_joy:')."""
    try:
        import emoji as _emoji

        return _emoji.demojize(s, delimiters=(" ", " "))
    except Exception:  # pragma: no cover
        return s


def _tokenize_lemmatize(text: str) -> tuple[list[str], list[str]]:
    nlp = _get_spacy()
    if nlp is not None:
        doc = nlp(text)
        tokens = [t.text for t in doc if not t.is_space]
        lemmas = [t.lemma_.lower() for t in doc if not t.is_space and t.lemma_.strip()]
        return tokens, lemmas
    # Fallback: regex tokenizer + WordNet lemmatizer (best-effort)
    tokens = _TOKEN_RE.findall(text)
    lemmas = list(tokens)
    try:
        from nltk.stem import WordNetLemmatizer

        lem = WordNetLemmatizer()
        lemmas = [lem.lemmatize(t.lower()) for t in tokens]
    except Exception:  # pragma: no cover
        lemmas = [t.lower() for t in tokens]
    return tokens, lemmas


class TextCleaner(Stage[RawTweet, CleanedTweet]):
    name = "stage2_text_clean"

    def __init__(
        self,
        compute_minhash: bool = True,
        num_perm: int = 128,
        min_token_len: int = 2,
    ) -> None:
        super().__init__()
        self.compute_minhash = compute_minhash
        self.num_perm = num_perm
        self.min_token_len = min_token_len
        self._minhash_seen: list[bytes] = []   # rolling buffer for dedup
        self._minhash_obj = None
        if compute_minhash:
            try:
                from datasketch import MinHash

                self._minhash_obj = MinHash
            except Exception:  # pragma: no cover
                logger.warning("datasketch not installed; MinHash dedup disabled")
                self.compute_minhash = False

    # ------------------------------------------------------------------
    def process(self, items: list[RawTweet]) -> StageResult[CleanedTweet]:
        passed: list[CleanedTweet] = []
        rejected: list[tuple[CleanedTweet, str]] = []
        for tw in items:
            try:
                ct = self._clean(tw)
            except Exception as e:
                logger.warning(f"text_clean failed for {tw.id}: {e}")
                continue

            if len(ct.tokens) < 5:
                # Too short to be informative — reject with reason but keep
                # a record for stats.
                rejected.append((ct, "too_few_tokens"))
                continue

            # near-duplicate detection via MinHash Jaccard
            if self.compute_minhash and ct.minhash_signature:
                if self._is_duplicate(ct.minhash_signature):
                    rejected.append((ct, "near_duplicate"))
                    continue

            passed.append(ct)

        return StageResult(
            passed=passed,
            rejected=rejected,
            stats={"input": len(items), "passed": len(passed), "rejected": len(rejected)},
        )

    # ------------------------------------------------------------------
    def _clean(self, tw: RawTweet) -> CleanedTweet:
        text = tw.text
        # unicode normalize
        text = unicodedata.normalize("NFC", text)
        # strip rt/quote prefix
        text = _RT_PREFIX_RE.sub("", text)
        # remove urls & mentions but keep hashtags-as-words
        text = _URL_RE.sub(" ", text)
        text = _MENTION_RE.sub(" ", text)
        text = _HASHTAG_KEEP_RE.sub(r"\1", text)
        # emoji -> text
        text = _strip_emoji_to_text(text)
        # lowercase
        text = text.lower()
        # collapse whitespace
        text = _WS_RE.sub(" ", text).strip()

        tokens, lemmas = _tokenize_lemmatize(text)
        tokens = [t for t in tokens if len(t) >= self.min_token_len]
        lemmas = [l for l in lemmas if len(l) >= self.min_token_len]

        sig: Optional[bytes] = None
        if self.compute_minhash and self._minhash_obj is not None:
            mh = self._minhash_obj(num_perm=self.num_perm)
            for tok in set(tokens):
                mh.update(tok.encode("utf-8"))
            # store as bytes for compactness (jb pack)
            sig = mh.hashvalues.tobytes()

        return CleanedTweet(
            raw=tw,
            clean_text=text,
            tokens=tokens,
            lemmas=lemmas,
            minhash_signature=sig,
            language=tw.lang or "und",
        )

    def _is_duplicate(self, sig: bytes, threshold: float = 0.85) -> bool:
        try:
            import numpy as np
            from datasketch import MinHash

            arr = np.frombuffer(sig, dtype=np.uint64)
            cur = MinHash(num_perm=self.num_perm, hashvalues=arr)
            for past in self._minhash_seen[-200:]:  # bounded window
                arr2 = np.frombuffer(past, dtype=np.uint64)
                other = MinHash(num_perm=self.num_perm, hashvalues=arr2)
                if cur.jaccard(other) >= threshold:
                    return True
            self._minhash_seen.append(sig)
            return False
        except Exception:  # pragma: no cover
            return False