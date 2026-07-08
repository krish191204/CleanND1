"""Stage 3.5: Noise / opinion / engagement-bait / misinfo filter.

Distinct from bot detection (Stage 3) — these tweets are usually from real humans
who just aren't producing *news*. Catching them here keeps the feed honest.

Categories:
  - engagement_bait    "follow this account", "subscribe", "turn on notifications"
  - rhetorical_question "can someone explain", "explain like I'm 8", "am I the only one"
  - promotional         "we're giving away", "apply now", "sign up", "free for next 24h"
  - personal_opinion    "I think", "imo", "in my opinion", "it's commendable"
  - misinfo_caps        ALL-CAPS miracle cures, "they don't want you to know"
  - product_announce    "new block", "we're extending", "coming to mobile"
  - reaction_post       "🚨🚨🚨", emoji-only commentary, "what the hell is going on"

Output:
  - noise_score in [0, 1]
  - noise_labels: list[str] of matched categories
  - rejection if noise_score >= 0.7
  - penalty applied to downstream credibility when noise_score in [0.3, 0.7)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from loguru import logger

from ..models.schemas import CleanedTweet
from .base import Stage, StageResult


# ---------------------------------------------------------------------
# Pattern banks
# ---------------------------------------------------------------------

PATTERNS: dict[str, list[str]] = {
    "engagement_bait": [
        r"^\s*follow\b",
        r"^\s*follow\s+(this|for|me|us|back)\b",
        r"\bfollow\s+(?:us|me)\s+for\b",
        r"\bturn\s+on\s+(?:post\s+)?notifications?\b",
        r"\bsubscribe\s+(?:to|for)\b",
        r"^\s*like\s+(?:and|&)\s+share\b",
        r"^\s*rt\s+if\s+you\s+agree\b",
    ],
    "rhetorical_question": [
        r"\bcan\s+someone\s+explain\b",
        r"\bexplain\s+(?:like|as\s+if)\s+i(?:'m|\s*am)\s+(?:8|a|an)\b",
        r"\bexplain\s+(?:like|as\s+if)\s+i(?:'m|\s*am)\s+(?:five|a\s+child)\b",
        r"^is\s+(?:it|this)\s+just\s+me\b",
        r"^am\s+i\s+the\s+only\s+(?:one|person)\b",
        r"\bdoes\s+anyone\s+else\b",
        r"^what\s+the\s+(?:hell|fuck|actual)\b",
    ],
    "promotional": [
        r"\b(?:we(?:'re|\s+are)\s+)?giving\s+away\b",
        r"\bapply\s+now\b",
        r"\bsign\s+up\s+(?:now|today|here)\b",
        r"\bjoin\s+\d[\d,]*\s+(?:others?|people)\b",
        r"\bfree\s+for\s+(?:the\s+next\s+)?\d+\b",
        r"\bonly\s+\d+\s+spots?\s+left\b",
        r"\blink\s+in\s+bio\b",
        r"\buse\s+(?:code|promo)\s+[A-Z0-9]+\b",
    ],
    "personal_opinion": [
        r"^i\s+think\b",
        r"^imo\b",
        r"^imho\b",
        r"^in\s+my\s+opinion\b",
        r"^turns\s+out\b",
        r"^it(?:'s|\s+is)\s+commendable\b",
        r"^it(?:'s|\s+is)\s+(?:cute|funny|sad|nice|great|cool)\s+that\b",
        r"^anybody\s+else\b",
        r"^elon\s+is\s+right\b",
        r"^elon\s+is\s+wrong\b",
        r"^love\s+this\b",
        r"^hate\s+(?:this|when)\b",
        r"^just\s+because\b",
        r"^always\s+(?:love|hate)\b",
        # looser patterns - anywhere in the tweet
        r"\byou\s+just\s+can'?t\s+make\s+this\s+up\b",
        r"\b(?:first|second|third),?\s+(?:mark|joe|kamala|elon|trump)\b",
        r"\b(?:is|are)\s+(?:one\s+of\s+the\s+)?(?:smartest|craziest|wildest|insane|coolest)\b",
        r"\b(?:i\s+am\s+approximately|approximately\s+as)\b",
        r"\b(?:will\s+always\s+be\s+remembered\s+as)\b",
        r"\b(?:will\s+always\s+be\s+remembered)\b",
    ],
    "personal_life": [
        r"^our\s+(?:older|younger|kid|son|daughter|baby|dog|cat)\b",
        r"^my\s+(?:kid|son|daughter|baby|dog|cat|wife|husband|mom|dad)\b",
        r"^i\s+(?:just\s+)?(?:got back|am back|am at|am heading|am off)\b",
        r"^on\s+(?:my|our)\s+(?:way|way\s+to|way\s+home)\b",
        r"^weekend\s+(?:vibes|recap|recap|thoughts)\b",
        r"^happy\s+(?:birthday|anniversary|holidays)\s+to\s+my\b",
    ],
    "political_propaganda": [
        r"\b(?:soros|globalist|deep\s+state|nwo|new\s+world\s+order)\b.*\b(?:lying|run|boss|behind)\b",
        r"\b(?:lizard\s+people|illuminati)\b",
        r"\bwas\s+(?:rigged|stolen|murdered)\b",
        r"\bdeep\s+state\b",
        r"\b(?:called|named)\s+pope\s+leo\b.*\b(?:chinese|communist|satan|antichrist)\b",
        r"\bwill\s+always\s+be\s+remembered\s+as\s+a\s+true\b",
        r"^breaking:\s+.{1,40}\b(?:magic|delusion|scam|hoax|exposed)\b",
        r"\bon\s+behalf\s+of\s+(?:the\s+)?(?:leadership|government)\s+(?:and|of)\s+people\b",
    ],
    "sensationalized": [
        r"\bbloodbath\b",
        r"\bmassive\s+selloff\b",
        r"\b(?:horror|panic|chaos|carnage)\b",
        r"\bcrash(?:ing|ed)?\b",
        r"\bhiding\s+something\b",
        r"\b(?:shocking|exposed|bombshell|breaking:?)\b\s+(?:news|report|story|update)\s*[!.]?\s*$",
        r"\b(?:will|would|could)\s+take\s+control\s+of\b",
        r"\bthis\s+is\s+pure\s+(?:authoritarianism|tyranny|propaganda|fascism)\b",
        r"\bfor\s+daring\s+to\s+speak\s+the\s+truth\b",
        r"\bi\s+was\s+arrested\b",
    ],
    "medical_conspiracy": [
        r"\bsv40\b",
        r"\b(?:vaccines?\s+cause|cause\s+autism)\b",
        r"\b(?:mrna|covid\s+vaccine|childhood\s+vaccines?)\b.*\b(?:poison|kill|danger|deadly)\b",
        r"\b(?:cancer\s+causing|carcinogenic)\b",
        r"\bwhy\s+was\s+.{1,40}\s+put\s+in\s+the\b",
        r"\bbig\s+pharma\b",
        r"\bmRNA\s+shots?\b.*\b(?:babies|offspring|fetus|fertility)\b",
    ],
    "political_commentary": [
        r"\b(?:victory\s+lap)\b",
        r"\b(?:the\s+experiment\s+failed|the\s+regime)\b",
        r"\btrump\s+admin(?:istration)?\s+(?:just|now|today)\s+\w+\b",
        r"\bclaude\s+at\s+investing\b",
        r"\bour\s+older\s+kid\b",
    ],
    "celebratory_greeting": [
        # holiday / birthday / anniversary greetings with little news content
        r"^\s*(?:happy|merry|cheers)\s+(?:\d+\w+\s+)?(?:birthday|holidays?|anniversary|4th|fourth|new\s+year|christmas|easter|thanksgiving|mothers?\s+day|fathers?\s+day|veterans?\s+day|memorial\s+day|labor\s+day|independence\s+day)\b",
        r"^\s*happy\s+(?:\d+\w+\s+)?(?:birthday|anniversary|holiday)",
        r"^\s*(?:happy|merry|wishing)\s+(?:\d+\w+\s+)?(?:of\s+)?july\b",
        # ordinal-only greeting (e.g. "Happy 250th, America!")
        r"^\s*(?:happy|merry|cheers)\s+\d+\w*\s*[!,.]?\s*$",
        r"^\s*happy\s+\d+\w*\s*,\s*(?:america|usa|ukraine|the\s+usa|all)",
        r"\b(?:wishing\s+(?:everyone|all|you|a\s+very)|to\s+all\s+(?:a|our))\s+(?:happy|merry|cheers)\b",
        r"^(?:🇺🇸|🇺🇦|🇷🇺|🇨🇳|🇪🇺|🇬🇧|🇯🇵|🇮🇱|🇫🇷)\s*(?:🇺🇸|🇺🇦|🇷🇺|🇨🇳|🇪🇺|🇬🇧|🇯🇵|🇮🇱|🇫🇷)?\s*(?:happy|congrats?|cheers)\b",
        r"^\s*congratulations?\s+(?:to\s+)?(?:\w+\s+){1,4}(?:on\b|!|$)",
        r"^\s*(?:proud|honored|excited)\s+(?:to|of|for)\b",
        r"^\s*honoring\s+(?:the|memory|legacy)\b",
        r"^\s*remembering\s+(?:the|memory|legacy|those)\b",
        # short flag-day / solidarity posts
        r"^\s*🇺🇸\s*[.!\?]*\s*$",
        r"^\s*god\s+bless\s+(?:america|the\s+usa|ukraine)\b",
        # "X years ago today" reflection pieces that are pure nostalgia (not news)
        r"^\s*\d{2,4}\s+years?\s+ago\s+(?:today|we|our|the)\b",
        # "we got you a present" — NASA-style holiday posts
        r"\bwe\s+got\s+you\s+a\s+present\b",
        # short reflexive/marketing messages
        r"^\s*love\s+this\s+re-imagining\b",
        r"^\s*re-imagining\s+america's\s+founding\b",
    ],
    "short_or_low_effort": [
        r"^\s*same\.?\s*$",
        r"^\s*this\.?\s*$",
        r"^\s*exactly\.?\s*$",
        r"^\s*period\.?\s*$",
        r"^\s*mood\.?\s*$",
        r"^\s*based\.?\s*$",
        r"^\s*wow\.?\s*$",
    ],
    "misinfo_caps": [
        r"\bCURED\b",
        r"\b\d{2,3}\s*%\s+(?:safe|effective|cure)\b",
        r"\bthey\s+don'?t\s+want\s+you\s+to\s+know\b",
        r"\bwake\s+up\b",
        r"\bopen\s+your\s+eyes\b",
        r"\bmiracle\s+(?:cure|drug|treatment)\b",
    ],
    "product_announce": [
        r"^new\s+(?:block|feature|tool|model|product|release|update|version)\b",
        r"\bwe(?:'re|\s+are)\s+(?:extending|launching|releasing|introducing|shipping)\b",
        r"\bcoming\s+to\s+(?:mobile|web|all\s+plans|desktop)\b",
        r"\bnow\s+(?:available|live)\s+(?:on|in|for)\b",
    ],
    "reaction_post": [
        r"^\s*🚨{2,}",
        r"^(?:this|that)\s+(?:is\s+)?(?:wild|insane|crazy|unreal)\b",
        r"^(?:lol|lmao|bruh|yikes)\b",
        r"^(?:hot\s+take|unpopular\s+opinion)\b",
    ],
    "short_thread_tease": [
        r"^(?:a|1)\s+thread\s+🧵",
        r"^\d+/\s*$",
        r"\bmore\s+below\b",
        r"\b👇\s*more\s+in\s+replies\b",
    ],
}


@dataclass
class NoiseResult:
    score: float = 0.0
    labels: list[str] = field(default_factory=list)


def _scan(text: str) -> NoiseResult:
    res = NoiseResult()
    # normalize Unicode curly quotes to ASCII straight quotes so the patterns work
    text_norm = (
        text.replace("’", "'")  # right single quotation mark
        .replace("‘", "'")  # left single quotation mark
        .replace("“", '"')  # left double quotation mark
        .replace("”", '"')  # right double quotation mark
    )
    text_lower = text_norm.lower()
    for label, patterns in PATTERNS.items():
        for pat in patterns:
            if re.search(pat, text_lower, flags=re.IGNORECASE | re.MULTILINE):
                res.labels.append(label)
                break
    # each label contributes a fixed weight
    weights = {
        "engagement_bait": 0.45,
        "rhetorical_question": 0.40,
        "promotional": 0.50,
        "personal_opinion": 0.40,
        "personal_life": 0.40,
        "political_propaganda": 0.40,
        "sensationalized": 0.35,
        "medical_conspiracy": 0.45,
        "political_commentary": 0.30,
        "celebratory_greeting": 0.45,
        "misinfo_caps": 0.60,
        "product_announce": 0.25,
        "reaction_post": 0.15,
        "short_thread_tease": 0.15,
        "short_or_low_effort": 0.30,
    }
    # hard reject: these alone are enough to drop the tweet outright
    hard_reject = {
        "misinfo_caps",
        "engagement_bait",
        "promotional",
        "rhetorical_question",
        "personal_life",
        "political_propaganda",
        "celebratory_greeting",
        "medical_conspiracy",
        "short_or_low_effort",
    }
    score = sum(weights.get(lbl, 0.25) for lbl in set(res.labels))
    res.score = min(score, 1.0)
    if any(lbl in hard_reject for lbl in res.labels):
        res.score = max(res.score, 0.40)
    return res


class NoiseFilter(Stage[CleanedTweet, CleanedTweet]):
    """Annotates each tweet with `noise_score` and `noise_labels`.

    Rejects obvious noise outright; lower-noise items get a downstream penalty.
    """

    name = "stage3b_noise"

    def __init__(self, reject_threshold: float = 0.30) -> None:
        super().__init__()
        self.reject_threshold = reject_threshold

    def process(self, items: list[CleanedTweet]) -> StageResult[CleanedTweet]:
        passed: list[CleanedTweet] = []
        rejected: list[tuple[CleanedTweet, str]] = []

        for ct in items:
            res = _scan(ct.raw.text)
            # attach for downstream use
            ct.__dict__["noise_score"] = res.score  # tolerated by Pydantic's extra=ignore
            ct.__dict__["noise_labels"] = res.labels

            if res.score >= self.reject_threshold:
                ct.bot_reasons = (ct.bot_reasons or []) + [f"noise:{lbl}" for lbl in res.labels]
                rejected.append((ct, f"noise={res.score:.2f}:{','.join(res.labels)}"))
            else:
                passed.append(ct)

        return StageResult(
            passed=passed,
            rejected=rejected,
            stats={"input": len(items), "passed": len(passed), "rejected": len(rejected)},
        )


def credibility_penalty(noise_score: float) -> float:
    """Penalty to apply to credibility when noise is moderate.

    Tuned so a single opinion tag demotes a tweet ~0.10 (HIGH -> MEDIUM) but
    doesn't tank it to LOW.
    """
    if noise_score < 0.15:
        return 0.0
    if noise_score < 0.35:
        return 0.10
    if noise_score < 0.55:
        return 0.20
    return 0.30