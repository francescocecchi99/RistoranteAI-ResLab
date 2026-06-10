from __future__ import annotations

import uuid
from typing import Any

import psycopg
from werkzeug.security import check_password_hash, generate_password_hash

from app.reservation_lab.db import execute, fetch_one


def has_owner_credentials_table(conn: psycopg.Connection[Any]) -> bool:
    row = fetch_one(
        conn,
        """
        SELECT 1 AS ok
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'restaurant_owners'
        LIMIT 1
        """,
    )
    return row is not None


def get_owner_username(conn: psycopg.Connection[Any], restaurant_id: str) -> str | None:
    row = fetch_one(
        conn,
        """
        SELECT username
        FROM restaurant_owners
        WHERE restaurant_id = %s AND active = true
        LIMIT 1
        """,
        (restaurant_id,),
    )
    if not row:
        return None
    return str(row.get("username") or "").strip() or None


def authenticate_owner(
    conn: psycopg.Connection[Any], username: str, password: str
) -> dict[str, Any] | None:
    row = fetch_one(
        conn,
        """
        SELECT owner_id, restaurant_id, username, password_hash, active
        FROM restaurant_owners
        WHERE lower(username) = lower(%s)
        LIMIT 1
        """,
        (username.strip(),),
    )
    if not row or not row.get("active"):
        return None
    stored = str(row.get("password_hash") or "")
    if not stored or not check_password_hash(stored, password):
        return None
    return dict(row)


def upsert_owner_credentials(
    conn: psycopg.Connection[Any],
    *,
    restaurant_id: str,
    username: str,
    password: str,
) -> None:
    rid = restaurant_id.strip()
    uname = username.strip()
    if not rid or not uname:
        raise ValueError("restaurant_id and username are required")
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters")
    if len(uname) < 3:
        raise ValueError("Username must be at least 3 characters")

    password_hash = generate_password_hash(password)
    existing = fetch_one(
        conn,
        "SELECT owner_id FROM restaurant_owners WHERE restaurant_id = %s LIMIT 1",
        (rid,),
    )
    if existing:
        execute(
            conn,
            """
            UPDATE restaurant_owners
            SET username = %s, password_hash = %s, active = true, updated_at = now()
            WHERE restaurant_id = %s
            """,
            (uname, password_hash, rid),
        )
        return

    owner_id = "own_" + uuid.uuid4().hex[:12]
    execute(
        conn,
        """
        INSERT INTO restaurant_owners (owner_id, restaurant_id, username, password_hash, active)
        VALUES (%s, %s, %s, %s, true)
        """,
        (owner_id, rid, uname, password_hash),
    )
