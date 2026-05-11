"""Shared helpers for API routes."""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

# Postgres reserved words we use as column names. When building dynamic
# UPDATE SQL from Pydantic field names, these need to be double-quoted to
# avoid the parser interpreting them as keywords.
_RESERVED = {"offset", "order", "user", "group", "table", "limit", "all"}


def sql_col(name: str) -> str:
    """Return the column name, double-quoted if it's a Postgres reserved word."""
    return f'"{name}"' if name in _RESERVED else name


def handle_integrity_error(exc: IntegrityError, resource: str) -> None:
    """Map a SQLAlchemy IntegrityError to an HTTPException with the right code.

    Postgres SQLSTATE codes (5 chars):
      23505  unique_violation
      23503  foreign_key_violation
      23502  not_null_violation
      23514  check_violation
    """
    code = getattr(getattr(exc, "orig", None), "pgcode", None)
    detail = ""
    if exc.orig is not None:
        # Postgres error messages look like:
        #   "duplicate key value violates unique constraint ...\nDETAIL: Key (...)"
        # Keep the first line; the DETAIL line is too verbose for an API response.
        detail = str(exc.orig).split("\nDETAIL:")[0].strip()

    if code == "23505":
        raise HTTPException(409, f"{resource} already exists: {detail}")
    if code == "23503":
        # Could be: caller referenced a missing parent (400),
        # or: caller tried to delete a parent that has children (409).
        # The error text typically indicates direction.
        if "is still referenced from table" in detail or "violates foreign key" in detail and "is still referenced" in detail:
            raise HTTPException(409, f"{resource} is referenced by other rows: {detail}")
        raise HTTPException(400, f"referenced row does not exist: {detail}")
    if code == "23502":
        raise HTTPException(400, f"missing required field: {detail}")
    if code == "23514":
        raise HTTPException(400, f"constraint violated: {detail}")

    raise HTTPException(400, f"database integrity error: {detail or exc}")
