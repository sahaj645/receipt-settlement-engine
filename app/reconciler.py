"""Cross-check the extraction and split, surface anything that smells off.

The reconciler is pure — input goes in, flags come out. It runs *after*
the splitter so it can compare:

* sum(line_totals)        vs printed_subtotal
* computed grand total    vs printed_total
* description content     vs item names on the bill
* payer presence and validity
* people-list sanity (empty, duplicates, etc.)
* negative or zero subtotals

The brief is explicit: **flag discrepancies, don't silently fix them.**
This module is the enforcer of that rule.
"""

from __future__ import annotations

import re
from decimal import Decimal

from app.schemas import ExtractedBill, SplitResult

# Tolerance for "close enough" Decimal comparisons. ₹1 rounding tolerance
# is generous; the brief's R3 example has a +0.10 round-off line.
_RUPEE_TOLERANCE = Decimal("1.00")


# =====================================================================
# Public entry
# =====================================================================
def reconcile(
    extracted: ExtractedBill,
    split: SplitResult,
    description: str,
) -> tuple[list[str], list[str]]:
    """Return ``(new_flags, new_assumptions)`` to merge into the response.

    Does not mutate ``split``. The caller is responsible for appending
    the returned lists to ``split.flags`` and ``split.assumptions``.
    """
    flags: list[str] = []
    assumptions: list[str] = []

    flags.extend(_check_line_items_sum(extracted))
    flags.extend(_check_grand_total(extracted, split))
    flags.extend(_check_payer(extracted))
    flags.extend(_check_people(extracted))
    flags.extend(_check_subtotals(split))
    flags.extend(_check_description_items(extracted, description))
    flags.extend(_check_ambiguity_phrases(description, extracted))
    flags.extend(_propagate_model_ambiguities(extracted))

    return flags, assumptions


# =====================================================================
# Individual checks
# =====================================================================
def _check_line_items_sum(extracted: ExtractedBill) -> list[str]:
    """sum(line_totals) should match the printed subtotal."""
    if extracted.printed_subtotal is None or not extracted.items:
        return []
    line_sum = sum((Decimal(str(i.line_total)) for i in extracted.items), Decimal("0"))
    printed = Decimal(str(extracted.printed_subtotal))
    diff = line_sum - printed
    if abs(diff) > _RUPEE_TOLERANCE:
        return [
            f"Line items sum to {line_sum:.2f} but printed subtotal is "
            f"{printed:.2f} — gap of {diff:+.2f} unexplained."
        ]
    return []


def _check_grand_total(extracted: ExtractedBill, split: SplitResult) -> list[str]:
    """Computed grand total should be within ₹1 of the printed grand total."""
    if extracted.printed_total is None:
        return ["No printed grand total found on the bill; using computed total."]
    printed = Decimal(str(extracted.printed_total))
    diff = Decimal(split.grand_total) - printed
    if abs(diff) > _RUPEE_TOLERANCE:
        return [
            f"Computed grand total ₹{split.grand_total} differs from printed "
            f"₹{printed} by {diff:+}; flagged for review."
        ]
    return []


def _check_payer(extracted: ExtractedBill) -> list[str]:
    """Payer must be explicitly stated and present in the people list."""
    if extracted.payer is None:
        return [
            "Payer not stated in description. Cannot generate settle-up — "
            "please re-submit with 'X paid the bill'."
        ]
    if extracted.people and extracted.payer not in extracted.people:
        return [
            f"Stated payer '{extracted.payer}' is not in the diner list "
            f"({extracted.people}); settle-up may be incomplete."
        ]
    return []


def _check_people(extracted: ExtractedBill) -> list[str]:
    """The diner list must be non-empty and free of duplicates."""
    flags: list[str] = []
    if not extracted.people:
        flags.append("No diners identified in the description; cannot split the bill.")
        return flags

    seen: set[str] = set()
    dupes: list[str] = []
    for p in extracted.people:
        key = p.strip().lower()
        if key in seen:
            dupes.append(p)
        seen.add(key)
    if dupes:
        flags.append(f"Duplicate diner names detected: {dupes}. De-duplicated for splitting.")
    return flags


def _check_subtotals(split: SplitResult) -> list[str]:
    """No person should owe a negative amount (would mean over-discount)."""
    flags: list[str] = []
    for p in split.per_person:
        if p.subtotal < 0:
            flags.append(f"Negative subtotal computed for {p.name}: ₹{p.subtotal}.")
        if p.total < 0:
            flags.append(
                f"Negative total for {p.name}: ₹{p.total} "
                "(discount exceeds their share — verify the discount line)."
            )
    return flags


# --- description vs bill cross-checks ----------------------------------
def _check_description_items(
    extracted: ExtractedBill, description: str
) -> list[str]:
    """Catch items mentioned in the description that aren't on the bill.

    We use a simple word-overlap check, not full NLP. False positives are
    fine here — over-flagging is safer than silently dropping items.
    """
    if not description or not extracted.items:
        return []

    # Lowercased food-token list from bill: split on whitespace, drop short tokens.
    bill_tokens: set[str] = set()
    for item in extracted.items:
        for tok in re.findall(r"[A-Za-z]+", item.name.lower()):
            if len(tok) >= 4:
                bill_tokens.add(tok)

    # Common food/drink words a person might name. Conservative list — only
    # flags when a clearly-noun food word in the description has no overlap
    # with the bill text at all.
    suspect_words = re.findall(
        r"\b(pasta|pizza|biryani|sandwich|burger|salad|cheesecake|brownie|"
        r"cappuccino|latte|mojito|beer|wine|soda|juice|coffee|tea|garlic|"
        r"bread|rice|noodles|momos|tikka|kebab|roll|wrap|fries|nachos)\b",
        description.lower(),
    )
    missing: list[str] = []
    for word in set(suspect_words):
        if not any(word in tok or tok in word for tok in bill_tokens):
            missing.append(word)
    if missing:
        return [
            f"Description mentions {sorted(missing)} but no matching item on the bill; "
            "those references were ignored."
        ]
    return []


def _check_ambiguity_phrases(
    description: str, extracted: ExtractedBill
) -> list[str]:
    """Catch 'rest of us', 'everyone else', etc. when context is thin."""
    if not description:
        return []
    phrases = [
        "rest of us",
        "rest of them",
        "everyone else",
        "the others",
        "all of us",
    ]
    lowered = description.lower()
    hits = [p for p in phrases if p in lowered]
    if hits and extracted.people:
        # The extractor should have resolved these; if it didn't (no
        # ambiguity entry exists), at least state our assumption.
        return [
            f"Detected vague phrasing {hits}; assumed it refers to all "
            f"diners listed: {extracted.people}."
        ]
    return []


def _propagate_model_ambiguities(extracted: ExtractedBill) -> list[str]:
    """Surface any ambiguity notes the extractor flagged directly."""
    return [f"Extractor noted: {note}" for note in extracted.ambiguities]
