from __future__ import annotations

import re

DIMENSION_PATTERNS: dict[str, tuple[str, ...]] = {
    "leadership": (
        r"\blead(?:s|ing)?\b",
        r"\bled\b",
        r"\bmanag(?:e|es|ed|ing|er)\b",
        r"\bmentor(?:s|ed|ing)?\b",
        r"\bcoach(?:es|ed|ing)?\b",
        r"\bsupervis(?:e|es|ed|ing|or)\b",
        r"\bdirect(?:s|ed|ing|or)\b",
        r"\bown(?:s|ed|ing)?\b",
        r"\bhead of\b",
        r"\bteam of\b",
        r"\bstakeholder(?:s)?\b",
    ),
    "strategic_impact": (
        r"\bstrateg(?:y|ic|ies)\b",
        r"\broadmap\b",
        r"\bvision\b",
        r"\btransform(?:ation|ational|ed|ing)?\b",
        r"\bbusiness case\b",
        r"\bcost (?:optimization|reduction|savings?)\b",
        r"\brevenue\b",
        r"\bgrowth\b",
        r"\badoption\b",
        r"\boperating model\b",
        r"\bokrs?\b",
    ),
    "architecture": (
        r"\barchitect(?:s|ed|ing|ure|ural)?\b",
        r"\bdesign(?:s|ed|ing)?\b",
        r"\bsolution(?:s)?\b",
        r"\bplatform(?:s)?\b",
        r"\blanding zone(?:s)?\b",
        r"\breference architecture\b",
        r"\bmicroservice(?:s)?\b",
        r"\bmulti[- ]cloud\b",
        r"\bscalab(?:le|ility)\b",
        r"\bresilien(?:t|ce)\b",
    ),
    "delivery": (
        r"\bdeliver(?:s|ed|ing|y)?\b",
        r"\blaunch(?:es|ed|ing)?\b",
        r"\bmigrat(?:e|es|ed|ing|ion)\b",
        r"\bimplement(?:s|ed|ing|ation)?\b",
        r"\brollout\b",
        r"\bdeploy(?:s|ed|ing|ment)?\b",
        r"\bend[- ]to[- ]end\b",
        r"\bon[- ]time\b",
        r"\bprogram(?:s)?\b",
    ),
    "domain": (
        r"\bdomain expertise\b",
        r"\bindustry\b",
        r"\bhealthcare\b",
        r"\bfinance\b",
        r"\bfinancial services\b",
        r"\bretail\b",
        r"\btelecom\b",
        r"\bmanufacturing\b",
        r"\bcompliance\b",
        r"\bregulated\b",
        r"\bsaas\b",
        r"\benterprise\b",
    ),
}

DIMENSION_ORDER = (
    "leadership",
    "strategic_impact",
    "architecture",
    "delivery",
    "domain",
)

STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "has",
    "have",
    "in",
    "into",
    "is",
    "it",
    "of",
    "on",
    "or",
    "our",
    "that",
    "the",
    "their",
    "to",
    "with",
    "you",
    "your",
}


def tokens(value: str) -> set[str]:
    result: set[str] = set()
    for token in re.findall(r"[a-z0-9+#.]+", value.casefold()):
        if len(token) < 3 or token in STOPWORDS:
            continue
        if token.endswith("ing") and len(token) > 5:
            token = token[:-3]
        elif (token.endswith("ed") or token.endswith("es")) and len(token) > 4:
            token = token[:-2]
        elif token.endswith("s") and len(token) > 3:
            token = token[:-1]
        result.add(token)
    return result


def classify_dimension(text: str, category: str = "") -> str:
    category_key = category.casefold()
    if category_key == "education":
        return "education"
    if category_key == "experience":
        return "experience"
    if category_key == "certification":
        return "certification"

    lowered = text.casefold()
    for dimension in DIMENSION_ORDER:
        if any(
            re.search(pattern, lowered, re.IGNORECASE) for pattern in DIMENSION_PATTERNS[dimension]
        ):
            return dimension
    return "technical"


def dimension_hits(text: str, dimension: str) -> int:
    patterns = DIMENSION_PATTERNS.get(dimension, ())
    return sum(1 for pattern in patterns if re.search(pattern, text, re.IGNORECASE))


def is_dimension_supported(text: str, dimension: str) -> bool:
    return dimension_hits(text, dimension) > 0
