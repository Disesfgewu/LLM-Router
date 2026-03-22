#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
auth.py — Two-tier API key security layer

Key types
---------
  Full key  (mk_<64hex>)   No expiry, no scope restriction.
                            Issued only by an authenticated admin.
  Agent key (ma_<64hex>)   Mandatory expiry (default 24 h), scoped endpoints.
                            Safe to hand to automated agents.

Security properties
-------------------
  Passwords  : PBKDF2-HMAC-SHA256, 260 000 iterations, 32-byte random salt.
  API keys   : secrets.token_hex(32) — full value NEVER stored.
               Only SHA-256 hash kept in DB.
  Sessions   : secrets.token_urlsafe(32), 24 h TTL.
               Stored in React memory only (never written to localStorage).
  Rate limit : per-key sliding-window counter, in-process memory.
  IP whitelist: per-account CIDR list; empty = allow all.
  Timing     : hmac.compare_digest used for all constant-time comparisons.

Endpoint scopes (used to restrict agent keys)
---------------------------------------------
  "chat"         /v1/chat/completions
  "completions"  /v1/completions
  "direct_query" /v1/direct_query
  "images"       /v1/images/generations
  "file"         /v1/file/generate_content
  "models"       /v1/models               (read-only info)

Admin endpoints (/admin/*) are session-only; API keys cannot reach them.
"""

import os
import json
import hmac
import time
import hashlib
import secrets
import sqlite3
import base64
import ipaddress
import logging
import threading
from collections import defaultdict, deque
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List, Tuple

logger = logging.getLogger("api.auth")

# ── Paths & constants ───────────────────────────────────────
_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "auth.db")

_PBKDF2_ITERATIONS  = 260_000
_SESSION_TTL_HOURS  = 24
_FULL_KEY_PREFIX    = "mk_"
_AGENT_KEY_PREFIX   = "ma_"
_KNOWN_PREFIXES     = (_FULL_KEY_PREFIX, _AGENT_KEY_PREFIX)

ALLOWED_SCOPES = {"chat", "completions", "direct_query", "images", "file", "models", "embeddings"}

# Single-device hardening defaults (can be overridden by env vars)
_DEFAULT_AGENT_MAX_HOURS = int(os.getenv("AUTH_AGENT_MAX_HOURS", "24"))
_DEFAULT_AGENT_DEFAULT_HOURS = int(os.getenv("AUTH_AGENT_DEFAULT_HOURS", "1"))
_DEFAULT_AGENT_MAX_RPM = int(os.getenv("AUTH_AGENT_MAX_RPM", "20"))

# ── In-memory rate-limit state ──────────────────────────────
_rate_lock: threading.Lock = threading.Lock()
_rate_windows: Dict[str, deque] = defaultdict(deque)   # key_hash → deque[timestamp]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Database helpers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@contextmanager
def _get_conn():
    conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create / migrate all auth tables. Safe to call multiple times."""
    with _get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS accounts (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT    UNIQUE NOT NULL,
                email         TEXT    UNIQUE NOT NULL,
                password_hash TEXT    NOT NULL,
                is_active     INTEGER NOT NULL DEFAULT 1,
                is_admin      INTEGER NOT NULL DEFAULT 0,
                created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS sessions (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                token      TEXT    UNIQUE NOT NULL,
                expires_at TEXT    NOT NULL,
                created_at TEXT    NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS api_keys (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id   INTEGER NOT NULL,
                name         TEXT    NOT NULL,
                key_type     TEXT    NOT NULL DEFAULT 'full',
                prefix       TEXT    NOT NULL,
                key_hash     TEXT    NOT NULL UNIQUE,
                scope        TEXT,
                expires_at   TEXT,
                rpm_limit    INTEGER NOT NULL DEFAULT 0,
                is_active    INTEGER NOT NULL DEFAULT 1,
                last_used_at TEXT,
                created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS ip_whitelist (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id  INTEGER NOT NULL,
                ip_cidr     TEXT    NOT NULL,
                description TEXT,
                created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS key_audit_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                key_id      INTEGER,
                account_id  INTEGER,
                action      TEXT    NOT NULL,
                client_ip   TEXT,
                endpoint    TEXT,
                created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
            );
        """)
    logger.info("Auth DB initialised at %s", _DB_PATH)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Password utilities
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def hash_password(plain: str) -> str:
    """Return base64( salt[32] ‖ PBKDF2-SHA256 key[32] )."""
    salt = os.urandom(32)
    key = hashlib.pbkdf2_hmac("sha256", plain.encode(), salt, _PBKDF2_ITERATIONS)
    return base64.b64encode(salt + key).decode()


def verify_password(plain: str, stored: str) -> bool:
    """Constant-time password check."""
    try:
        data = base64.b64decode(stored.encode())
        salt, stored_key = data[:32], data[32:]
        key = hashlib.pbkdf2_hmac("sha256", plain.encode(), salt, _PBKDF2_ITERATIONS)
        return hmac.compare_digest(key, stored_key)
    except Exception:
        return False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Account management
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def register_account(username: str, email: str, password: str) -> Dict[str, Any]:
    """
    Create a new account.
    The FIRST account ever created becomes admin automatically.
    Raises ValueError on duplicate or invalid input.
    """
    username = username.strip()
    email = email.strip().lower()
    if not username or len(username) < 3:
        raise ValueError("帳號名稱至少需要 3 個字元")
    if len(password) < 8:
        raise ValueError("密碼至少需要 8 個字元")
    if "@" not in email:
        raise ValueError("電子郵件格式錯誤")

    pw_hash = hash_password(password)

    with _get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
        is_admin = 1 if count == 0 else 0
        try:
            cur = conn.execute(
                "INSERT INTO accounts (username, email, password_hash, is_admin) VALUES (?,?,?,?)",
                (username, email, pw_hash, is_admin),
            )
        except sqlite3.IntegrityError:
            raise ValueError("帳號名稱或電子郵件已被使用")

        row = conn.execute(
            "SELECT id, username, email, is_active, is_admin, created_at FROM accounts WHERE id=?",
            (cur.lastrowid,),
        ).fetchone()
        return dict(row)


def get_account_by_id(account_id: int) -> Optional[Dict[str, Any]]:
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT id, username, email, is_active, is_admin, created_at FROM accounts WHERE id=?",
            (account_id,),
        ).fetchone()
        return dict(row) if row else None


def get_account_by_username(username: str) -> Optional[Dict[str, Any]]:
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT id, username, email, password_hash, is_active, is_admin, created_at FROM accounts WHERE username=?",
            (username.strip(),),
        ).fetchone()
        return dict(row) if row else None


def list_all_accounts() -> List[Dict[str, Any]]:
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT id, username, email, is_active, is_admin, created_at FROM accounts ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]


def set_account_active(account_id: int, active: bool) -> bool:
    with _get_conn() as conn:
        cur = conn.execute(
            "UPDATE accounts SET is_active=? WHERE id=?",
            (1 if active else 0, account_id),
        )
        return cur.rowcount > 0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Session management
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def login(username: str, password: str) -> Optional[Dict[str, Any]]:
    """
    Verify credentials and issue a session token.
    Returns session dict on success, None on failure.
    Constant-time even for missing users (prevents enumeration).
    """
    acct = get_account_by_username(username)
    # Always run the hash to prevent timing-based user enumeration
    dummy = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=="
    stored = acct["password_hash"] if acct else dummy
    valid = verify_password(password, stored)

    if not acct or not valid or not acct.get("is_active"):
        return None

    token = secrets.token_urlsafe(32)
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=_SESSION_TTL_HOURS)).isoformat()

    with _get_conn() as conn:
        conn.execute(
            "INSERT INTO sessions (account_id, token, expires_at) VALUES (?,?,?)",
            (acct["id"], token, expires_at),
        )

    return {
        "token": token,
        "expires_at": expires_at,
        "account_id": acct["id"],
        "username": acct["username"],
        "is_admin": bool(acct["is_admin"]),
    }


def validate_session(token: str) -> Optional[Dict[str, Any]]:
    """Validate a session token; auto-deletes expired sessions. Returns account info or None."""
    if not token:
        return None
    with _get_conn() as conn:
        row = conn.execute(
            """SELECT s.account_id, s.expires_at,
                      a.username, a.email, a.is_active, a.is_admin
               FROM sessions s
               JOIN accounts a ON a.id = s.account_id
               WHERE s.token = ?""",
            (token,),
        ).fetchone()

        if not row or not row["is_active"]:
            return None

        try:
            exp = datetime.fromisoformat(row["expires_at"])
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) > exp:
                conn.execute("DELETE FROM sessions WHERE token=?", (token,))
                return None
        except Exception:
            return None

        return {
            "account_id": row["account_id"],
            "username": row["username"],
            "email": row["email"],
            "is_admin": bool(row["is_admin"]),
        }


def logout(token: str) -> None:
    with _get_conn() as conn:
        conn.execute("DELETE FROM sessions WHERE token=?", (token,))


def purge_expired_sessions() -> None:
    with _get_conn() as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at < datetime('now')")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  API Key management
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _check_rate_limit(key_hash: str, rpm: int) -> bool:
    """
    Sliding-window rate limiter (in-process memory).
    Returns True if the request is within the limit.
    """
    now = time.monotonic()
    window_start = now - 60.0
    with _rate_lock:
        q = _rate_windows[key_hash]
        # Drop timestamps outside the 60-second window
        while q and q[0] < window_start:
            q.popleft()
        if len(q) >= rpm:
            return False
        q.append(now)
        return True


def _is_localhost_ip(client_ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    return addr.is_loopback


def generate_full_key(account_id: int, name: str) -> Tuple[str, Dict[str, Any]]:
    """
    Generate a full-access API key (mk_).
    No scope restriction, no expiry.
    Returns (full_key, record).  full_key shown to user exactly once.
    """
    raw = secrets.token_hex(32)
    full_key = f"{_FULL_KEY_PREFIX}{raw}"
    prefix = f"{_FULL_KEY_PREFIX}{raw[:8]}"
    key_hash = _hash_api_key(full_key)

    with _get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO api_keys
               (account_id, name, key_type, prefix, key_hash, scope, expires_at, rpm_limit)
               VALUES (?,?,?,?,?,NULL,NULL,0)""",
            (account_id, name.strip(), "full", prefix, key_hash),
        )
        row = conn.execute(
            "SELECT id, account_id, name, key_type, prefix, scope, expires_at, rpm_limit, is_active, created_at FROM api_keys WHERE id=?",
            (cur.lastrowid,),
        ).fetchone()
        return full_key, dict(row)


def generate_agent_key(
    account_id: int,
    name: str,
    scopes: List[str],
    expires_hours: int = 24,
    rpm_limit: int = 60,
) -> Tuple[str, Dict[str, Any]]:
    """
    Generate a restricted agent API key (ma_).
    - scopes:        list of allowed endpoint scopes (from ALLOWED_SCOPES)
    - expires_hours: TTL in hours (1-8760, default 24)
    - rpm_limit:     max requests per minute (0 = unlimited, default 60)
    Returns (full_key, record).  full_key shown to user exactly once.
    """
    bad_scopes = [s for s in scopes if s not in ALLOWED_SCOPES]
    if bad_scopes:
        raise ValueError(f"不合法的 scope: {bad_scopes}。允許的值: {sorted(ALLOWED_SCOPES)}")
    if not scopes:
        raise ValueError("Agent key 至少需要一個 scope")

    # Clamp to safer defaults for single-device operation.
    safe_default_hours = max(1, _DEFAULT_AGENT_DEFAULT_HOURS)
    safe_max_hours = max(safe_default_hours, _DEFAULT_AGENT_MAX_HOURS)
    safe_max_rpm = max(1, _DEFAULT_AGENT_MAX_RPM)

    expires_hours = int(expires_hours or safe_default_hours)
    expires_hours = max(1, min(expires_hours, safe_max_hours))
    rpm_limit = int(rpm_limit or safe_max_rpm)
    rpm_limit = max(1, min(rpm_limit, safe_max_rpm))

    logger.info(
        "Issuing agent key with hardened policy: expires_hours=%s (max=%s), rpm_limit=%s (max=%s)",
        expires_hours,
        safe_max_hours,
        rpm_limit,
        safe_max_rpm,
    )

    expires_at = (datetime.now(timezone.utc) + timedelta(hours=expires_hours)).isoformat()
    scope_json = json.dumps(sorted(set(scopes)))

    raw = secrets.token_hex(32)
    full_key = f"{_AGENT_KEY_PREFIX}{raw}"
    prefix = f"{_AGENT_KEY_PREFIX}{raw[:8]}"
    key_hash = _hash_api_key(full_key)

    with _get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO api_keys
               (account_id, name, key_type, prefix, key_hash, scope, expires_at, rpm_limit)
               VALUES (?,?,?,?,?,?,?,?)""",
            (account_id, name.strip(), "agent", prefix, key_hash, scope_json, expires_at, rpm_limit),
        )
        row = conn.execute(
            "SELECT id, account_id, name, key_type, prefix, scope, expires_at, rpm_limit, is_active, created_at FROM api_keys WHERE id=?",
            (cur.lastrowid,),
        ).fetchone()
        return full_key, dict(row)


def list_api_keys(account_id: int) -> List[Dict[str, Any]]:
    with _get_conn() as conn:
        rows = conn.execute(
            """SELECT id, name, key_type, prefix, scope, expires_at, rpm_limit,
                      is_active, last_used_at, created_at
               FROM api_keys WHERE account_id=? ORDER BY created_at DESC""",
            (account_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def revoke_api_key(key_id: int, account_id: int) -> bool:
    with _get_conn() as conn:
        cur = conn.execute(
            "UPDATE api_keys SET is_active=0 WHERE id=? AND account_id=?",
            (key_id, account_id),
        )
        return cur.rowcount > 0


def validate_api_key(
    raw_key: str,
    client_ip: Optional[str] = None,
    endpoint_scope: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Validate a raw API key string.
    Checks: existence, is_active, account active, expiry, scope, IP whitelist, rate limit.
    Writes audit log on success.
    Returns account info dict or None.
    """
    if not raw_key:
        return None
    if not any(raw_key.startswith(p) for p in _KNOWN_PREFIXES):
        return None

    key_hash = _hash_api_key(raw_key)

    with _get_conn() as conn:
        row = conn.execute(
            """SELECT k.id, k.account_id, k.key_type, k.is_active, k.scope,
                      k.expires_at, k.rpm_limit,
                      a.username, a.is_active AS acct_active, a.is_admin
               FROM api_keys k
               JOIN accounts a ON a.id = k.account_id
               WHERE k.key_hash = ?""",
            (key_hash,),
        ).fetchone()

        if not row:
            return None
        if not row["is_active"] or not row["acct_active"]:
            return None

        # Expiry check
        if row["expires_at"]:
            try:
                exp = datetime.fromisoformat(row["expires_at"])
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) > exp:
                    conn.execute("UPDATE api_keys SET is_active=0 WHERE id=?", (row["id"],))
                    logger.info("API key %s expired and deactivated", row["id"])
                    return None
            except Exception:
                return None

        # Scope check (agent keys only; full keys have NULL scope = all allowed)
        if row["scope"] and endpoint_scope:
            try:
                allowed = json.loads(row["scope"])
                if endpoint_scope not in allowed:
                    logger.warning(
                        "Key %s (account %s) scope %s does not include endpoint '%s'",
                        row["id"], row["account_id"], allowed, endpoint_scope,
                    )
                    return None
            except Exception:
                return None

        # IP whitelist check
        if client_ip and not _ip_allowed(conn, row["account_id"], client_ip):
            logger.warning(
                "Key %s rejected: IP %s not in account %s whitelist",
                row["id"], client_ip, row["account_id"],
            )
            return None

        # Rate limit check
        rpm = row["rpm_limit"]
        if rpm and rpm > 0:
            if not _check_rate_limit(key_hash, rpm):
                logger.warning("Key %s rate limited (%d rpm)", row["id"], rpm)
                return None

        # Update last_used_at + audit log
        try:
            conn.execute(
                "UPDATE api_keys SET last_used_at=datetime('now') WHERE id=?",
                (row["id"],),
            )
            conn.execute(
                "INSERT INTO key_audit_log (key_id, account_id, action, client_ip, endpoint) VALUES (?,?,?,?,?)",
                (row["id"], row["account_id"], "api_call", client_ip, endpoint_scope),
            )
        except Exception:
            pass

        return {
            "account_id": row["account_id"],
            "username": row["username"],
            "is_admin": bool(row["is_admin"]),
            "key_type": row["key_type"],
        }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  IP Whitelist management
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _ip_allowed(conn: sqlite3.Connection, account_id: int, client_ip: str) -> bool:
    """Returns True if client_ip is in the account whitelist, or if no whitelist is set."""
    rows = conn.execute(
        "SELECT ip_cidr FROM ip_whitelist WHERE account_id=?",
        (account_id,),
    ).fetchall()

    if not rows:
        # Safer default for single-device deployments:
        # when whitelist is empty, only allow localhost unless explicitly disabled.
        localhost_only = os.getenv("AUTH_DEFAULT_LOCALHOST_ONLY", "1").lower() not in {"0", "false", "no"}
        if localhost_only:
            return _is_localhost_ip(client_ip)
        return True

    try:
        addr = ipaddress.ip_address(client_ip)
    except ValueError:
        return False

    for r in rows:
        try:
            if addr in ipaddress.ip_network(r["ip_cidr"], strict=False):
                return True
        except ValueError:
            continue
    return False


def add_ip_whitelist(account_id: int, ip_cidr: str, description: str = "") -> Dict[str, Any]:
    try:
        ipaddress.ip_network(ip_cidr, strict=False)
    except ValueError:
        raise ValueError(f"無效的 IP / CIDR: {ip_cidr}")

    with _get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO ip_whitelist (account_id, ip_cidr, description) VALUES (?,?,?)",
            (account_id, ip_cidr.strip(), (description or "").strip()),
        )
        row = conn.execute(
            "SELECT id, ip_cidr, description, created_at FROM ip_whitelist WHERE id=?",
            (cur.lastrowid,),
        ).fetchone()
        return dict(row)


def list_ip_whitelist(account_id: int) -> List[Dict[str, Any]]:
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT id, ip_cidr, description, created_at FROM ip_whitelist WHERE account_id=? ORDER BY created_at DESC",
            (account_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def delete_ip_whitelist(entry_id: int, account_id: int) -> bool:
    with _get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM ip_whitelist WHERE id=? AND account_id=?",
            (entry_id, account_id),
        )
        return cur.rowcount > 0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Audit log
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def get_audit_log(account_id: int, limit: int = 100) -> List[Dict[str, Any]]:
    with _get_conn() as conn:
        rows = conn.execute(
            """SELECT id, key_id, action, client_ip, endpoint, created_at
               FROM key_audit_log
               WHERE account_id=?
               ORDER BY id DESC LIMIT ?""",
            (account_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
