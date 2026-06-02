"""Reconciler unit tests.

Each test pins one specific flag condition. Clean bills should produce
zero flags; intentionally broken bills should produce exactly the
flag we expect, and not more.
"""

from __future__ import annotations

from app.reconciler import reconcile
from app.schemas import (
    ExtractedAssignment,
    ExtractedBill,
    ExtractedItem,
)
from app.splitter import compute_split


def _clean_bill() -> ExtractedBill:
    return ExtractedBill(
        items=[ExtractedItem(name="Coffee", qty=1, unit_price="100", line_total="100")],
        printed_subtotal="100",
        printed_service="5",
        printed_tax="5",
        printed_total="110",
        people=["A", "B"],
        assignments=[ExtractedAssignment(item_index=0, shared_by_all=True)],
        payer="A",
    )


# --- happy path ---------------------------------------------------------
def test_clean_bill_produces_no_flags():
    bill = _clean_bill()
    result = compute_split(bill)
    flags, _ = reconcile(bill, result, "A and B shared coffee. A paid.")
    assert flags == []


def test_r1_fixture_is_clean(r1):
    bill, desc = r1
    result = compute_split(bill)
    flags, _ = reconcile(bill, result, desc)
    assert flags == [], f"R1 should reconcile cleanly, got: {flags}"


def test_r4_fixture_is_clean(r4):
    bill, desc = r4
    result = compute_split(bill)
    flags, _ = reconcile(bill, result, desc)
    assert flags == [], f"R4 should reconcile cleanly, got: {flags}"


# --- individual flag conditions ----------------------------------------
def test_line_items_dont_match_printed_subtotal():
    bill = ExtractedBill(
        items=[ExtractedItem(name="Coffee", qty=1, unit_price="100", line_total="100")],
        printed_subtotal="200",  # bogus
        printed_total="220",
        people=["A"],
        assignments=[ExtractedAssignment(item_index=0, people=["A"])],
        payer="A",
    )
    r = compute_split(bill)
    flags, _ = reconcile(bill, r, "A had coffee. A paid.")
    assert any("Line items sum to" in f for f in flags)


def test_missing_payer_is_flagged():
    bill = _clean_bill()
    bill.payer = None
    r = compute_split(bill)
    flags, _ = reconcile(bill, r, "A and B shared coffee.")
    assert any("Payer not stated" in f for f in flags)


def test_payer_not_in_people_list():
    bill = ExtractedBill(
        items=_clean_bill().items,
        printed_subtotal="100", printed_total="110",
        people=["A", "B"],
        assignments=_clean_bill().assignments,
        payer="Z",
    )
    r = compute_split(bill)
    flags, _ = reconcile(bill, r, "A and B shared. Z paid.")
    assert any("not in the diner list" in f for f in flags)


def test_empty_people_list_is_flagged():
    bill = ExtractedBill(
        items=_clean_bill().items,
        printed_subtotal="100", printed_total="110",
        people=[], assignments=[],
        payer=None,
    )
    r = compute_split(bill)
    flags, _ = reconcile(bill, r, "someone")
    assert any("No diners" in f for f in flags)


def test_duplicate_people_are_flagged():
    bill = ExtractedBill(
        items=_clean_bill().items,
        printed_subtotal="100", printed_total="110",
        people=["A", "B", "a"],
        assignments=_clean_bill().assignments,
        payer="A",
    )
    r = compute_split(bill)
    flags, _ = reconcile(bill, r, "A and B. A paid.")
    assert any("Duplicate diner names" in f for f in flags)


def test_vague_phrase_rest_of_us_is_flagged():
    bill = _clean_bill()
    r = compute_split(bill)
    flags, _ = reconcile(bill, r, "A had coffee, the rest of us shared. A paid.")
    assert any("rest of us" in f for f in flags)


def test_extractor_ambiguity_is_propagated():
    bill = _clean_bill()
    bill.ambiguities = ["unit price for line 3 is illegible"]
    r = compute_split(bill)
    flags, _ = reconcile(bill, r, "A and B shared. A paid.")
    assert any("Extractor noted:" in f for f in flags)


def test_description_mentions_item_not_on_bill():
    bill = _clean_bill()
    r = compute_split(bill)
    flags, _ = reconcile(bill, r, "A and B shared coffee and pizza. A paid.")
    assert any("but no matching item" in f for f in flags)


def test_synonym_paraphrase_does_not_flag():
    """'pasta' in description should match a bill item like 'Penne Arrabiata'."""
    bill = ExtractedBill(
        items=[ExtractedItem(name="Penne Arrabiata", qty=1, unit_price="320", line_total="320")],
        printed_subtotal="320", printed_total="320",
        people=["A"],
        assignments=[ExtractedAssignment(item_index=0, people=["A"])],
        payer="A",
    )
    r = compute_split(bill)
    flags, _ = reconcile(bill, r, "A had the pasta. A paid.")
    assert not any("but no matching item" in f for f in flags)
