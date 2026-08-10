import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from atmos_gl.db.engine import Session
from atmos_gl.db.models import User, UserIdentity, UserSession
from atmos_gl.lib.auth import is_admin_email


def _user_dict(user: User) -> dict:
    return {"id": user.id, "email": user.email, "name": user.name, "is_admin": user.is_admin}


class UserAdapter:
    """Real adapter for users/user_identities/user_sessions, backed by SQLAlchemy
    (issue #303)."""

    def get_or_create_user(self, *, email: str, name: str | None, provider: str, provider_user_id: str) -> dict:
        """Look up the user for this (provider, provider_user_id); reuse a user with a
        matching email if this is a new provider for an existing account (so adding a
        provider never needs a schema change -- see #6/GitHub+Microsoft); otherwise
        create one. is_admin is refreshed against ADMIN_EMAILS on every call."""
        is_admin = is_admin_email(email)
        with Session() as session:
            identity = session.execute(
                select(UserIdentity).where(
                    UserIdentity.provider == provider,
                    UserIdentity.provider_user_id == provider_user_id,
                )
            ).scalar_one_or_none()

            if identity is not None:
                user = session.get(User, identity.user_id)
                user.name = name
                user.is_admin = is_admin
                identity.email = email
                session.commit()
                return _user_dict(user)

            user = session.execute(select(User).where(User.email == email)).scalar_one_or_none()
            if user is None:
                user = User(email=email, name=name, is_admin=is_admin)
                session.add(user)
                session.flush()
            else:
                user.name = name
                user.is_admin = is_admin

            session.add(
                UserIdentity(
                    user_id=user.id, provider=provider, provider_user_id=provider_user_id, email=email,
                )
            )
            session.commit()
            return _user_dict(user)

    def create_session(self, user_id: int, ttl_seconds: int) -> str:
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        with Session() as session:
            session.add(UserSession(token=token, user_id=user_id, expires_at=expires_at))
            session.commit()
        return token

    def get_session_user(self, token: str) -> dict | None:
        with Session() as session:
            sess = session.get(UserSession, token)
            if sess is None or sess.expires_at < datetime.now(timezone.utc):
                return None
            user = session.get(User, sess.user_id)
            return _user_dict(user) if user is not None else None

    def delete_session(self, token: str) -> None:
        with Session() as session:
            sess = session.get(UserSession, token)
            if sess is not None:
                session.delete(sess)
                session.commit()

    def delete_user(self, user_id: int) -> None:
        """Hard-deletes a user's account (issue #307): removing the User row cascades
        to user_identities, user_sessions, and user_settings via their existing
        ondelete="CASCADE" foreign keys (db/models.py) -- deliberately no manual
        per-table cleanup here, since the DB already guarantees it."""
        with Session() as session:
            user = session.get(User, user_id)
            if user is not None:
                session.delete(user)
                session.commit()


class FakeUserAdapter:
    """In-memory fake for users/user_identities/user_sessions, matching UserAdapter's
    method contracts."""

    def __init__(self):
        self._users: dict[int, dict] = {}
        self._identities: dict[tuple[str, str], int] = {}
        self._sessions: dict[str, dict] = {}
        self._next_id = 1

    def get_or_create_user(self, *, email: str, name: str | None, provider: str, provider_user_id: str) -> dict:
        is_admin = is_admin_email(email)
        key = (provider, provider_user_id)

        user_id = self._identities.get(key)
        if user_id is None:
            user_id = next(
                (uid for uid, u in self._users.items() if u["email"] == email), None
            )
        if user_id is None:
            user_id = self._next_id
            self._next_id += 1
            self._users[user_id] = {"id": user_id, "email": email, "name": name, "is_admin": is_admin}
        else:
            self._users[user_id]["name"] = name
            self._users[user_id]["is_admin"] = is_admin

        self._identities[key] = user_id
        return dict(self._users[user_id])

    def create_session(self, user_id: int, ttl_seconds: int) -> str:
        token = secrets.token_urlsafe(32)
        self._sessions[token] = {
            "user_id": user_id,
            "expires_at": datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds),
        }
        return token

    def get_session_user(self, token: str) -> dict | None:
        sess = self._sessions.get(token)
        if sess is None or sess["expires_at"] < datetime.now(timezone.utc):
            return None
        user = self._users.get(sess["user_id"])
        return dict(user) if user is not None else None

    def delete_session(self, token: str) -> None:
        self._sessions.pop(token, None)

    def delete_user(self, user_id: int) -> None:
        """Mirrors the real adapter's CASCADE by also dropping this user's identities
        and sessions -- not just the user itself."""
        self._users.pop(user_id, None)
        self._identities = {k: v for k, v in self._identities.items() if v != user_id}
        self._sessions = {t: s for t, s in self._sessions.items() if s["user_id"] != user_id}
