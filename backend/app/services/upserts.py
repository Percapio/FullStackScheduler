"""Shared upsert helpers for Customer and Assembly.

Lifted from transform.py so that services/jobs.py and transform.py share one
canonicalisation path for customer-name and part-number lookups.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Assembly, Customer


def upsert_customer(session: Session, name: str) -> Customer:
    """Find or create a Customer by exact name match.

    Pre:  name is non-empty (caller must validate before calling).
    Post: a Customer with the given name exists in the session; returned.
    """
    customer = session.execute(
        select(Customer).where(Customer.name == name)
    ).scalar_one_or_none()
    if customer is None:
        customer = Customer(name=name)
        session.add(customer)
        session.flush()
    return customer


def upsert_assembly_by_part_number(session: Session, part_number: str) -> Assembly:
    """Find or create an Assembly by part_number.

    Pre:  part_number is non-empty (caller must validate before calling).
    Post: an Assembly with the given part_number exists; returned.
          No metadata fields (mfg_notes, program_name, classifications) are
          touched — those remain the responsibility of the ingestion pipeline.
    """
    assembly = session.execute(
        select(Assembly).where(Assembly.part_number == part_number)
    ).scalar_one_or_none()
    if assembly is None:
        assembly = Assembly(part_number=part_number)
        session.add(assembly)
        session.flush()
    return assembly
