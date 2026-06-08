from __future__ import annotations

import os
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator, Iterable

from dotenv import load_dotenv
import psycopg
from psycopg.errors import ConnectionTimeout
from psycopg.rows import dict_row

def _connect_timeout_sec() -> int:
    """TCP connect timeout (libpq). Override with DATABASE_CONNECT_TIMEOUT in .env (5-120)."""
    try:
        n = int((os.getenv("DATABASE_CONNECT_TIMEOUT") or "25").strip())
    except ValueError:
        return 25
    return max(5, min(n, 120))


def _parsed_pg_host(url: str) -> str:
    """Hostname from a postgres URI (uses last @ so user:pass is not split on @ in password)."""
    try:
        after_scheme = url.split("://", 1)[-1]
        if "@" not in after_scheme:
            return ""
        tail = after_scheme.rsplit("@", 1)[-1]
        hostport = tail.split("/")[0].split("?")[0]
        return hostport.split(":")[0].lower()
    except (IndexError, ValueError):
        return ""

# Load repo-root .env regardless of the shell's current working directory.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_ENV_PATH = _REPO_ROOT / ".env"
# utf-8-sig: .env saved from Windows/Notepad often has a BOM; without it the key becomes "\ufeffDATABASE_URL".
# override=True: a pre-existing empty DATABASE_URL in the shell/IDE would otherwise block values from .env.
load_dotenv(_ENV_PATH, encoding="utf-8-sig", override=True)


def _validate_database_url(url: str) -> None:
    """Catch common Supabase template mistakes before DNS / auth fails with obscure errors."""
    if not url.startswith(("postgresql://", "postgres://")):
        raise RuntimeError(
            "DATABASE_URL deve iniziare con postgresql:// (o postgres://). "
            f"File controllato: {_ENV_PATH}"
        )
    low = url.lower()
    if (
        "<project_ref>" in low
        or "<db_password>" in low
        or re.search(r"db\.<[^>]+>\.supabase\.co", url, re.IGNORECASE)
    ):
        raise RuntimeError(
            "DATABASE_URL contiene ancora segnaposti del template (es. <PROJECT_REF>, <DB_PASSWORD>). "
            "Non vanno lasciati nella stringa finale.\n\n"
            "Cosa fare: Supabase -> Connect -> copia la URI (per reti domestiche: Session pooler, non Direct). "
            "Una sola riga in .env:\n"
            "  DATABASE_URL=postgresql://...\n\n"
            "La password e quella del database Postgres (Database settings).\n"
            f"File .env: {_ENV_PATH}"
        )
    if "<your-password" in low or "<db_password>" in low or "[your-password]" in low:
        raise RuntimeError(
            "DATABASE_URL contiene ancora un segnaposto per la password. "
            "Sostituiscilo con la password del database Postgres del progetto (Database settings), "
            "non con chiavi API OpenAI o con la service role key.\n"
            f"File .env usato: {_ENV_PATH}"
        )
    # postgresql://user:password@host - need @ before hostname (common paste typo: password glued to db.)
    after_scheme = url.split("://", 1)[-1] if "://" in url else url
    if "@" not in after_scheme:
        raise RuntimeError(
            "DATABASE_URL: manca il carattere @ tra password e host.\n\n"
            "Formato corretto:\n"
            "  postgresql://postgres:LA_PASSWORD@db.REF.supabase.co:5432/postgres?sslmode=require\n\n"
            "Subito dopo la password deve esserci @, poi db.... (non attaccare la password a db.).\n"
            f"File .env: {_ENV_PATH}"
        )
    host = _parsed_pg_host(url)
    if host and _is_supabase_direct_db_host(host):
        if os.getenv("DATABASE_ALLOW_DIRECT_DB_HOST", "").lower() not in ("1", "true", "yes"):
            raise RuntimeError(
                "DATABASE_URL usa ancora la connessione Direct (host db.xxxxx.supabase.co). "
                "Su molte reti domestiche / Windows va in timeout (solo IPv4 verso Internet).\n\n"
                "Cosa fare: Supabase - Connect - Connection Method: Session pooler - Type URI - Copy. "
                "Sostituisci tutta la riga DATABASE_URL in .env con quella URI "
                "(host tipo aws-0-REGION.pooler.supabase.com, utente spesso postgres.TUO_REF).\n\n"
                "Solo se la Direct ti funziona davvero (IPv6 ok), aggiungi in .env:\n"
                "  DATABASE_ALLOW_DIRECT_DB_HOST=1\n"
                f"File .env: {_ENV_PATH}"
            )


def _is_supabase_direct_db_host(host: str) -> bool:
    return bool(re.fullmatch(r"db\.[0-9a-z]{8,40}\.supabase\.co", host))


def get_database_url() -> str:
    database_url = (os.getenv("DATABASE_URL") or "").strip()
    if not database_url:
        raise RuntimeError(
            "DATABASE_URL non e impostata. Aggiungila in .env (una riga DATABASE_URL=...).\n"
            f"File atteso: {_ENV_PATH} (esiste: {_ENV_PATH.is_file()})"
        )
    _validate_database_url(database_url)
    if "supabase" in database_url.lower() and "sslmode=" not in database_url.lower():
        sep = "&" if "?" in database_url else "?"
        database_url = f"{database_url}{sep}sslmode=require"
    return database_url


@contextmanager
def get_connection() -> Generator[psycopg.Connection[Any], None, None]:
    try:
        conn = psycopg.connect(
            get_database_url(),
            connect_timeout=_connect_timeout_sec(),
            row_factory=dict_row,
        )
    except psycopg.OperationalError as exc:
        msg = str(exc).lower()
        if isinstance(exc, ConnectionTimeout) or "timeout" in msg or "timed out" in msg:
            raise RuntimeError(
                f"Timeout di connessione al database (oltre {_connect_timeout_sec()}s). "
                "Controlla rete, firewall e VPN.\n\n"
                "Se Supabase segnala 'Not IPv4 compatible': la URI Direct (host db....supabase.co) spesso non passa "
                "da reti solo-IPv4. Usa invece la connection string del Session pooler: "
                "Dashboard -> Connect / Project Settings -> Database -> "
                "Connection pooling -> Session mode -> copia la URI e mettila in DATABASE_URL nel file .env "
                "(host tipo aws-0-....pooler.supabase.com, utente spesso postgres.TUO_REF).\n\n"
                f"Errore originale: {exc}\n"
                f"File .env: {_ENV_PATH}"
            ) from exc
        if "getaddrinfo" in msg or "11001" in msg or "name or service not known" in msg:
            raise RuntimeError(
                "Connessione fallita (spesso letto come errore DNS).\n\n"
                "Controlla @ tra password e host e l'host corretto. "
                "Su rete IPv4-only usa la URI Session pooler da Supabase (non la Direct db....supabase.co).\n\n"
                f"Errore originale: {exc}\n"
                f"File .env: {_ENV_PATH}"
            ) from exc
        if "password authentication failed" in msg:
            raise RuntimeError(
                "Password rifiutata per l'utente postgres: la password nella DATABASE_URL non coincide con quella "
                "del database su Supabase (Project Settings -> Database), oppure nella URI non e codificata correttamente "
                "(caratteri come @ : # / ? devono essere percent-encoded). "
                "Puoi reimpostare la password del database dalla dashboard e aggiornare .env.\n"
                f"File .env: {_ENV_PATH}"
            ) from exc
        raise

    # Mimic psycopg.Connection.__exit__: commit open transaction on success, else rollback.
    # Otherwise SELECTs from compute_availability start a transaction; INSERTs run in a nested
    # savepoint; closing without commit rolls back and nothing persists (e.g. Create reservation).
    try:
        yield conn
    except BaseException:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    else:
        try:
            conn.commit()
        except Exception:
            pass
    finally:
        if not conn.closed:
            conn.close()


def fetch_one(conn: psycopg.Connection[Any], query: str, params: Iterable[Any] | None = None) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(query, params or ())
        row = cur.fetchone()
    return dict(row) if row else None


def fetch_all(conn: psycopg.Connection[Any], query: str, params: Iterable[Any] | None = None) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(query, params or ())
        rows = cur.fetchall()
    return [dict(r) for r in rows]


def execute(conn: psycopg.Connection[Any], query: str, params: Iterable[Any] | None = None) -> None:
    with conn.cursor() as cur:
        cur.execute(query, params or ())
