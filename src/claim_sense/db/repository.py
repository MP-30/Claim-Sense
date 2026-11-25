"""PostgreSQL access for policies, garages, claims, and audit events."""

from datetime import date
from decimal import Decimal

from sqlalchemy import delete, func, literal, select
from sqlalchemy.dialects.postgresql import insert

from claim_sense.db.session import SessionLocal
from claim_sense.models.models import AuditLog, Claim, Garage, Policy, PriorClaim


def _record(row) -> dict:
    fields = {}
    for key, value in row.items():
        if isinstance(value, date):
            value = value.isoformat()
        elif isinstance(value, Decimal):
            value = float(value)
        fields[key] = value
    return fields


def get_policy(policy_number: str) -> dict | None:
    statement = select(Policy.__table__).where(Policy.policy_number == policy_number.strip())
    with SessionLocal() as session:
        row = session.execute(statement).mappings().one_or_none()
    return _record(row) if row is not None else None


def count_prior_claims(policy_number: str, before_date: str, months: int = 12) -> int:
    cutoff = date.fromisoformat(before_date)
    # Preserve SQLite's forward rollover when the target month is shorter.
    start = literal(cutoff.replace(day=1)) + func.make_interval(0, -months, 0, cutoff.day - 1)
    statement = select(func.count()).select_from(PriorClaim).where(
        PriorClaim.policy_number == policy_number,
        PriorClaim.claim_date < cutoff,
        PriorClaim.claim_date >= start,
    )
    with SessionLocal() as session:
        return session.execute(statement).scalar_one()


def get_garage(garage_id: str) -> dict | None:
    statement = select(Garage.__table__).where(Garage.garage_id == garage_id.strip().upper())
    with SessionLocal() as session:
        row = session.execute(statement).mappings().one_or_none()
    return dict(row) if row is not None else None


def save_claim(claim_id: str, **fields) -> None:
    statement = insert(Claim).values(claim_id=claim_id, **fields)
    updated = {key: statement.excluded[key] for key in fields}
    updated["updated_at"] = func.now()
    statement = statement.on_conflict_do_update(index_elements=[Claim.claim_id], set_=updated)
    with SessionLocal.begin() as session:
        session.execute(statement)


def get_claim(claim_id: str) -> dict | None:
    statement = select(Claim.__table__).where(Claim.claim_id == claim_id)
    with SessionLocal() as session:
        row = session.execute(statement).mappings().one_or_none()
    if row is None:
        return None
    claim = _record(row)
    claim["details"] = claim.pop("details_json") or {}
    return claim


def create_claim(claim_id: str, **fields) -> bool:
    statement = insert(Claim).values(claim_id=claim_id, **fields)
    statement = statement.on_conflict_do_nothing(index_elements=[Claim.claim_id]).returning(Claim.claim_id)
    with SessionLocal.begin() as session:
        return session.execute(statement).scalar_one_or_none() is not None


def list_claims(status: str | None = None) -> list[dict]:
    statement = select(
        Claim.claim_id, Claim.policy_number, Claim.status, Claim.route, Claim.recommendation,
        Claim.fraud_score, Claim.payable_amount, Claim.final_decision, Claim.final_amount,
        Claim.reviewer, Claim.updated_at,
    ).order_by(Claim.updated_at.desc(), Claim.claim_id)
    if status:
        statement = statement.where(Claim.status == status)
    with SessionLocal() as session:
        return [_record(row) for row in session.execute(statement).mappings()]


def reset_claim(claim_id: str) -> None:
    """Remove a previous run when reprocessing a local sample claim."""
    with SessionLocal.begin() as session:
        session.execute(delete(AuditLog).where(AuditLog.claim_id == claim_id))
        session.execute(delete(Claim).where(Claim.claim_id == claim_id))


def add_audit(claim_id: str, step: str, actor: str, details: dict) -> None:
    with SessionLocal.begin() as session:
        session.add(AuditLog(claim_id=claim_id, step=step, actor=actor, details=details))


def get_audit(claim_id: str) -> list[dict]:
    statement = select(AuditLog.ts, AuditLog.step, AuditLog.actor, AuditLog.details).where(
        AuditLog.claim_id == claim_id
    ).order_by(AuditLog.id)
    with SessionLocal() as session:
        return [_record(row) for row in session.execute(statement).mappings()]
