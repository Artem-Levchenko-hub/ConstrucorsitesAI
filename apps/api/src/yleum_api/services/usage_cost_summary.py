"""Classify stored customer costs without recomputing prices or reading provider calls."""

import re
from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import Literal

from sqlalchemy import Numeric, and_, case, cast, func
from sqlalchemy.sql.elements import ColumnElement

from yleum_api.models.usage import Usage
from yleum_api.schemas.billing import UsageCostBreakdownPublic

CostKind = Literal["confirmed", "estimated", "unknown"]
ESTIMATE_BASES = ("token_tariff", "local_token_estimate", "provider_catalog_estimate")
# At most 80 characters and a two-digit exponent keep every accepted value far
# within PostgreSQL's unconstrained NUMERIC range, including negative zero.
NONNEGATIVE_RUB = r"^(\+?([0-9]+(\.[0-9]*)?|\.[0-9]+)|-0+(\.0*)?)([eE][+-]?[0-9]{1,2})?$"


def classify_cost(provenance: object, stored_cost: Decimal) -> CostKind:
    if not isinstance(provenance, dict) or type(provenance.get("schema_version")) is not int:
        return "unknown"
    if provenance["schema_version"] != 1:
        return "unknown"

    def amount(key: str) -> Decimal | None:
        value = provenance.get(key)
        if isinstance(value, str) and len(value) <= 80:
            value = value.strip(" ")
            if re.fullmatch(NONNEGATIVE_RUB, value):
                return Decimal(value)
        return None

    effective, calculated, reported = (
        amount(key) for key in ("effective_cost_rub", "calculated_cost_rub", "reported_cost_rub")
    )
    if effective is None or calculated is None:
        return "unknown"
    with localcontext() as context:
        context.prec = 200
        rounded = effective.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    if rounded != stored_cost:
        return "unknown"
    basis = provenance.get("basis")
    if basis == "provider_reported_rub" and reported == effective:
        return "confirmed"
    if (basis in ESTIMATE_BASES and "reported_cost_rub" in provenance
            and provenance["reported_cost_rub"] is None and calculated == effective):
        return "estimated"
    return "unknown"


def cost_classification() -> ColumnElement[str]:
    provenance = Usage.cost_provenance
    basis = provenance["basis"].astext
    # CASE guards the cast itself: SQL may reorder AND conditions. Never cast to
    # NUMERIC(12,4), which can overflow; round safe NUMERIC and compare saved cost.
    def amount(key: str) -> ColumnElement[Decimal]:
        raw = provenance[key].astext
        stripped = func.btrim(raw)
        valid = and_(
            func.jsonb_typeof(provenance[key]) == "string",
            func.length(raw) <= 80,
            stripped.op("~")(NONNEGATIVE_RUB),
        )
        return case((valid, cast(stripped, Numeric())), else_=None)

    effective, calculated, reported = (
        amount(key) for key in ("effective_cost_rub", "calculated_cost_rub", "reported_cost_rub")
    )
    supported = and_(
        func.jsonb_typeof(provenance) == "object",
        func.jsonb_typeof(provenance["schema_version"]) == "number",
        provenance["schema_version"].astext == "1",
        calculated.is_not(None),
        func.round(effective, 4) == Usage.cost_rub,
    )
    return case(
        (and_(supported, basis == "provider_reported_rub", reported == effective), "confirmed"),
        (and_(supported, basis.in_(ESTIMATE_BASES), calculated == effective,
              func.jsonb_typeof(provenance["reported_cost_rub"]) == "null"), "estimated"),
        else_="unknown",
    )


def add_cost(summary: UsageCostBreakdownPublic, kind: str, calls: int, cost: Decimal) -> None:
    bucket = getattr(summary, kind if kind in ("confirmed", "estimated", "unknown") else "unknown")
    bucket.calls += calls
    bucket.cost_rub += cost


def merge_costs(*summaries: UsageCostBreakdownPublic) -> UsageCostBreakdownPublic:
    result = UsageCostBreakdownPublic()
    for summary in summaries:
        for kind in ("confirmed", "estimated", "unknown"):
            bucket = getattr(summary, kind)
            add_cost(result, kind, bucket.calls, bucket.cost_rub)
    return result
