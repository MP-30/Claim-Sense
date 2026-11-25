import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
import jwt

from claim_sense.api.auth import require_user


class CognitoAuthTest(unittest.TestCase):
    def test_claim_route_accepts_only_valid_access_tokens(self):
        issuer = "https://cognito.example.test/pool"
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        client = FastAPI()
        client.get("/claims")(lambda user=Depends(require_user): {"user": user})
        claims = {
            "iss": issuer,
            "sub": "reviewer-id",
            "username": "adjuster",
            "token_use": "access",
            "client_id": "portal",
            "exp": int(time.time()) + 60,
        }

        def headers(token_claims, signing_key=key):
            token = jwt.encode(token_claims, signing_key, algorithm="RS256")
            return {"Authorization": f"Bearer {token}"}

        with patch.dict(os.environ, {"CLAIMS_AUTH_REQUIRED": "true", "COGNITO_ISSUER": issuer,
                                     "COGNITO_CLIENT_ID": "portal"}), patch("claim_sense.api.auth.jwks") as jwks:
            jwks.return_value.get_signing_key_from_jwt.return_value = SimpleNamespace(key=key.public_key())
            with TestClient(client) as api:
                self.assertEqual(api.get("/claims").status_code, 401)
                response = api.get("/claims", headers=headers(claims))
                self.assertEqual(response.json(), {"user": "adjuster"})
                for overrides in (
                    {"token_use": "id"}, {"client_id": "other"}, {"iss": f"{issuer}/other"}, {"exp": 0}
                ):
                    with self.subTest(overrides=overrides):
                        self.assertEqual(api.get("/claims", headers=headers(claims | overrides)).status_code, 401)
                self.assertEqual(api.get("/claims", headers=headers(claims, other_key)).status_code, 401)
