"""Pydantic models for the /split request/response contract.

Two layers:

1. **Wire models** — exactly the shape the brief specifies, used at the
   HTTP boundary. Field order in ``model_dump()`` matches the spec.
2. **Internal models** — what the extractor returns and what the
   splitter consumes. Kept separate so the wire contract can evolve
   independently of how we represent intermediate state.

All money values on the wire are integer rupees (rounded). Internal
math uses ``Decimal`` for paise-precise accumulation; conversion to
int happens in the splitter's final rounding step.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# =====================================================================
# Wire: request
# =====================================================================
class SplitRequest(BaseModel):
    """Inbound request body for ``POST /split``."""

    model_config = ConfigDict(extra="forbid")

    receipt_base64: str = Field(
        ...,
        min_length=16,
        description="Base64-encoded image bytes. No data-URI prefix.",
    )
    description: str = Field(
        ...,
        min_length=1,
        max_length=4000,
        description="Free-text 'who had what' description; should also name the payer.",
    )

    @field_validator("receipt_base64")
    @classmethod
    def _strip_data_uri(cls, v: str) -> str:
        """Be lenient: strip an accidental ``data:image/...;base64,`` prefix."""
        v = v.strip()
        if v.startswith("data:") and "," in v:
            v = v.split(",", 1)[1]
        return v


# =====================================================================
# Wire: response
# =====================================================================
class PersonSplit(BaseModel):
    """One row of the per-person table on the wire."""

    name: str
    items: list[str]
    subtotal: int
    tax_share: int
    service_share: int
    discount_share: int
    total: int


class Reconciliation(BaseModel):
    sum_of_person_totals: int
    matches_bill: bool


class SettleUp(BaseModel):
    from_: str = Field(..., alias="from")
    to: str
    amount: int

    model_config = ConfigDict(populate_by_name=True)


class SplitResponse(BaseModel):
    """Outbound response body for ``POST /split``.

    Field order here is the order they appear in ``model_dump()`` and
    therefore in the serialized JSON — matching the brief example.
    """

    per_person: list[PersonSplit]
    grand_total: int
    reconciliation: Reconciliation
    paid_by: Optional[str]
    settle_up: list[SettleUp]
    assumptions: list[str]
    flags: list[str]


# =====================================================================
# Internal: what the extractor returns
# =====================================================================
class ExtractedItem(BaseModel):
    """One line item as parsed from the bill image."""

    name: str
    qty: int = Field(default=1, ge=1)
    unit_price: Decimal = Field(default=Decimal("0"))
    line_total: Decimal


class ExtractedAssignment(BaseModel):
    """Who consumed item at ``item_index`` (0-based into ``items``).

    ``people`` is the explicit list of names. ``shared_by_all`` is a
    convenience flag we resolve against the full people list before the
    splitter sees it.
    """

    item_index: int = Field(..., ge=0)
    people: list[str] = Field(default_factory=list)
    shared_by_all: bool = False


class ExtractedBill(BaseModel):
    """Structured bill data — the Gemini extractor's only output.

    Critically: this contains no derived totals. The splitter computes
    everything; printed_* fields exist only for reconciliation.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    items: list[ExtractedItem]
    printed_subtotal: Optional[Decimal] = None
    printed_service: Optional[Decimal] = None
    printed_tax: Optional[Decimal] = None
    printed_discount: Optional[Decimal] = None  # positive number; sign applied in math
    printed_total: Optional[Decimal] = None
    people: list[str] = Field(default_factory=list)
    assignments: list[ExtractedAssignment] = Field(default_factory=list)
    payer: Optional[str] = None
    ambiguities: list[str] = Field(default_factory=list)


# =====================================================================
# Internal: what the splitter returns to the API layer
# =====================================================================
class SplitResult(BaseModel):
    """Splitter output — already rounded, ready to serialize.

    The API layer wraps this in ``SplitResponse`` and merges
    reconciler-generated flags.
    """

    per_person: list[PersonSplit]
    grand_total: int
    reconciliation: Reconciliation
    paid_by: Optional[str]
    settle_up: list[SettleUp]
    assumptions: list[str]
    flags: list[str]

    def to_response(self) -> SplitResponse:
        return SplitResponse(
            per_person=self.per_person,
            grand_total=self.grand_total,
            reconciliation=self.reconciliation,
            paid_by=self.paid_by,
            settle_up=self.settle_up,
            assumptions=self.assumptions,
            flags=self.flags,
        )
