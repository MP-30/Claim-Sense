from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Numeric, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Policy(Base):
    __tablename__ = "policies"

    policy_number: Mapped[str] = mapped_column(primary_key=True)
    holder_name: Mapped[str | None]
    phone: Mapped[str | None]
    vehicle_reg: Mapped[str | None]
    make_model: Mapped[str | None]
    engine_cc: Mapped[int | None]
    registration_date: Mapped[date | None]
    policy_type: Mapped[str | None]
    start_date: Mapped[date | None]
    end_date: Mapped[date | None]
    idv: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    zero_dep: Mapped[bool | None]


class PriorClaim(Base):
    __tablename__ = "prior_claims"
    __table_args__ = (
        Index("ix_prior_claims_policy_number_claim_date", "policy_number", "claim_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    policy_number: Mapped[str | None] = mapped_column(ForeignKey("policies.policy_number"))
    claim_date: Mapped[date | None]
    amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))


class Garage(Base):
    __tablename__ = "garages"

    garage_id: Mapped[str] = mapped_column(primary_key=True)
    name: Mapped[str | None]
    city: Mapped[str | None]
    watchlist: Mapped[bool | None]


class Claim(Base):
    __tablename__ = "claims"

    claim_id: Mapped[str] = mapped_column(primary_key=True)
    # Keep the submitted policy number even when the policy lookup fails.
    policy_number: Mapped[str | None]
    docs_dir: Mapped[str | None]
    thread_id: Mapped[str | None]
    status: Mapped[str | None]
    route: Mapped[str | None]
    recommendation: Mapped[str | None]
    fraud_score: Mapped[int | None]
    payable_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    final_decision: Mapped[str | None]
    final_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    reviewer: Mapped[str | None]
    details_json: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    letter: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_claim_id_id", "claim_id", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    claim_id: Mapped[str | None] = mapped_column(ForeignKey("claims.claim_id"))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    step: Mapped[str | None]
    actor: Mapped[str | None]
    details: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
