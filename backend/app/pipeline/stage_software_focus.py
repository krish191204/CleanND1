"""Stage 0: Software Focus Filter.

Broad gate covering the **software sphere** — AI/ML + programming languages +
frameworks + tools + tech companies + engineering culture. Runs BEFORE the rest
of the cleaning pipeline; rejects anything that isn't about software.

Sub-checks (all independently toggleable):

  1. account bio / display name contains software-sphere keywords
  2. profile metadata: ≥100 followers, ≥30 days old
  3. handle is in a curated known-account list
     (researchers / practitioners / organizations / papers / media / engineering voices)
  4. tweet content contains software-sphere terms
  5. tweet content does NOT contain scam terms (giveaway / airdrop / crypto / nft)
  6. retweet filter: if it's a retweet, original author must be in known list
  7. engagement quality: ≥5 combined (likes + retweets)

The user-facing helper ``clean_tweet_for_software_focus`` returns ``None`` for any
tweet that fails the gate.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from loguru import logger

from ..models.schemas import RawTweet
from .base import Stage, StageResult


# =====================================================================
# Keyword banks
# =====================================================================

# Terms that should appear in an account's bio / display name OR in the tweet.
# Covers the full software sphere: AI/ML, programming languages, frameworks,
# tools, roles, methodology.
_BIO_KEYWORDS: list[str] = [
    # ---- AI / ML ----
    r"\bai\b", r"\bml\b", r"\bai/ml\b", r"\bnlp\b",
    r"\bmachine\s+learning\b", r"\bdeep\s+learning\b",
    r"\bartificial\s+intelligence\b", r"\bdata\s+science\b",
    r"\bneural\s+network\b", r"\bcomputer\s+vision\b",
    r"\breinforcement\s+learning\b",
    r"\bllm\b", r"\bgpt\b", r"\btransformer\b", r"\bgenerative\s+ai\b",
    r"\bdiffusion\b", r"\bllama\b", r"\bclaude\b",
    r"\bml\s+engineer\b", r"\bai\s+researcher\b",
    r"\bdata\s+scientist\b", r"\bmlops\b",
    r"\bai\s+ethics\b", r"\bprompt\s+engineer\b",
    r"\bpytorch\b", r"\btensorflow\b", r"\bhugging\s*face\b",
    r"\bscikit[\s-]?learn\b", r"\bkeras\b", r"\blangchain\b",
    r"\bopenai\b", r"\bmlflow\b",
    # ---- Programming languages ----
    r"\bpython\b", r"\bjavascript\b", r"\btypescript\b", r"\brust\b",
    r"\bgo(lang)?\b", r"\bjava\b", r"\bkotlin\b", r"\bswift\b",
    r"\bruby\b", r"\bphp\b", r"\bc\+\+\b", r"\bc#\b", r"\bscala\b",
    r"\belixir\b", r"\bzig\b", r"\bnim\b", r"\bcrystal\b",
    r"\belm\b", r"\bocaml\b", r"\bzig\b", r"\brust\s+lang\b",
    r"\bdart\b", r"\bflutter\b",
    # ---- Frameworks / libraries ----
    r"\breact\b", r"\bvue\b", r"\bangular\b", r"\bsvelte\b",
    r"\bnext\.?js\b", r"\bnuxt\b", r"\bremix\b",
    r"\bexpress\b", r"\bdjango\b", r"\bflask\b", r"\bfastapi\b",
    r"\brails\b", r"\bspring\b", r"\.net\b", r"\blaravel\b",
    r"\bphoenix\b", r"\bactix\b", r"\baxum\b", r"\bnestjs\b",
    r"\bdjango\b", r"\btauri\b", r"\belectron\b",
    # ---- Databases / data ----
    r"\bpostgres(ql)?\b", r"\bmysql\b", r"\bmongodb\b",
    r"\bredis\b", r"\bsqlite\b", r"\belasticsearch\b",
    r"\bcassandra\b", r"\bdynamodb\b", r"\bsupabase\b",
    r"\bprisma\b", r"\bplanetscale\b", r"\bfoundationdb\b",
    r"\bduckdb\b", r"\bclickhouse\b", r"\bneo4j\b",
    # ---- Cloud / DevOps / Infra ----
    r"\baws\b", r"\bgcp\b", r"\bazure\b", r"\bkubernetes\b",
    r"\bdocker\b", r"\bterraform\b", r"\bansible\b",
    r"\bjenkins\b", r"\bgithub\s+actions\b", r"\bvercel\b",
    r"\bnetlify\b", r"\bfly\.io\b", r"\brender\b",
    r"\bdigitalocean\b", r"\bheroku\b", r"\blinode\b",
    r"\bcloudflare\b", r"\bkafka\b", r"\brabbitmq\b",
    r"\bnginx\b", r"\bwasm\b", r"\bwebassembly\b",
    # ---- Engineering roles ----
    r"\bsoftware\s+engineer\b", r"\bfrontend\s+(developer|engineer)\b",
    r"\bbackend\s+(developer|engineer)\b", r"\bfull[\s-]?stack\b",
    r"\bdevops\b", r"\bsre\b", r"\bplatform\s+engineer\b",
    r"\bdata\s+engineer\b", r"\bml\s+engineer\b",
    r"\bengineering\s+manager\b", r"\bstaff\s+engineer\b",
    r"\bprincipal\s+engineer\b",
    r"\bdeveloper\s+advocate\b", r"\btech\s+lead\b",
    r"\bsre\b", r"\bdevrel\b",
    # ---- Methodology / concepts ----
    r"\bopen\s+source\b", r"\bmicroservices?\b", r"\bserverless\b",
    r"\bedge\s+compute\b", r"\brest\s+api\b", r"\bgraphql\b",
    r"\bgrpc\b", r"\bobservability\b", r"\bmonitoring\b",
    r"\bci/?cd\b", r"\btest\s+driven\b", r"\bagile\b",
    r"\bscrum\b", r"\bsre\b",
    r"\bcode\s+review\b", r"\bunit\s+test", r"\bintegration\s+test",
    r"\btdd\b", r"\bbdd\b",
    # ---- Misc ----
    r"\bresearcher\b", r"\bresearch\s+scientist\b",
    r"\bphd\s+(student|candidate|fellow)\b",
    r"\btech\s+(startup|company|companies)\b",
    r"\bgithub\b", r"\bgitlab\b",
    r"\bvscode\b", r"\bjetbrains\b", r"\bneovim\b",
]

# Software-sphere terms that must appear in the tweet body itself.
_TWEET_KEYWORDS: list[str] = [
    # ---- AI/ML ----
    r"\bmodel\b", r"\btraining\b", r"\bfine[\s-]?tun(e|ing)\b",
    r"\binference\b", r"\bdataset\b", r"\bbenchmark\b",
    r"\baccuracy\b", r"\bparameter(s)?\b", r"\bweight(s)?\b",
    r"\bcheckpoint(s)?\b", r"\bembedding(s)?\b",
    r"\btokeniz(e|ation|ing)\b", r"\barxiv\b", r"\bneurips\b",
    r"\bicml\b", r"\bcvpr\b", r"\biccv\b", r"\biclr\b", r"\baaai\b",
    r"\bgpu(s)?\b", r"\btpu(s)?\b", r"\bcompute\b",
    r"\battention\b", r"\bopen\s+source\b",
    r"\bgpt\b", r"\bclaude\b", r"\bllama\b", r"\bgemini\b",
    r"\bdeepseek\b", r"\bmistral\b",
    r"\bprompt(s|ing)?\b", r"\bfine[\s-]?tun(e|ed|ing)\b",
    r"\bpretrain(ed|ing)?\b", r"\btrain(ed|ing)?\b",
    r"\binference\b", r"\bembed(ding)?\b", r"\bvector\s+(store|db|search)\b",
    r"\banthropic\b", r"\bopenai\b", r"\bhugging\s*face\b",
    r"\bneural\s+(net|work)\b", r"\btransformer\b", r"\bclassifier\b",
    # ---- Programming / software engineering ----
    r"\brelease(d)?\b", r"\bv?\d+\.\d+(\.\d+)?\b",
    r"\bcompiler\b", r"\bruntime\b", r"\bbytecode\b",
    r"\bsource\s+code\b", r"\brepository\b", r"\brepo\b",
    r"\bcommit\b", r"\bmerge\b", r"\bbranch\b", r"\bpull\s+request\b",
    r"\bissue\b", r"\bbug\b", r"\bpatch\b", r"\bhotfix\b",
    r"\bdeprecated\b", r"\bbreaking\s+change\b",
    r"\bapi\b", r"\bendpoint\b", r"\broute\b", r"\bhandler\b",
    r"\bmiddleware\b", r"\bplugin\b", r"\bextension\b",
    r"\bcompiler\b", r"\binterpreter\b", r"\bparser\b",
    r"\bcompiler\b", r"\boptimization\b", r"\bperformance\b",
    r"\blatency\b", r"\bthroughput\b", r"\bbenchmark\b",
    r"\bunit\s+test", r"\bintegration\s+test", r"\be2e\s+test",
    r"\brefactor\b", r"\bmigration\b", r"\barchitecture\b",
    r"\bdesign\s+pattern\b", r"\bcode\s+review\b",
    r"\btype\s+system\b", r"\bstatic\s+typing\b", r"\bdynamic\s+typing\b",
    r"\bcompil(e|er|ation)\b", r"\bdependency\b", r"\bpackage\s+manager\b",
    r"\bbundler\b", r"\btranspil(e|er|ation)\b",
    # ---- Languages ----
    r"\bpython\b", r"\bjavascript\b", r"\btypescript\b", r"\brust\b",
    r"\bjava\b", r"\bkotlin\b", r"\bswift\b", r"\bruby\b",
    r"\bphp\b", r"\bc\+\+\b", r"\bc#\b", r"\bscala\b",
    r"\belixir\b", r"\bzig\b", r"\bgo(lang)?\b",
    # ---- Frameworks / libraries (tweet-level) ----
    r"\breact\b", r"\bvue\b", r"\bnext\.?js\b",
    r"\bdjango\b", r"\bfastapi\b", r"\brails\b",
    r"\bspring\b", r"\bexpress\b", r"\bnestjs\b",
    r"\btailwind\b", r"\bprisma\b", r"\blangchain\b",
    r"\bpytorch\b", r"\btensorflow\b",
    # ---- Infra / cloud ----
    r"\bkubernetes\b", r"\bdocker\b", r"\bterraform\b",
    r"\baws\b", r"\bgcp\b", r"\bazure\b",
    r"\bvercel\b", r"\bnetlify\b", r"\bsupabase\b",
    r"\bredis\b", r"\bpostgres(ql)?\b", r"\bmongodb\b",
    r"\bkafka\b", r"\brabbitmq\b", r"\bnginx\b",
    # ---- Org / product names ----
    r"\bgithub\b", r"\bgitlab\b", r"\bmicrosoft\b",
    r"\bapple\b", r"\bgoogle\b", r"\bmeta\b", r"\bamazon\b",
    r"\bnvidia\b", r"\bopenai\b", r"\banthropic\b",
    r"\bhugging\s*face\b", r"\bfigma\b", r"\bnotion\b",
    r"\bvscode\b", r"\bjetbrains\b",
]

# Scam / crypto noise — these get a hard reject.
_SCAM_KEYWORDS: list[str] = [
    r"\bgiveaway\b", r"\bairdrop\b", r"\bcrypto\b", r"\bnft\b",
    r"\bpump\s+and\s+dump\b", r"\bmemecoin\b",
    r"\b100x\b", r"\bmoonshot\b",
    r"\b(?:dm|message)\s+me\s+for\b",
]


# =====================================================================
# Stage
# =====================================================================

class SoftwareFocusFilter(Stage[RawTweet, RawTweet]):
    """Filter tweets to the broader software sphere (AI/ML + programming + tech)."""

    name = "stage0_software_focus"

    def __init__(
        self,
        *,
        known_accounts_path: str | Path = "./data/known_software_accounts.json",
        min_followers: int = 100,
        min_account_age_days: int = 30,
        min_engagement: int = 5,
        require_all_signals: bool = False,
        check_retweets: bool = True,
        check_engagement: bool = True,
        check_scam: bool = True,
        check_profile_metadata: bool = True,
        known_accounts: Optional[Iterable[str]] = None,
        bio_keywords: Optional[Iterable[str]] = None,
        tweet_keywords: Optional[Iterable[str]] = None,
        scam_keywords: Optional[Iterable[str]] = None,
    ) -> None:
        super().__init__()
        self.min_followers = int(min_followers)
        self.min_account_age_days = int(min_account_age_days)
        self.min_engagement = int(min_engagement)
        self.require_all_signals = bool(require_all_signals)
        self.check_retweets = bool(check_retweets)
        self.check_engagement = bool(check_engagement)
        self.check_scam = bool(check_scam)
        self.check_profile_metadata = bool(check_profile_metadata)

        self._bio_rx = [re.compile(p, re.IGNORECASE) for p in (bio_keywords or _BIO_KEYWORDS)]
        self._tweet_rx = [re.compile(p, re.IGNORECASE) for p in (tweet_keywords or _TWEET_KEYWORDS)]
        self._scam_rx = [re.compile(p, re.IGNORECASE) for p in (scam_keywords or _SCAM_KEYWORDS)]

        if known_accounts is not None:
            self.known_accounts: set[str] = {h.lower().lstrip("@") for h in known_accounts}
        else:
            self.known_accounts = self._load_known(Path(known_accounts_path))

        logger.info(
            f"[software_focus] loaded {len(self.known_accounts)} known accounts, "
            f"{len(self._bio_rx)} bio kws, {len(self._tweet_rx)} tweet kws, "
            f"{len(self._scam_rx)} scam kws"
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _load_known(path: Path) -> set[str]:
        if not path.exists():
            logger.warning(f"[software_focus] known-accounts file not found: {path}")
            return set()
        try:
            data = json.loads(path.read_text())
        except Exception as e:
            logger.warning(f"[software_focus] failed to read {path}: {e}")
            return set()
        handles: set[str] = set()
        for category in (
            "researchers", "practitioners", "organizations",
            "papers", "media", "engineering_voices",
        ):
            for h in data.get(category, []) or []:
                handles.add(str(h).lower().lstrip("@"))
        return handles

    # ------------------------------------------------------------------
    @classmethod
    def from_settings(cls, settings) -> "SoftwareFocusFilter":
        return cls(
            known_accounts_path=settings.software_known_accounts_path,
            min_followers=settings.software_min_followers,
            min_account_age_days=settings.software_min_account_age_days,
            min_engagement=settings.software_min_engagement,
            require_all_signals=settings.software_require_all_signals,
            check_retweets=settings.software_check_retweets,
            check_engagement=settings.software_check_engagement,
            check_scam=settings.software_check_scam,
            check_profile_metadata=settings.software_check_profile_metadata,
        )

    # ------------------------------------------------------------------
    def process(self, items: list[RawTweet]) -> StageResult[RawTweet]:
        passed: list[RawTweet] = []
        rejected: list[tuple[RawTweet, str]] = []
        now = datetime.now(timezone.utc)

        for tw in items:
            reason = self._reject_reason(tw, now)
            if reason:
                rejected.append((tw, reason))
            else:
                self._annotate(tw)
                passed.append(tw)

        return StageResult(
            passed=passed,
            rejected=rejected,
            stats={
                "input": len(items),
                "passed": len(passed),
                "rejected": len(rejected),
            },
        )

    # ------------------------------------------------------------------
    def _reject_reason(self, tw: RawTweet, now: datetime) -> Optional[str]:
        if self.check_profile_metadata:
            if tw.author_followers < self.min_followers:
                return f"followers<{self.min_followers}"
            if tw.author_created_at:
                age = (now - tw.author_created_at).days
                if age < self.min_account_age_days:
                    return f"account_age<{self.min_account_age_days}d"

        if not self._passes_account(tw):
            return "account_not_software_focused"

        if self.check_scam and self._is_scam(tw):
            return "tweet_scam_terms"

        if not self._passes_content(tw):
            return "tweet_no_software_terms"

        if self.check_retweets and self._is_rt_of_unknown(tw):
            return "rt_unknown_author"

        if self.check_engagement and self._too_low_engagement(tw):
            return f"low_engagement<{self.min_engagement}"

        return None

    # ------------------------------------------------------------------
    # Sub-checks
    # ------------------------------------------------------------------
    def _passes_account(self, tw: RawTweet) -> bool:
        handle = (tw.author_handle or "").lower().lstrip("@")
        if handle and handle in self.known_accounts:
            return True

        bio_hit = any(rx.search(tw.author_description or "") for rx in self._bio_rx)
        name_hit = any(rx.search(tw.author_display_name or "") for rx in self._bio_rx)
        verified_software = (
            tw.author_verified
            and any(rx.search(tw.author_description or "") for rx in self._bio_rx)
        )

        signals = [bio_hit, name_hit, verified_software]
        if self.require_all_signals:
            return all(signals)
        return any(signals)

    def _passes_content(self, tw: RawTweet) -> bool:
        text = tw.text or ""
        return any(rx.search(text) for rx in self._tweet_rx)

    def _is_scam(self, tw: RawTweet) -> bool:
        text = tw.text or ""
        return any(rx.search(text) for rx in self._scam_rx)

    def _is_rt_of_unknown(self, tw: RawTweet) -> bool:
        text = (tw.text or "").lstrip().lower()
        if not text.startswith("rt "):
            return False
        parts = text.split(None, 2)
        if len(parts) < 2 or not parts[1].startswith("@"):
            return False
        original = parts[1].lstrip("@").lower()
        return original not in self.known_accounts

    def _too_low_engagement(self, tw: RawTweet) -> bool:
        return (tw.like_count + tw.retweet_count) < self.min_engagement

    # ------------------------------------------------------------------
    def _annotate(self, tw: RawTweet) -> None:
        if not hasattr(tw, "_software_focus_passed"):
            object.__setattr__(tw, "_software_focus_passed", True)
        meta = []
        handle = (tw.author_handle or "").lower().lstrip("@")
        if handle and handle in self.known_accounts:
            meta.append(f"known_account:{handle}")
        bio_hit = any(rx.search(tw.author_description or "") for rx in self._bio_rx)
        name_hit = any(rx.search(tw.author_display_name or "") for rx in self._bio_rx)
        if bio_hit:
            meta.append("bio_keyword_match")
        if name_hit:
            meta.append("display_name_keyword_match")
        if tw.author_verified and bio_hit:
            meta.append("verified_software_account")
        meta.append(f"followers={tw.author_followers}")
        object.__setattr__(tw, "_software_focus_meta", meta)


# =====================================================================
# Helper: clean_tweet_for_software_focus
# =====================================================================

def clean_tweet_for_software_focus(
    raw: RawTweet, *, settings=None
) -> Optional[RawTweet]:
    """Functional wrapper — returns None if the tweet fails the software focus gate.

    Use this directly when you want a single-tweet decision:

        out = clean_tweet_for_software_focus(raw_tweet)
        if out is None:
            ...reject...
        else:
            ...process out...

    For batch processing, use ``SoftwareFocusFilter(...).process([...])`` instead.
    """
    if settings is None:
        from ..config import get_settings

        settings = get_settings()
    stage = SoftwareFocusFilter.from_settings(settings)
    result = stage.process([raw])
    return result.passed[0] if result.passed else None