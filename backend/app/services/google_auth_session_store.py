from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

SQLITE_BUSY_TIMEOUT_MS = 5_000


class SecretToken:
    """Opaque token whose representation never exposes the bearer secret."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        if not value:
            raise ValueError("token must not be empty")
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "SecretToken(<redacted>)"

    __str__ = __repr__


@dataclass(frozen=True, slots=True)
class IssuedAuthSession:
    access_token: SecretToken
    refresh_token: SecretToken
    expires_at_ms: int


@dataclass(frozen=True, slots=True)
class GoogleUserIdentity:
    user_id: int
    account_id: str
    provider_subject: str
    email: str | None
    display_name: str | None


class InvalidRefreshTokenError(RuntimeError):
    pass


class GoogleAuthSessionStore:
    """SQLite session store containing only SHA-256 digests of bearer tokens."""

    def __init__(
        self,
        path: str | Path,
        *,
        token_factory: Callable[[], str] | None = None,
    ) -> None:
        self.path = Path(path).expanduser().resolve()
        self._token_factory = token_factory or (lambda: secrets.token_urlsafe(32))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=SQLITE_BUSY_TIMEOUT_MS / 1_000,
            isolation_level=None,
        )
        connection.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            self._enable_wal_with_retry(connection)
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS google_auth_users (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        account_id TEXT UNIQUE,
                        provider TEXT NOT NULL,
                        provider_subject TEXT NOT NULL,
                        email TEXT,
                        display_name TEXT,
                        created_at_ms INTEGER NOT NULL,
                        updated_at_ms INTEGER NOT NULL,
                        UNIQUE(provider, provider_subject),
                        CHECK(provider = 'google')
                    )
                    """
                )
                columns = {
                    str(row[1])
                    for row in connection.execute("PRAGMA table_info(google_auth_users)")
                }
                if "account_id" not in columns:
                    connection.execute("ALTER TABLE google_auth_users ADD COLUMN account_id TEXT")
                missing_ids = connection.execute(
                    "SELECT id FROM google_auth_users WHERE account_id IS NULL"
                ).fetchall()
                for (user_id,) in missing_ids:
                    connection.execute(
                        "UPDATE google_auth_users SET account_id = ? WHERE id = ?",
                        (self._new_account_id(), int(user_id)),
                    )
                connection.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS google_auth_users_account_id_idx "
                    "ON google_auth_users(account_id)"
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS google_auth_sessions (
                        session_id TEXT PRIMARY KEY,
                        user_id INTEGER NOT NULL REFERENCES google_auth_users(id),
                        access_token_digest BLOB NOT NULL UNIQUE,
                        refresh_token_digest BLOB NOT NULL UNIQUE,
                        access_expires_at_ms INTEGER NOT NULL,
                        refresh_expires_at_ms INTEGER NOT NULL,
                        created_at_ms INTEGER NOT NULL,
                        rotated_at_ms INTEGER NOT NULL,
                        revoked_at_ms INTEGER,
                        CHECK(length(access_token_digest) = 32),
                        CHECK(length(refresh_token_digest) = 32)
                    ) WITHOUT ROWID
                    """
                )
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS google_auth_sessions_user_idx
                    ON google_auth_sessions(user_id, revoked_at_ms)
                    """
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        os.chmod(self.path, 0o600)

    @staticmethod
    def _enable_wal_with_retry(connection: sqlite3.Connection) -> None:
        deadline = time.monotonic() + SQLITE_BUSY_TIMEOUT_MS / 1_000
        delay = 0.01
        while True:
            try:
                connection.execute("PRAGMA journal_mode=WAL")
                return
            except sqlite3.OperationalError as exc:
                error_code = getattr(exc, "sqlite_errorcode", None)
                primary = error_code & 0xFF if error_code is not None else None
                if primary not in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise
                time.sleep(min(delay, remaining))
                delay = min(delay * 2, 0.1)

    @staticmethod
    def _digest(token: str) -> bytes:
        return hashlib.sha256(token.encode("utf-8")).digest()

    def issue_for_google_user(
        self,
        *,
        provider_subject: str,
        email: str | None,
        display_name: str | None,
        now_ms: int,
        access_ttl_seconds: int,
        refresh_ttl_seconds: int,
    ) -> tuple[GoogleUserIdentity, IssuedAuthSession]:
        access_token = self._new_token()
        refresh_token = self._new_token()
        access_expires_at_ms = now_ms + access_ttl_seconds * 1_000
        refresh_expires_at_ms = now_ms + refresh_ttl_seconds * 1_000
        session_id = secrets.token_hex(16)

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO google_auth_users (
                        account_id, provider, provider_subject, email, display_name,
                        created_at_ms, updated_at_ms
                    ) VALUES (?, 'google', ?, ?, ?, ?, ?)
                    ON CONFLICT(provider, provider_subject) DO UPDATE SET
                        email = excluded.email,
                        display_name = excluded.display_name,
                        updated_at_ms = excluded.updated_at_ms
                    """,
                    (
                        self._new_account_id(),
                        provider_subject,
                        email,
                        display_name,
                        now_ms,
                        now_ms,
                    ),
                )
                row = connection.execute(
                    """
                    SELECT id, account_id, provider_subject, email, display_name
                    FROM google_auth_users
                    WHERE provider = 'google' AND provider_subject = ?
                    """,
                    (provider_subject,),
                ).fetchone()
                if row is None:
                    raise RuntimeError("google user upsert failed")
                user = self._identity(row)
                connection.execute(
                    """
                    INSERT INTO google_auth_sessions (
                        session_id, user_id, access_token_digest, refresh_token_digest,
                        access_expires_at_ms, refresh_expires_at_ms,
                        created_at_ms, rotated_at_ms, revoked_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
                    """,
                    (
                        session_id,
                        user.user_id,
                        self._digest(access_token),
                        self._digest(refresh_token),
                        access_expires_at_ms,
                        refresh_expires_at_ms,
                        now_ms,
                        now_ms,
                    ),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

        return user, IssuedAuthSession(
            access_token=SecretToken(access_token),
            refresh_token=SecretToken(refresh_token),
            expires_at_ms=access_expires_at_ms,
        )

    def rotate_refresh_token(
        self,
        refresh_token: str,
        *,
        now_ms: int,
        access_ttl_seconds: int,
        refresh_ttl_seconds: int,
    ) -> IssuedAuthSession:
        presented_digest = self._digest(refresh_token)
        new_access_token = self._new_token()
        new_refresh_token = self._new_token()
        access_expires_at_ms = now_ms + access_ttl_seconds * 1_000
        refresh_expires_at_ms = now_ms + refresh_ttl_seconds * 1_000

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    """
                    SELECT session_id, refresh_expires_at_ms, revoked_at_ms
                    FROM google_auth_sessions
                    WHERE refresh_token_digest = ?
                    """,
                    (presented_digest,),
                ).fetchone()
                if row is None or row[2] is not None or int(row[1]) <= now_ms:
                    if row is not None and row[2] is None:
                        connection.execute(
                            """
                            UPDATE google_auth_sessions SET revoked_at_ms = ?
                            WHERE session_id = ? AND revoked_at_ms IS NULL
                            """,
                            (now_ms, str(row[0])),
                        )
                    connection.commit()
                    raise InvalidRefreshTokenError("refresh token is invalid")
                cursor = connection.execute(
                    """
                    UPDATE google_auth_sessions SET
                        access_token_digest = ?,
                        refresh_token_digest = ?,
                        access_expires_at_ms = ?,
                        refresh_expires_at_ms = ?,
                        rotated_at_ms = ?
                    WHERE session_id = ?
                      AND refresh_token_digest = ?
                      AND revoked_at_ms IS NULL
                    """,
                    (
                        self._digest(new_access_token),
                        self._digest(new_refresh_token),
                        access_expires_at_ms,
                        refresh_expires_at_ms,
                        now_ms,
                        str(row[0]),
                        presented_digest,
                    ),
                )
                if cursor.rowcount != 1:
                    raise InvalidRefreshTokenError("refresh token was already rotated")
                connection.commit()
            except InvalidRefreshTokenError:
                if connection.in_transaction:
                    connection.rollback()
                raise
            except BaseException:
                connection.rollback()
                raise

        return IssuedAuthSession(
            access_token=SecretToken(new_access_token),
            refresh_token=SecretToken(new_refresh_token),
            expires_at_ms=access_expires_at_ms,
        )

    def identity_for_access_token(
        self,
        access_token: str,
        *,
        now_ms: int,
    ) -> GoogleUserIdentity | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT users.id, users.account_id, users.provider_subject,
                       users.email, users.display_name
                FROM google_auth_sessions AS sessions
                JOIN google_auth_users AS users ON users.id = sessions.user_id
                WHERE sessions.access_token_digest = ?
                  AND sessions.revoked_at_ms IS NULL
                  AND sessions.access_expires_at_ms > ?
                """,
                (self._digest(access_token), now_ms),
            ).fetchone()
        return self._identity(row) if row is not None else None

    def revoke_refresh_token(self, refresh_token: str, *, now_ms: int) -> bool:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    """
                    UPDATE google_auth_sessions SET revoked_at_ms = ?
                    WHERE refresh_token_digest = ? AND revoked_at_ms IS NULL
                    """,
                    (now_ms, self._digest(refresh_token)),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return cursor.rowcount == 1

    def delete_account(self, account_id: str) -> bool:
        """Remove an account and all access/refresh token digests bound to it."""

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT id FROM google_auth_users WHERE account_id = ?",
                    (account_id,),
                ).fetchone()
                if row is None:
                    connection.commit()
                    return False
                user_id = int(row[0])
                connection.execute(
                    "DELETE FROM google_auth_sessions WHERE user_id = ?",
                    (user_id,),
                )
                connection.execute(
                    "DELETE FROM google_auth_users WHERE id = ?",
                    (user_id,),
                )
                connection.commit()
                return True
            except BaseException:
                connection.rollback()
                raise

    def _new_token(self) -> str:
        value = self._token_factory()
        if not value or len(value) > 1_024:
            raise ValueError("token factory returned an invalid token")
        return value

    @staticmethod
    def _new_account_id() -> str:
        return f"acct_{secrets.token_urlsafe(18)}"

    @staticmethod
    def _identity(row: sqlite3.Row | tuple[object, ...]) -> GoogleUserIdentity:
        return GoogleUserIdentity(
            user_id=int(row[0]),
            account_id=str(row[1]),
            provider_subject=str(row[2]),
            email=str(row[3]) if row[3] is not None else None,
            display_name=str(row[4]) if row[4] is not None else None,
        )
