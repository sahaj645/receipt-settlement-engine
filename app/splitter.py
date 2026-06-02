"""Fairness math — pure functions, no I/O.

This module is the single source of truth for *how* a bill is split.
Everything is deterministic, ``Decimal``-precise during accumulation,
and rounds to whole rupees only at the very end.

Fairness rules (from the brief, exactly):

1. Each person pays for the items they consumed.
2. Shared items split equally among the people who shared *that
   specific item* (not all diners).
3. Tax + service charge allocated proportional to each person's
   pre-tax subtotal.
4. Bill-level discount allocated proportional to subtotal.
5. Round to the rupee; leftover paise are absorbed by the payer.
   (If no payer is known, the largest-subtotal person absorbs them
   and we flag it.)

The splitter does NOT decide whether the bill reconciles against the
printed total — that's the reconciler's job. The splitter only
guarantees ``sum(person.total) == grand_total`` after rounding.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from app.schemas import (
    ExtractedBill,
    PersonSplit,
    Reconciliation,
    SettleUp,
    SplitResult,
)

# Use 2dp precision internally (paise); the final cast to int rounds to rupees.
_PAISE = Decimal("0.01")
_ZERO = Decimal("0")


# =====================================================================
# Helpers
# =====================================================================
def _q(x: Decimal | int | float | str | None) -> Decimal:
    """Coerce to Decimal at paise precision. None -> 0."""
    if x is None:
        return _ZERO
    return Decimal(str(x)).quantize(_PAISE, rounding=ROUND_HALF_UP)


def _proportional_split(
    pool: Decimal, weights: dict[str, Decimal]
) -> dict[str, Decimal]:
    """Split ``pool`` across ``weights`` proportional to weight value.

    If all weights are zero (degenerate: no one ate anything), split equally
    across all keys. Returns paise-precision Decimals.
    """
    total_weight = sum(weights.values(), _ZERO)
    if total_weight == _ZERO:
        if not weights:
            return {}
        share = (pool / Decimal(len(weights))).quantize(_PAISE, rounding=ROUND_HALF_UP)
        return {name: share for name in weights}

    return {
        name: (pool * w / total_weight).quantize(_PAISE, rounding=ROUND_HALF_UP)
        for name, w in weights.items()
    }


def _format_item_label(name: str, qty: int, headcount: int) -> str:
    """Render an item label for the per-person ``items`` list.

    Examples:
        ``Cappuccino`` (qty 1, sole eater)
        ``Pasta (1/2)`` (qty 1, shared with one other person)
        ``Chicken Biryani x2`` (qty 2, sole eater)
    """
    base = name if qty == 1 else f"{name} x{qty}"
    if headcount > 1:
        return f"{base} (1/{headcount})"
    return base


# =====================================================================
# Core: compute a split
# =====================================================================
def compute_split(extracted: ExtractedBill) -> SplitResult:
    """Run the fairness rules and return a rounded, reconciled split.

    The returned ``SplitResult`` has:

    * ``per_person`` totals that sum exactly to ``grand_total``,
    * a ``reconciliation`` block comparing internal sum vs grand total,
    * ``settle_up`` rows (everyone non-payer owes the payer their total),
    * ``assumptions`` describing rounding decisions,
    * ``flags`` *only* for things the splitter itself notices; the
      reconciler appends more flags later.
    """
    assumptions: list[str] = []
    flags: list[str] = []

    # ---------- 0. normalize people list ----------
    people = list(dict.fromkeys(extracted.people))  # dedupe, preserve order

    # ---------- 1. allocate items to people ----------
    # Each person's pre-tax subtotal accumulates here (paise precision).
    subtotals: dict[str, Decimal] = {p: _ZERO for p in people}
    # Per-person human-readable item labels.
    person_items: dict[str, list[str]] = {p: [] for p in people}

    for item in extracted.items:
        # Find this item's assignment (by index in extracted.items)
        idx = extracted.items.index(item)
        assignment = next(
            (a for a in extracted.assignments if a.item_index == idx), None
        )

        if assignment is None or (
            not assignment.people and not assignment.shared_by_all
        ):
            # Unassigned line — falls to everyone equally and we flag it.
            eaters = list(people)
            if not eaters:
                flags.append(
                    f"Item '{item.name}' has no assignment and no people to fall back on; skipping."
                )
                continue
            flags.append(
                f"Item '{item.name}' had no explicit assignment; split equally among all {len(eaters)} diners."
            )
        elif assignment.shared_by_all:
            eaters = list(people)
        else:
            eaters = [p for p in assignment.people if p in subtotals]
            missing = [p for p in assignment.people if p not in subtotals]
            if missing:
                flags.append(
                    f"Item '{item.name}' assigned to unknown person(s) {missing}; ignored those names."
                )
            if not eaters:
                flags.append(
                    f"Item '{item.name}' has no valid eaters after filtering; skipping."
                )
                continue

        line_total = _q(item.line_total)
        per_head = (line_total / Decimal(len(eaters))).quantize(
            _PAISE, rounding=ROUND_HALF_UP
        )

        # Fix paise drift on the line so the line's parts sum to line_total exactly.
        residual = line_total - per_head * len(eaters)
        for i, eater in enumerate(eaters):
            share = per_head + (residual if i == 0 else _ZERO)
            subtotals[eater] += share
            person_items[eater].append(
                _format_item_label(item.name, item.qty, len(eaters))
            )

    # ---------- 2. compute service/tax/discount pools ----------
    subtotal_total = sum(subtotals.values(), _ZERO)
    service_pool = _q(extracted.printed_service)
    tax_pool = _q(extracted.printed_tax)
    discount_pool = _q(extracted.printed_discount)  # positive magnitude

    service_shares = _proportional_split(service_pool, subtotals)
    tax_shares = _proportional_split(tax_pool, subtotals)
    discount_shares = _proportional_split(discount_pool, subtotals)

    # ---------- 3. compute pre-rounding per-person totals ----------
    pre_round_totals: dict[str, Decimal] = {}
    for p in people:
        pre_round_totals[p] = (
            subtotals[p]
            + service_shares.get(p, _ZERO)
            + tax_shares.get(p, _ZERO)
            - discount_shares.get(p, _ZERO)
        ).quantize(_PAISE, rounding=ROUND_HALF_UP)

    # ---------- 4. determine grand_total ----------
    # Trust the printed grand_total if present; else compute it from pools.
    if extracted.printed_total is not None:
        grand_total_decimal = _q(extracted.printed_total)
    else:
        grand_total_decimal = (
            subtotal_total + service_pool + tax_pool - discount_pool
        ).quantize(_PAISE, rounding=ROUND_HALF_UP)

    grand_total = int(grand_total_decimal.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    # ---------- 5. round each person to whole rupees, balance to grand_total ----------
    rounded: dict[str, int] = {
        p: int(t.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        for p, t in pre_round_totals.items()
    }
    diff = grand_total - sum(rounded.values())

    payer = extracted.payer if extracted.payer in subtotals else None
    if extracted.payer and payer is None:
        flags.append(
            f"Stated payer '{extracted.payer}' is not in the people list; treating payer as unknown."
        )

    if diff != 0 and rounded:
        absorber = payer
        if absorber is None:
            # No payer — assign to largest-subtotal person, with a flag.
            absorber = max(rounded, key=lambda p: subtotals[p])
            assumptions.append(
                f"No payer stated; assigned {diff:+d} rupee rounding residual to '{absorber}' (largest subtotal)."
            )
        else:
            assumptions.append(
                f"Payer '{absorber}' absorbs {diff:+d} rupee rounding residual to balance to grand total."
            )
        rounded[absorber] += diff

    # ---------- 6. round component shares (cosmetic; informational only) ----------
    def _round_int(d: Decimal) -> int:
        return int(d.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    per_person: list[PersonSplit] = []
    for p in people:
        per_person.append(
            PersonSplit(
                name=p,
                items=person_items[p],
                subtotal=_round_int(subtotals[p]),
                tax_share=_round_int(tax_shares.get(p, _ZERO)),
                service_share=_round_int(service_shares.get(p, _ZERO)),
                discount_share=-_round_int(discount_shares.get(p, _ZERO)),
                total=rounded[p],
            )
        )

    sum_person_totals = sum(pp.total for pp in per_person)
    reconciliation = Reconciliation(
        sum_of_person_totals=sum_person_totals,
        matches_bill=(sum_person_totals == grand_total),
    )

    # ---------- 7. settle-up ----------
    settle_up = _build_settle_up(per_person, payer)

    return SplitResult(
        per_person=per_person,
        grand_total=grand_total,
        reconciliation=reconciliation,
        paid_by=payer,
        settle_up=settle_up,
        assumptions=assumptions,
        flags=flags,
    )


# =====================================================================
# Settle-up
# =====================================================================
def _build_settle_up(
    per_person: list[PersonSplit], payer: Optional[str]
) -> list[SettleUp]:
    """Everyone who isn't the payer owes the payer their total.

    If payer is unknown, return an empty list — the reconciler will
    flag this separately. We don't invent a transfer graph.
    """
    if payer is None:
        return []
    out: list[SettleUp] = []
    for pp in per_person:
        if pp.name == payer or pp.total == 0:
            continue
        out.append(SettleUp(**{"from": pp.name, "to": payer, "amount": pp.total}))
    return out
