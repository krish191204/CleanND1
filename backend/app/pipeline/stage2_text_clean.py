"""Stage 2: Text cleaning.

Steps:
- Strip URLs, mentions, RT prefix
- Lowercase
- Normalize unicode (NFC) + emoji description
- Tokenize (lightweight regex tokenizer; spaCy if available)
- Lemmatize (spaCy if available; otherwise NLTK WordNet; otherwise raw tokens)
- MinHash signature for near-duplicate detection (uses datasketch)

Fix 4 — when two near-duplicates come from DIFFERENT known handles, we keep
both and tag them with a shared `corroboration_group_id` so Stage 4 can
count them as a single corroboration event without requiring time-window
proximity.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any, Optional

from loguru import logger

from ..models.schemas import CleanedTweet, RawTweet
from ..services.known_handles import is_known_any
from .base import Stage, StageResult


_URL_RE = re.compile(r"https?://\S+|t\.co/\S+", re.IGNORECASE)
_MENTION_RE = re.compile(r"@\w+")
_HASHTAG_KEEP_RE = re.compile(r"#(\w+)")
_RT_PREFIX_RE = re.compile(r"^\s*(rt|qt)\s+", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z']+|[0-9]+(?:[.,][0-9]+)?")


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
    """Convert emojis to their textual description."""
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
    tokens = _TOKEN_RE.findall(text)
    lemmas = list(tokens)
    try:
        from nltk.stem import WordNetLemmatizer

        lem = WordNetLemmatizer()
        lemmas = [lem.lemmatize(t.lower()) for t in tokens]
    except Exception:  # pragma: no cover
        lemmas = [t.lower() for t in tokens]
    return tokens, lemmas


def _bucket_id(mh) -> str:
    """Stable 16-char hex id from a MinHash object's hashvalues bytes.
    Two tweets that match the same MinHash bucket share this id, which
    Stage 4's burst detector consumes as a no-time-window corroboration
    signal."""
    return hashlib.md5(mh.hashvalues.tobytes()).hexdigest()[:16]


class TextCleaner(Stage[RawTweet, CleanedTweet]):
    name = "stage2_text_clean"

    def __init__(
        self,
        compute_minhash: bool = True,
        num_perm: int = 128,
        min_token_len: int = 2,
        skip_dedup_for_known_handles: bool = True,
    ) -> None:
        super().__init__()
        self.compute_minhash = compute_minhash
        self.num_perm = num_perm
        self.min_token_len = min_token_len
        # Fix 4 (Issue 4): when two near-duplicates come from different
        # known handles, keep both and tag with a shared
        # corroboration_group_id instead of dropping the second.
        self.skip_dedup_for_known_handles = skip_dedup_for_known_handles
        # Rolling buffer of (MinHash_object, original_cleaned_tweet). We
        # keep the live MinHash object rather than bytes — re-creating
        # MinHash from raw bytes loses scheme metadata in datasketch 2.0+.
        # For corroboration_group_id derivation we use the bytes anyway.
        self._minhash_seen: list[tuple[Any, CleanedTweet]] = []
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
                # Sub-agent B Bug A: previously the tweet vanished here
                # — passed+rejected stayed < input. Now log the traceback
                # and append a stub CleanedTweet to `rejected` with a
                # `processing_failed:<exc-class>` reason so the caller
                # can audit / retry. We need a CleanedTweet-shaped stub
                # so the StageResult shape stays consistent.
                logger.exception(f"text_clean failed for {tw.id}")
                stub = CleanedTweet(
                    raw=tw,
                    clean_text="",
                    tokens=[],
                    lemmas=[],
                    language=tw.lang or "und",
                )
                rejected.append(
                    (stub, f"processing_failed:{type(e).__name__}:{e}")
                )
                continue

            if len(ct.tokens) < 5:
                # Too short to be informative
                rejected.append((ct, "too_few_tokens"))
                continue

            # near-duplicate detection via MinHash Jaccard
            if self.compute_minhash and ct.minhash_object is not None:
                dup_match = self._find_dup(ct.minhash_object)
                if dup_match is not None:
                    jaccard, original = dup_match
                    # Fix 4 — keep both tweets if BOTH are from known
                    # handles. Stage 4 uses the shared
                    # corroboration_group_id to count them as a single
                    # corroboration event.
                    if (
                        self.skip_dedup_for_known_handles
                        and is_known_any(ct.raw.author_handle)
                        and is_known_any(original.raw.author_handle)
                    ):
                        gid = _bucket_id(ct.minhash_object)
                        ct.corroboration_group_id = gid
                        original.corroboration_group_id = gid
                        if original not in passed:
                            passed.append(original)
                        passed.append(ct)
                        continue
                    rejected.append((ct, f"near_duplicate:jaccard={jaccard:.2f}"))
                    continue

                # Not a duplicate — add to the rolling buffer for future
                # comparisons. Pairs in the keep-both branch above don't
                # reach here the first time (canonical "first" entry is
                # already in the buffer).
                self._minhash_seen.append((ct.minhash_object, ct))
                if len(self._minhash_seen) > 200:
                    self._minhash_seen = self._minhash_seen[-200:]

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

        mh_obj = None
        if self.compute_minhash and self._minhash_obj is not None:
            # datasketch >= 2.0 requires explicit scheme. 'affine32' is
            # the new default; 'affine64' and 'legacy' also work.
            mh_obj = self._minhash_obj(num_perm=self.num_perm, scheme="affine32")
            for tok in set(tokens):
                mh_obj.update(tok.encode("utf-8"))

        # We attach the live MinHash object so _find_dup doesn't have to
        # reconstruct from bytes (which loses scheme metadata). The
        # corroboration_group_id is derived later from the same object's
        # hashvalues via _bucket_id.
        return CleanedTweet(
            raw=tw,
            clean_text=text,
            tokens=tokens,
            lemmas=lemmas,
            minhash_object=mh_obj,
            language=tw.lang or "und",
        )

    def _find_dup(
        self, mh_obj, threshold: float = 0.85
    ) -> Optional[tuple[float, CleanedTweet]]:
        """Return (jaccard, original) if a near-duplicate exists in the
        rolling buffer, else None. Issue 4 — refactored from a
        boolean-returning helper so the caller can decide whether to keep
        both tweets when the handles warrant it."""
        try:
            for past_mh, original in self._minhash_seen[-200:]:
                j = mh_obj.jaccard(past_mh)
                if j >= threshold:
                    return float(j), original
            return None
        except Exception:  # pragma: no cover
            return None
