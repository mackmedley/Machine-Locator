"""Parsing and relevance scoring for for-sale listings.

Search results from business brokerages are noisy: a query for "vending" pulls
in laundromats, ATM routes, single machines being flipped on Craigslist, and
franchise pitches. This module decides what is actually a *route* -- a running
book of machines with revenue -- and pulls the numbers out of the ad copy.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

# --------------------------------------------------------------- vocabulary

STRONG_TERMS = (
    "vending route",
    "vending machine route",
    "snack route",
    "soda route",
    "beverage route",
    "vending business",
    "vending company",
    "vending operation",
    "micro market",
    "micromarket",
    "established vending",
    "profitable vending",
    "vending machine business",
)

MEDIUM_TERMS = (
    "vending",
    "vending machines",
    "amusement route",
    "atm route",
    "coffee service",
    "ocs route",
)

# Ads that mention vending but are not a route business for sale.
NEGATIVE_TERMS = (
    "vending machine parts",
    "vending machine repair",
    "vending machine service manual",
    "parts only",
    "for parts",
    "machine only",
    "single machine",
    "one machine",
    "empty machine",
    "vending machine wanted",
    "looking to buy",
    "wanted: vending",
)

# Language that shows up in biz-op pitches rather than genuine route sales.
CAUTION_TERMS = (
    "no experience necessary",
    "guaranteed income",
    "turnkey opportunity",
    "we place your machines",
    "locations guaranteed",
    "business opportunity package",
    "financing available for everyone",
)

OKC_METRO_TERMS = (
    "oklahoma city",
    "okc",
    "edmond",
    "moore, ok",
    "norman",
    "yukon",
    "mustang",
    "midwest city",
    "del city",
    "bethany",
    "warr acres",
    "the village",
    "nichols hills",
    "choctaw",
    "harrah",
    "newcastle",
    "piedmont",
    "guthrie",
    "shawnee",
    "el reno",
)

OKLAHOMA_TERMS = ("oklahoma", "okla")

_STATE_ABBREV = re.compile(r"(?:^|[,\s])ok(?:[,\s]|$)|,\s*ok\s*\d{5}", re.IGNORECASE)


# ------------------------------------------------------------------ parsing

_MULTIPLIERS = {"k": 1_000.0, "m": 1_000_000.0, "mm": 1_000_000.0, "b": 1_000_000_000.0}

_PRICE_RE = re.compile(
    r"\$\s*([\d,]+(?:\.\d+)?)\s*(k|m|mm|b)?\b", re.IGNORECASE
)


def parse_money(text: Optional[str]) -> Optional[float]:
    """Pull the first dollar figure out of a string.

    Handles ``$45,000``, ``$45K``, ``$1.2M``. Returns None when the text says
    something unhelpful like "Price on request".
    """
    if not text:
        return None
    match = _PRICE_RE.search(text)
    if not match:
        return None
    number = match.group(1).replace(",", "")
    try:
        value = float(number)
    except ValueError:
        return None
    suffix = (match.group(2) or "").lower()
    if suffix in _MULTIPLIERS:
        value *= _MULTIPLIERS[suffix]
    return value


_COUNT_RE = re.compile(
    r"(\d{1,4})\s*(?:\+\s*)?(?:vending\s+)?machines?\b", re.IGNORECASE
)
_COUNT_RE_ALT = re.compile(
    r"(?:route\s+of|consists?\s+of|includes?)\s+(\d{1,4})\b", re.IGNORECASE
)


def parse_machine_count(text: Optional[str]) -> Optional[int]:
    """Best-effort machine count from ad copy like '32 machines on location'."""
    if not text:
        return None
    for pattern in (_COUNT_RE, _COUNT_RE_ALT):
        match = pattern.search(text)
        if match:
            try:
                count = int(match.group(1))
            except ValueError:
                continue
            if 1 <= count <= 2000:
                return count
    return None


_REVENUE_LABELS = (
    ("cash flow", "cash_flow"),
    ("sde", "cash_flow"),
    ("seller's discretionary", "cash_flow"),
    ("net profit", "cash_flow"),
    ("net income", "cash_flow"),
    ("gross revenue", "gross_revenue"),
    ("gross income", "gross_revenue"),
    ("annual sales", "gross_revenue"),
    ("gross sales", "gross_revenue"),
    ("revenue", "gross_revenue"),
)


def parse_financials(text: Optional[str]) -> Dict[str, Optional[float]]:
    """Find labelled money figures ('Cash Flow: $38,000') in a blob of text."""
    result: Dict[str, Optional[float]] = {"cash_flow": None, "gross_revenue": None}
    if not text:
        return result
    lowered = text.lower()
    for label, key in _REVENUE_LABELS:
        if result[key] is not None:
            continue
        index = lowered.find(label)
        if index == -1:
            continue
        window = text[index : index + len(label) + 40]
        value = parse_money(window)
        if value is not None:
            result[key] = value
    return result


# -------------------------------------------------------------- relevance

def locality(text: str) -> Tuple[bool, str]:
    """(is_okc_metro, state) for a listing's location text."""
    lowered = (text or "").lower()
    is_metro = any(term in lowered for term in OKC_METRO_TERMS)
    is_state = (
        is_metro
        or any(term in lowered for term in OKLAHOMA_TERMS)
        or bool(_STATE_ABBREV.search(text or ""))
    )
    return is_metro, "OK" if is_state else ""


def score_relevance(
    title: str, description: str = "", location_text: str = ""
) -> Tuple[float, List[str], bool]:
    """Score a listing 0-100 for "is this a vending route I could buy?".

    Returns (score, reasons, is_okc_metro).
    """
    blob = " ".join(part for part in (title, description) if part).lower()
    reasons: List[str] = []
    score = 0.0

    matched_strong = [term for term in STRONG_TERMS if term in blob]
    if matched_strong:
        score += 55.0
        reasons.append(f"matches '{matched_strong[0]}'")
    else:
        matched_medium = [term for term in MEDIUM_TERMS if term in blob]
        if matched_medium:
            score += 28.0
            reasons.append(f"mentions '{matched_medium[0]}'")
        else:
            return 0.0, ["no vending terms found"], False

    matched_negative = [term for term in NEGATIVE_TERMS if term in blob]
    if matched_negative:
        score -= 45.0
        reasons.append(f"looks like it is not a route: '{matched_negative[0]}'")

    matched_caution = [term for term in CAUTION_TERMS if term in blob]
    if matched_caution:
        score -= 12.0
        reasons.append(f"biz-op sales language: '{matched_caution[0]}' -- verify carefully")

    is_metro, state = locality(location_text or blob)
    if is_metro:
        score += 30.0
        reasons.append("in the OKC metro")
    elif state == "OK":
        score += 18.0
        reasons.append("elsewhere in Oklahoma")

    # Concrete numbers are a good sign that a real book of business is on offer.
    if parse_machine_count(blob) is not None:
        score += 8.0
        reasons.append("states a machine count")
    financials = parse_financials(blob)
    if financials["cash_flow"] is not None or financials["gross_revenue"] is not None:
        score += 9.0
        reasons.append("discloses revenue or cash flow")

    return max(0.0, min(100.0, score)), reasons, is_metro
