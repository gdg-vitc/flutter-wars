"""Daily control-plane cleanup; keep consumed hashes until their whole session expires.

Run with DATABASE_URL targeting the intended database: uv run python scripts/cleanup_auth.py.
"""

import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

from app.core.db import get_engine  # noqa: E402
from app.modules.authentication.sessions import (  # noqa: E402
    AuthRateBucket,
    AuthSession,
    LoginCode,
    OAuthAttempt,
    RefreshCredential,
    now,
)


def main():
    timestamp = now()
    with Session(get_engine()) as db:
        expired = select(AuthSession.id).where(AuthSession.expires_at < timestamp)
        db.execute(delete(RefreshCredential).where(RefreshCredential.session_id.in_(expired)))
        db.execute(delete(AuthSession).where(AuthSession.expires_at < timestamp))
        db.execute(delete(LoginCode).where(LoginCode.expires_at < timestamp))
        db.execute(delete(OAuthAttempt).where(OAuthAttempt.expires_at < timestamp))
        db.execute(delete(AuthRateBucket).where(AuthRateBucket.window_start < timestamp - timedelta(days=1)))
        db.commit()
    print("Expired authentication records removed.")


if __name__ == "__main__":
    main()
