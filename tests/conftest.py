"""pytest fixtures shared across the test suite."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.schemas import ExtractedAssignment, ExtractedBill, ExtractedItem

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sample_extractions.json"


def _build(raw: dict) -> tuple[ExtractedBill, str]:
    """Build an ExtractedBill from one fixture entry. Returns (bill, description)."""
    items = [ExtractedItem(**i) for i in raw["items"]]
    assignments = [
        ExtractedAssignment(
            item_index=a["item_index"],
            people=a.get("people", []),
            shared_by_all=a.get("shared_by_all", False),
        )
        for a in raw["assignments"]
    ]
    bill = ExtractedBill(
        items=items,
        printed_subtotal=raw.get("printed_subtotal"),
        printed_service=raw.get("printed_service"),
        printed_tax=raw.get("printed_tax"),
        printed_discount=raw.get("printed_discount"),
        printed_total=raw.get("printed_total"),
        people=raw["people"],
        assignments=assignments,
        payer=raw.get("payer"),
        ambiguities=raw.get("ambiguities", []),
    )
    return bill, raw.get("description", "")


@pytest.fixture(scope="session")
def fixtures() -> dict[str, dict]:
    return json.loads(FIXTURE_PATH.read_text())


@pytest.fixture()
def r1(fixtures):
    return _build(fixtures["R1_brew_and_bite"])


@pytest.fixture()
def r2(fixtures):
    return _build(fixtures["R2_tamarind_kitchen"])


@pytest.fixture()
def r3(fixtures):
    return _build(fixtures["R3_daily_grind"])


@pytest.fixture()
def r4(fixtures):
    return _build(fixtures["R4_spice_route"])
