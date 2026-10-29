"""Normalization helpers turning raw scraped strings into canonical values."""

from __future__ import annotations

import hashlib
import re
import unicodedata

_CITY_ALIASES: dict[str, str] = {
    "lisbon": "lisboa",
    "lisboa": "lisboa",
    "oporto": "porto",
    "porto": "porto",
    "braga": "braga",
    "coimbra": "coimbra",
    "aveiro": "aveiro",
    "faro": "faro",
    "leiria": "leiria",
}

_WORK_MODE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("remote", r"remot|teletrabalho|100%\s*remote|work from home|wfh"),
    ("hybrid", r"h[ií]brid|hybrid"),
    ("onsite", r"presencial|on-?site|no escrit[óo]rio"),
)

_GENDER_MARKER = re.compile(
    r"(?<![a-z])\(?\s*[mfhwxd](?:\s*/\s*[mfhwxd])+\s*\)?(?![a-z])",
    re.IGNORECASE,
)
_LEGAL_SUFFIX = re.compile(
    r"[,\s]+(?:s\.?\s*a\.?|lda\.?|unipessoal|sgps|s\.?\s*l\.?)\.?\s*$",
    re.IGNORECASE,
)
_SENIORITY = re.compile(r"\b(?:senior|junior|mid|pleno|lead)\b[-\s]*")
_WS = re.compile(r"\s+")
_MARKUP = re.compile(r"<[^>]+>|https?://\S+|www\.\S+", re.IGNORECASE)
_FP_SEP = "\x00"
_SENIORITY_LEVELS = re.compile(
    r"\b(?:(?P<intern>estagi\w*|intern(?:ship)?|trainee)"
    r"|(?P<junior>junior|jr|entry[- ]level)"
    r"|(?P<mid>mid(?:[- ]level)?|pleno|intermedi(?:o|a|ate))"
    r"|(?P<senior>senior|sr)"
    r"|(?P<lead>lead|principal|staff|head of))\b"
)

_YEARS = (
    r"(?:(ate|up to)\s+)?(?<!\d)(\d{1,2})\s*\+?\s*"
    r"(?:(?:-|\s(?:a|e|to|and)\s)\s*\d{1,2}\s*\+?\s*)?"
    r"(?:or more\s+|ou mais\s+)?(years?|anos?)\b"
)
_REQUIRED_YEARS = re.compile(
    rf"{_YEARS}(?=[^.]{{0,40}}experi)"
    rf"|(?:experi\w*|at least|pelo menos|minim\w*|procuramos)[^.\d]{{0,40}}?{_YEARS}"
)
_MAX_REQUIRED_YEARS = 15

_TAG_ALIASES: dict[str, str] = {
    "python": "python",
    "java": "java",
    "javascript": "javascript",
    "js": "javascript",
    "typescript": "typescript",
    "ts": "typescript",
    "react": "react",
    "vue": "vue",
    "angular": "angular",
    "node": "node",
    "node.js": "node",
    "nodejs": "node",
    "golang": "golang",
    "rust": "rust",
    "php": "php",
    "c#": "csharp",
    ".net": "dotnet",
    "postgresql": "postgresql",
    "postgres": "postgresql",
    "mysql": "mysql",
    "mongodb": "mongodb",
    "redis": "redis",
    "docker": "docker",
    "kubernetes": "kubernetes",
    "k8s": "kubernetes",
    "aws": "aws",
    "azure": "azure",
    "gcp": "gcp",
    "django": "django",
    "flask": "flask",
    "fastapi": "fastapi",
    "spring": "spring",
    "next.js": "nextjs",
    "nextjs": "nextjs",
    "nest.js": "nestjs",
    "nestjs": "nestjs",
    "react native": "react-native",
    "react-native": "react-native",
    "tailwind": "tailwind",
    "tailwindcss": "tailwind",
}
_TAG_SHORTHANDS = {"go": "golang", "next": "nextjs", "nest": "nestjs"}
KNOWN_TAGS = frozenset(_TAG_ALIASES.values()) | {"golang"}
KNOWN_CITIES = frozenset(_CITY_ALIASES.values())


def _alias_pattern(key: str) -> re.Pattern[str]:
    left = r"(?<![a-z0-9])" if key[0].isalnum() else ""
    right = r"(?![a-z0-9])" if key[-1].isalnum() else ""
    return re.compile(left + re.escape(key) + right)


_TAG_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (_alias_pattern(key), canon) for key, canon in _TAG_ALIASES.items()
)

# "Go" is also a common word and brand name ("Go Fitness", "Pingo Doce & Go"),
# so it only counts as golang next to programming words.
_TAG_SPECIALS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"(?<![a-z])golang(?![a-z])"
            r"|(?<![a-z])go(?=\s+(?:developer|engineer|programmer|backend|dev)\b)"
            r"|(?<=\bem )go(?![a-z])|(?<=\bin )go(?![a-z])"
        ),
        "golang",
    ),
)


def _strip_accents(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(c for c in normalized if not unicodedata.combining(c))


def _clean(text: str | None) -> str:
    if not text:
        return ""
    return _WS.sub(" ", _strip_accents(text)).strip().lower()


def normalize_city(raw: str | None) -> str | None:
    """Map a raw location string to a canonical city slug, or None."""
    cleaned = _clean(raw)
    if not cleaned:
        return None
    for token in re.split(r"[,\-/]", cleaned):
        candidate = token.strip()
        if candidate in _CITY_ALIASES:
            return _CITY_ALIASES[candidate]
    return None


def normalize_work_mode(raw: str | None) -> str:
    """Classify a raw string as remote, hybrid, onsite, or unknown."""
    cleaned = _clean(raw)
    if not cleaned:
        return "unknown"
    for mode, pattern in _WORK_MODE_PATTERNS:
        if re.search(pattern, cleaned):
            return mode
    return "unknown"


def parse_salary(raw: str | None) -> tuple[int | None, int | None]:
    """Parse a salary string into a yearly EUR (min, max) tuple.

    Returns (None, None) when the amount is not clearly a EUR monthly or yearly
    figure (foreign currency, hourly rate, or no number). A bare 4-digit amount
    with no period is assumed monthly; a value >= 10000 or a k-suffix is yearly.
    """
    if not raw:
        return (None, None)
    text = raw.lower().replace("\xa0", " ")
    norm = _strip_accents(text)
    if ("$" in text or "usd" in norm) and "eur" not in norm and "€" not in text:
        return (None, None)
    if re.search(r"/\s*h\b|/\s*hora|\bhora\b|\bhour\b", norm):
        return (None, None)
    monthly = bool(re.search(r"\bmensa|\bmes\b|/\s*mes\b|\bmonth\b", norm))
    yearly = bool(re.search(r"\bano\b|\banual\b|\byear\b|/\s*ano\b|\bannual\b", norm))

    joined = re.sub(r"(?<=\d)[ .](?=\d{3}(?:\D|$))", "", text)
    matches = re.findall(r"(\d+(?:[.,]\d+)?)\s*(k)?", joined)
    parsed: list[tuple[float, bool]] = []
    for digits, k in matches:
        if not digits:
            continue
        value = float(digits.replace(",", "."))
        parsed.append((value * 1000, True) if k else (value, False))
    if not parsed:
        return (None, None)

    has_k = any(k for _, k in parsed)
    annuals: list[int] = []
    for value, k in parsed:
        if has_k and not k and value < 1000:
            value *= 1000
            k = True
        if monthly:
            annuals.append(round(value * 12))
        elif yearly or value >= 10000 or k:
            annuals.append(round(value))
        else:
            annuals.append(round(value * 12))
    return (min(annuals), max(annuals))


def normalize_tags(text: str | None) -> list[str]:
    """Extract canonical tech tags found in free text, sorted and unique."""
    if not text:
        return []
    low = _MARKUP.sub(" ", text).lower()
    found = {canon for pattern, canon in _TAG_PATTERNS if pattern.search(low)}
    found |= {canon for pattern, canon in _TAG_SPECIALS if pattern.search(low)}
    return sorted(found)


def canonical_tag(raw: str) -> str:
    """Map a user-typed tag (e.g. "Next", "node.js") to its canonical form."""
    key = _WS.sub(" ", raw.strip().lower())
    return _TAG_ALIASES.get(key) or _TAG_SHORTHANDS.get(key) or key


def normalize_seniority(title: str | None) -> str:
    """Classify a job title as intern, junior, mid, senior, lead, or unknown.

    The first level word in the title wins ("Senior Tech Lead" is senior).
    """
    match = _SENIORITY_LEVELS.search(_clean(title))
    return match.lastgroup or "unknown" if match else "unknown"


def levels_from_description(text: str | None) -> tuple[str, ...] | None:
    """Levels implied by the years of experience a description asks for.

    3 or more years is mid or senior, fewer (or "up to N") is junior or mid.
    None when the description asks for no years.
    """
    for match in _REQUIRED_YEARS.finditer(_clean(text)):
        groups = match.groups()
        up_to, number, unit = groups[:3] if groups[1] else groups[3:]
        years = int(number)
        # A singular "12 ano" is a school year; larger counts are the company's age.
        if (unit in ("ano", "year") and years != 1) or years > _MAX_REQUIRED_YEARS:
            continue
        return ("mid", "senior") if years >= 3 and not up_to else ("junior", "mid")
    return None


def normalize_title(raw: str | None) -> str:
    """Lowercase a job title and drop gender markers like (m/f)."""
    return _clean(_GENDER_MARKER.sub(" ", raw or ""))


def normalize_company(raw: str | None) -> str:
    """Lowercase a company name and drop legal suffixes like Lda or S.A."""
    base = _clean(raw)
    while True:
        stripped = _LEGAL_SUFFIX.sub("", base)
        if stripped == base:
            return base
        base = stripped


def fingerprint(title: str, company: str, city: str | None) -> str:
    """Stable cross-source identity hash from role, company, and city."""
    norm_title = _WS.sub(" ", _SENIORITY.sub("", normalize_title(title))).strip()
    key = _FP_SEP.join(
        (normalize_company(company), norm_title, normalize_city(city) or "")
    )
    return hashlib.sha1(key.encode("utf-8"), usedforsecurity=False).hexdigest()
