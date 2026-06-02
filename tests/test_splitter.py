"""Splitter unit tests.

The four sample receipts (R1-R4) are the contract. If any of these
break, the deploy is wrong. Edge tests cover paise drift, missing
payer, shared-subset items, and quantity arithmetic.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.schemas import (
    ExtractedAssignment,
    ExtractedBill,
    ExtractedItem,
)
from app.splitter import _proportional_split, compute_split


# =====================================================================
# R1-R4 contract tests
# =====================================================================
class TestSampleReceipts:
    """Each sample must reconcile and produce a sensible per-person table."""

    def test_R1_brew_and_bite(self, r1):
        bill, _ = r1
        result = compute_split(bill)
        assert result.grand_total == 1147
        assert result.reconciliation.matches_bill is True
        assert result.reconciliation.sum_of_person_totals == 1147
        assert result.paid_by == "Sameer"
        ravi = self._person(result, "Ravi")
        assert ravi.subtotal == 440
        assert ravi.discount_share == 0
        neha = self._person(result, "Neha")
        assert neha.subtotal == 440
        sameer = self._person(result, "Sameer")
        assert sameer.subtotal == 160
        owed = {(s.from_, s.to): s.amount for s in result.settle_up}
        assert ("Ravi", "Sameer") in owed
        assert ("Neha", "Sameer") in owed
        assert all(s.to == "Sameer" for s in result.settle_up)

    def test_R2_taj_thali(self, r2):
        bill, _ = r2
        result = compute_split(bill)
        assert result.grand_total == 1455
        assert result.reconciliation.matches_bill is True
        for name in ("Arjun", "Kabir", "Priya"):
            p = self._person(result, name)
            assert p.subtotal > 0

    def test_R3_pizza_bar(self, r3):
        bill, _ = r3
        result = compute_split(bill)
        assert result.grand_total == 1830
        assert result.reconciliation.matches_bill is True
        meera = self._person(result, "Meera")
        ishaan = self._person(result, "Ishaan")
        rohit = self._person(result, "Rohit")
        assert meera.total < ishaan.total
        assert meera.total < rohit.total
        # Ishaan and Rohit ate identical items, so subtotals match within
        # a 1₹ paise-rounding drift on shared lines.
        assert abs(ishaan.subtotal - rohit.subtotal) <= 1

    def test_R4_spice_route_with_discount(self, r4):
        bill, _ = r4
        result = compute_split(bill)
        assert result.grand_total == 1436
        assert result.reconciliation.matches_bill is True
        for p in result.per_person:
            assert p.discount_share <= 0
        farah = self._person(result, "Farah")
        anjali = self._person(result, "Anjali")
        assert abs(farah.discount_share) > abs(anjali.discount_share)

    @staticmethod
    def _person(result, name):
        return next(p for p in result.per_person if p.name == name)


# =====================================================================
# Fairness rule properties (R1-R4 act as concrete instances)
# =====================================================================
class TestFairnessRules:
    """Properties that must hold for every well-formed bill."""

    @pytest.mark.parametrize("fixture_name", ["r1", "r2", "r3", "r4"])
    def test_sum_of_person_totals_equals_grand_total(self, fixture_name, request):
        bill, _ = request.getfixturevalue(fixture_name)
        result = compute_split(bill)
        assert sum(p.total for p in result.per_person) == result.grand_total

    @pytest.mark.parametrize("fixture_name", ["r1", "r2", "r3", "r4"])
    def test_each_person_total_equals_components(self, fixture_name, request):
        bill, _ = request.getfixturevalue(fixture_name)
        result = compute_split(bill)
        for p in result.per_person:
            recomputed = p.subtotal + p.service_share + p.tax_share + p.discount_share
            assert abs(p.total - recomputed) <= 2, (
                f"{p.name}: sum-of-parts {recomputed} vs total {p.total}"
            )

    @pytest.mark.parametrize("fixture_name", ["r1", "r2", "r3", "r4"])
    def test_settle_up_targets_payer(self, fixture_name, request):
        bill, _ = request.getfixturevalue(fixture_name)
        result = compute_split(bill)
        if result.paid_by is None:
            return
        for s in result.settle_up:
            assert s.to == result.paid_by
            assert s.from_ != result.paid_by


# =====================================================================
# Edge cases
# =====================================================================
class TestEdgeCases:
    def test_single_person_bill(self):
        bill = ExtractedBill(
            items=[ExtractedItem(name="Espresso", qty=1, unit_price="120", line_total="120")],
            printed_subtotal="120",
            printed_service="6",
            printed_tax="6",
            printed_total="132",
            people=["Solo"],
            assignments=[ExtractedAssignment(item_index=0, people=["Solo"])],
            payer="Solo",
        )
        r = compute_split(bill)
        assert r.grand_total == 132
        assert len(r.per_person) == 1
        assert r.per_person[0].total == 132
        assert r.settle_up == []

    def test_missing_payer_totals_still_reconcile(self):
        """When payer is unknown, totals still sum to grand_total
        (residual is absorbed by largest-subtotal person)."""
        bill = ExtractedBill(
            items=[
                ExtractedItem(name="A", qty=1, unit_price="100", line_total="100"),
                ExtractedItem(name="B", qty=1, unit_price="50", line_total="50"),
            ],
            printed_subtotal="150",
            printed_service="7.50",
            printed_tax="7.88",
            printed_total="165",
            people=["X", "Y"],
            assignments=[
                ExtractedAssignment(item_index=0, people=["X"]),
                ExtractedAssignment(item_index=1, people=["Y"]),
            ],
            payer=None,
        )
        r = compute_split(bill)
        assert r.paid_by is None
        assert sum(p.total for p in r.per_person) == r.grand_total

    def test_shared_subset_paise_drift_is_corrected(self):
        """100 / 3 = 33.33 + 33.33 + 33.34 must still sum to 100 in displayed subtotals."""
        bill = ExtractedBill(
            items=[ExtractedItem(name="Cake", qty=1, unit_price="100", line_total="100")],
            printed_subtotal="100",
            printed_total="100",
            people=["A", "B", "C"],
            assignments=[ExtractedAssignment(item_index=0, people=["A", "B", "C"])],
            payer="A",
        )
        r = compute_split(bill)
        assert r.grand_total == 100
        assert sum(p.subtotal for p in r.per_person) == 100

    def test_quantity_3_split_among_2_people(self):
        bill = ExtractedBill(
            items=[ExtractedItem(name="Beer", qty=3, unit_price="200", line_total="600")],
            printed_subtotal="600",
            printed_total="600",
            people=["P", "Q"],
            assignments=[ExtractedAssignment(item_index=0, people=["P", "Q"])],
            payer="P",
        )
        r = compute_split(bill)
        assert r.grand_total == 600
        assert {p.subtotal for p in r.per_person} == {300}

    def test_unassigned_item_falls_to_all_with_flag(self):
        bill = ExtractedBill(
            items=[ExtractedItem(name="Mystery side", qty=1, unit_price="60", line_total="60")],
            printed_subtotal="60",
            printed_total="60",
            people=["A", "B"],
            assignments=[],
            payer="A",
        )
        r = compute_split(bill)
        assert any("no explicit assignment" in f for f in r.flags)
        assert r.grand_total == 60


# =====================================================================
# Proportional split utility
# =====================================================================
class TestProportionalSplit:
    def test_equal_weights_equal_shares(self):
        out = _proportional_split(Decimal("90"), {"A": Decimal("1"), "B": Decimal("1"), "C": Decimal("1")})
        assert out == {"A": Decimal("30.00"), "B": Decimal("30.00"), "C": Decimal("30.00")}

    def test_uneven_weights(self):
        out = _proportional_split(Decimal("100"), {"A": Decimal("75"), "B": Decimal("25")})
        assert out["A"] == Decimal("75.00")
        assert out["B"] == Decimal("25.00")

    def test_zero_weights_split_equally(self):
        out = _proportional_split(Decimal("60"), {"A": Decimal("0"), "B": Decimal("0")})
        assert out == {"A": Decimal("30.00"), "B": Decimal("30.00")}

    def test_empty_dict_returns_empty(self):
        assert _proportional_split(Decimal("100"), {}) == {}
