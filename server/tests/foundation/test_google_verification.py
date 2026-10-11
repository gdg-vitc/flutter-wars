"""Google ID-token verification without the network: a local RSA key stands in for Google's."""

import asyncio
import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.modules.authentication import google, http

CLIENT_ID = "client-123.apps.googleusercontent.com"


@pytest.fixture()
def signer(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid="test-kid", alg="RS256", use="sig")

    async def certs(url):
        assert url == google.GOOGLE_CERTS_URL
        return {"keys": [jwk]}

    monkeypatch.setattr(http, "get_json", certs)
    monkeypatch.setattr(google, "_KEYS_EXPIRE_AT", 0.0)

    def sign(**overrides):
        now = int(time.time())
        claims = {
            "iss": "https://accounts.google.com",
            "aud": CLIENT_ID,
            "sub": "google-sub-1",
            "email": "player@student.test",
            "email_verified": True,
            "iat": now,
            "exp": now + 300,
        } | overrides
        return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test-kid"})

    return sign


def verify(token, audience=CLIENT_ID):
    return asyncio.run(google.verify_google_token(token, audience))


def test_valid_token_yields_identity(signer):
    assert verify(signer()) == {"google_subject": "google-sub-1", "email": "player@student.test"}


@pytest.mark.parametrize(
    "overrides",
    [
        {"email_verified": False},
        {"aud": "someone-else"},
        {"iss": "https://evil.example"},
        {"exp": int(time.time()) - 60},
    ],
)
def test_untrusted_claims_are_rejected(signer, overrides):
    assert verify(signer(**overrides)) is None


def test_missing_client_id_or_wrong_signature_is_rejected(signer):
    assert verify(signer(), audience=None) is None
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged = jwt.encode({"sub": "x"}, other, algorithm="RS256", headers={"kid": "test-kid"})
    assert verify(forged) is None


def test_small_google_clock_skew_is_accepted(signer):
    assert verify(signer(iat=int(time.time()) + 20)) is not None


def test_google_clock_skew_beyond_tolerance_is_rejected(signer):
    assert verify(signer(iat=int(time.time()) + 60)) is None
