from __future__ import annotations

from functools import wraps
from typing import Any, Callable

from flask import redirect, request, session, url_for

from app.reservation_lab.db import get_connection
from app.reservation_lab.owner_credentials import authenticate_owner, has_owner_credentials_table

SESSION_LOGGED_IN = "owner_logged_in"
SESSION_RESTAURANT_ID = "owner_restaurant_id"
SESSION_OWNER_USERNAME = "owner_username"


def is_logged_in() -> bool:
    return bool(session.get(SESSION_LOGGED_IN))


def login_owner(*, restaurant_id: str, username: str | None = None) -> None:
    session[SESSION_LOGGED_IN] = True
    session[SESSION_RESTAURANT_ID] = restaurant_id.strip()
    if username:
        session[SESSION_OWNER_USERNAME] = username.strip()


def logout_owner() -> None:
    session.pop(SESSION_LOGGED_IN, None)
    session.pop(SESSION_RESTAURANT_ID, None)
    session.pop(SESSION_OWNER_USERNAME, None)


def session_restaurant_id() -> str:
    return str(session.get(SESSION_RESTAURANT_ID) or "").strip()


def verify_credentials(username: str, password: str) -> str | None:
    """Return restaurant_id on success, else None."""
    uname = username.strip()
    if not uname or not password:
        return None
    try:
        with get_connection() as conn:
            if not has_owner_credentials_table(conn):
                return None
            row = authenticate_owner(conn, uname, password)
    except Exception:
        return None
    if not row:
        return None
    return str(row.get("restaurant_id") or "").strip() or None


def login_required(view: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any):
        if is_logged_in():
            return view(*args, **kwargs)
        next_url = request.path
        if request.query_string:
            next_url = f"{next_url}?{request.query_string.decode('utf-8')}"
        return redirect(url_for("owner_login", next=next_url))

    return wrapped
