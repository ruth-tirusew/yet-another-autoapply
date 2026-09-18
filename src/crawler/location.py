"""Parse job location strings into worldwide vs country-restricted eligibility."""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.settings import get_config

# ISO 3166-1 alpha-2 codes keyed by normalized country name / alias.
_COUNTRY_NAME_TO_CODE: dict[str, str] = {
    "afghanistan": "AF",
    "albania": "AL",
    "algeria": "DZ",
    "argentina": "AR",
    "armenia": "AM",
    "australia": "AU",
    "austria": "AT",
    "bangladesh": "BD",
    "belarus": "BY",
    "belgium": "BE",
    "bolivia": "BO",
    "bosnia and herzegovina": "BA",
    "brazil": "BR",
    "bulgaria": "BG",
    "cambodia": "KH",
    "canada": "CA",
    "chile": "CL",
    "china": "CN",
    "colombia": "CO",
    "costa rica": "CR",
    "croatia": "HR",
    "czech republic": "CZ",
    "czechia": "CZ",
    "denmark": "DK",
    "dominican republic": "DO",
    "ecuador": "EC",
    "egypt": "EG",
    "el salvador": "SV",
    "estonia": "EE",
    "ethiopia": "ET",
    "finland": "FI",
    "france": "FR",
    "georgia": "GE",
    "germany": "DE",
    "ghana": "GH",
    "greece": "GR",
    "guatemala": "GT",
    "honduras": "HN",
    "hungary": "HU",
    "india": "IN",
    "indonesia": "ID",
    "ireland": "IE",
    "israel": "IL",
    "italy": "IT",
    "japan": "JP",
    "jordan": "JO",
    "kenya": "KE",
    "latvia": "LV",
    "lithuania": "LT",
    "luxembourg": "LU",
    "malaysia": "MY",
    "mexico": "MX",
    "morocco": "MA",
    "nepal": "NP",
    "netherlands": "NL",
    "new zealand": "NZ",
    "nicaragua": "NI",
    "nigeria": "NG",
    "norway": "NO",
    "pakistan": "PK",
    "panama": "PA",
    "paraguay": "PY",
    "peru": "PE",
    "philippines": "PH",
    "poland": "PL",
    "portugal": "PT",
    "romania": "RO",
    "russia": "RU",
    "saudi arabia": "SA",
    "serbia": "RS",
    "singapore": "SG",
    "slovakia": "SK",
    "slovenia": "SI",
    "south africa": "ZA",
    "south korea": "KR",
    "spain": "ES",
    "sri lanka": "LK",
    "sweden": "SE",
    "switzerland": "CH",
    "taiwan": "TW",
    "thailand": "TH",
    "turkey": "TR",
    "ukraine": "UA",
    "united arab emirates": "AE",
    "united kingdom": "GB",
    "united states": "US",
    "uruguay": "UY",
    "usa": "US",
    "us": "US",
    "u.s.": "US",
    "u.s.a.": "US",
    "uk": "GB",
    "u.k.": "GB",
    "uae": "AE",
    "vietnam": "VN",
    "european union": "EU",
    "eu": "EU",
}

for _code in set(_COUNTRY_NAME_TO_CODE.values()):
    _COUNTRY_NAME_TO_CODE[_code.lower()] = _code

# Continent/region names keyed to the set of country codes they expand to.
# Only covers regions that show up in "Remote (<region>)" / "Remote - <region>"
# postings; deliberately a subset of _COUNTRY_NAME_TO_CODE so every expanded
# code is one we can already match against a candidate's country.
_REGION_NAME_TO_CODES: dict[str, frozenset[str]] = {
    "north america": frozenset({"US", "CA", "MX"}),
    "latin america": frozenset(
        {"MX", "BR", "AR", "CL", "CO", "PE", "EC", "BO", "PY", "UY", "CR", "GT", "HN", "NI", "PA", "DO", "SV"}
    ),
    "south america": frozenset({"BR", "AR", "CL", "CO", "PE", "EC", "BO", "PY", "UY"}),
    "emea": frozenset(
        {
            # Europe
            "AL", "AM", "AT", "BY", "BE", "BA", "BG", "HR", "CZ", "DK", "EE", "FI", "FR",
            "GE", "DE", "GR", "HU", "IE", "IT", "LV", "LT", "LU", "NL", "NO", "PL", "PT",
            "RO", "RU", "RS", "SK", "SI", "ES", "SE", "CH", "UA", "GB", "EU",
            # Middle East
            "IL", "JO", "SA", "AE", "TR",
            # Africa
            "DZ", "EG", "ET", "GH", "KE", "MA", "NG", "ZA",
        }
    ),
    "apac": frozenset(
        {"AU", "BD", "KH", "CN", "IN", "ID", "JP", "MY", "NP", "NZ", "PK", "PH", "SG", "KR", "LK", "TW", "TH", "VN"}
    ),
    "asia pacific": frozenset(
        {"AU", "BD", "KH", "CN", "IN", "ID", "JP", "MY", "NP", "NZ", "PK", "PH", "SG", "KR", "LK", "TW", "TH", "VN"}
    ),
    "asia-pacific": frozenset(
        {"AU", "BD", "KH", "CN", "IN", "ID", "JP", "MY", "NP", "NZ", "PK", "PH", "SG", "KR", "LK", "TW", "TH", "VN"}
    ),
}

_TRUE_WORLDWIDE_DEFAULTS = ("worldwide", "anywhere", "global", "international")
_REMOTE_DASH_RE = re.compile(r"remote\s*[-–—]\s*([^;|]+)", re.IGNORECASE)
_REMOTE_PAREN_RE = re.compile(r"remote\s*\(([^)]+)\)", re.IGNORECASE)
_COUNTRY_SPLIT_RE = re.compile(r"\s*(?:,|/|\band\b|\bor\b)\s*", re.IGNORECASE)
_LOCATED_IN_RE = re.compile(
    r"(?:must\s+(?:be\s+)?(?:located|based|reside|living)\s+in|"
    r"only\s+(?:for\s+)?(?:candidates\s+in|open\s+to)\s+|"
    r"(?:authorized|eligible)\s+to\s+work\s+in)\s+"
    r"([a-z][a-z\s.\-]{1,40})",
    re.IGNORECASE,
)
_ONLY_SUFFIX_RE = re.compile(r"^(.+?)\s+only$", re.IGNORECASE)


@dataclass(frozen=True)
class ParsedLocation:
    is_worldwide: bool
    allowed_country_codes: frozenset[str] | None
    restriction_label: str = ""


def _worldwide_markers() -> tuple[str, ...]:
    cfg = get_config()
    defaults = list(_TRUE_WORLDWIDE_DEFAULTS)
    custom = cfg.get("filters", {}).get("worldwide_markers") or []
    seen: set[str] = set()
    out: list[str] = []
    for marker in defaults + custom:
        key = str(marker).lower().strip()
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    return tuple(out)


def _restricted_markers() -> tuple[str, ...]:
    cfg = get_config()
    defaults = [
        "us only",
        "uk only",
        "eu only",
        "canada only",
        "usa only",
        "australia only",
        "germany only",
        "must be located in",
        "must reside in",
        "authorized to work in the us",
        "authorized to work in the united states",
    ]
    custom = cfg.get("filters", {}).get("restricted_markers") or []
    seen: set[str] = set()
    out: list[str] = []
    for marker in defaults + custom:
        key = str(marker).lower().strip()
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    return tuple(out)


def _normalize_token(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower().strip().rstrip("."))


def _token_to_code(token: str) -> str | None:
    token = _normalize_token(token)
    if not token:
        return None
    if token in _worldwide_markers():
        return None
    if token in _COUNTRY_NAME_TO_CODE:
        return _COUNTRY_NAME_TO_CODE[token]
    only = _ONLY_SUFFIX_RE.match(token)
    if only:
        return _token_to_code(only.group(1))
    if len(token) == 2 and token.upper() in set(_COUNTRY_NAME_TO_CODE.values()):
        return token.upper()
    return None


def _tokens_to_codes(text: str) -> set[str]:
    codes: set[str] = set()
    for part in _COUNTRY_SPLIT_RE.split(text):
        normalized = _normalize_token(part)
        region_codes = _REGION_NAME_TO_CODES.get(normalized)
        if region_codes:
            codes.update(region_codes)
            continue
        code = _token_to_code(part)
        if code:
            codes.add(code)
    return codes


def _extract_remote_country_codes(location: str) -> set[str]:
    codes: set[str] = set()
    for match in _REMOTE_DASH_RE.finditer(location):
        codes.update(_tokens_to_codes(match.group(1)))
    for match in _REMOTE_PAREN_RE.finditer(location):
        codes.update(_tokens_to_codes(match.group(1)))
    return codes


def _extract_description_country_codes(description: str) -> set[str]:
    codes: set[str] = set()
    desc = (description or "")[:4000]
    for match in _LOCATED_IN_RE.finditer(desc):
        codes.update(_tokens_to_codes(match.group(1)))
    desc_lower = desc.lower()
    for marker in _restricted_markers():
        if marker in desc_lower:
            tail = desc_lower.split(marker, 1)[-1][:60]
            codes.update(_tokens_to_codes(tail))
    return codes


def _location_is_bare_remote(loc: str) -> bool:
    normalized = _normalize_token(loc)
    return normalized in {"remote", "fully remote", "100% remote", "work from home", "wfh"}


def _has_worldwide_marker(text: str) -> bool:
    lower = _normalize_token(text)
    return any(marker in lower for marker in _worldwide_markers())


def parse_job_location(location: str = "", description: str = "") -> ParsedLocation:
    loc = (location or "").strip()
    loc_lower = loc.lower()
    desc = description or ""

    remote_codes = _extract_remote_country_codes(loc)
    desc_codes = _extract_description_country_codes(desc)
    allowed = remote_codes | desc_codes

    if loc_lower in _COUNTRY_NAME_TO_CODE:
        allowed.add(_COUNTRY_NAME_TO_CODE[loc_lower])

    only_match = _ONLY_SUFFIX_RE.match(loc_lower)
    if only_match:
        code = _token_to_code(only_match.group(1))
        if code:
            allowed.add(code)

    if _has_worldwide_marker(loc) and not allowed:
        return ParsedLocation(is_worldwide=True, allowed_country_codes=None)

    if allowed:
        if _has_worldwide_marker(loc) and len(allowed) == 0:
            return ParsedLocation(is_worldwide=True, allowed_country_codes=None)
        label = loc or ", ".join(sorted(allowed))
        return ParsedLocation(
            is_worldwide=False,
            allowed_country_codes=frozenset(allowed),
            restriction_label=label,
        )

    if any(marker in loc_lower for marker in _restricted_markers()):
        return ParsedLocation(
            is_worldwide=False,
            allowed_country_codes=frozenset(),
            restriction_label=loc,
        )

    if desc_codes:
        return ParsedLocation(
            is_worldwide=False,
            allowed_country_codes=frozenset(desc_codes),
            restriction_label=loc or "description restriction",
        )

    if _location_is_bare_remote(loc) or not loc:
        if any(marker in (desc or "").lower() for marker in _restricted_markers()):
            return ParsedLocation(
                is_worldwide=False,
                allowed_country_codes=frozenset(desc_codes),
                restriction_label="description restriction",
            )
        return ParsedLocation(is_worldwide=True, allowed_country_codes=None)

    if _has_worldwide_marker(loc):
        return ParsedLocation(is_worldwide=True, allowed_country_codes=None)

    code = _token_to_code(loc)
    if code:
        return ParsedLocation(
            is_worldwide=False,
            allowed_country_codes=frozenset({code}),
            restriction_label=loc,
        )

    return ParsedLocation(is_worldwide=True, allowed_country_codes=None)


def is_worldwide(location: str, description: str = "") -> bool:
    parsed = parse_job_location(location, description)
    if parsed.allowed_country_codes:
        return False
    if not parsed.is_worldwide and parsed.restriction_label:
        return False
    return parsed.is_worldwide


def candidate_matches_location(
    parsed: ParsedLocation,
    candidate_country: str,
) -> tuple[bool, str]:
    if parsed.is_worldwide or parsed.allowed_country_codes is None:
        return True, ""

    if not parsed.allowed_country_codes:
        return False, f"Location restricted: {parsed.restriction_label or 'unknown'}"

    cc = (candidate_country or "").upper()
    if not cc:
        return False, "Country-restricted role; set your country in profile"

    if cc in parsed.allowed_country_codes:
        return True, ""

    label = parsed.restriction_label or ", ".join(sorted(parsed.allowed_country_codes))
    return False, f"Restricted to {label}; candidate is {cc}"
