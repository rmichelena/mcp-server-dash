"""PKCE OAuth flow implementation for Dropbox authentication.

This module handles the OAuth 2.0 with PKCE (Proof Key for Code Exchange) flow
for authenticating with Dropbox. PKCE (RFC 7636) protects against authorization
code interception attacks.
"""

import base64
import hashlib
import logging
import secrets
from typing import Any
from urllib.parse import urlencode

import httpx

try:
    import dropbox as dropbox_mod
except ImportError:  # pragma: no cover - allow running tests without SDK
    dropbox_mod = None

# Single assignment for type-checkers
dropbox: Any = dropbox_mod

logger = logging.getLogger(__name__)


class PKCEAuthFlow:
    """Manages PKCE-based OAuth flow state and operations."""

    def __init__(self) -> None:
        """Initialize PKCE auth flow with empty state."""
        self._code_verifier: str | None = None

    def _generate_code_verifier(self) -> str:
        """Generate a cryptographically random code verifier for PKCE.

        Returns a base64url-encoded string of 32 random bytes (43 characters).
        Per RFC 7636, code_verifier must be 43-128 characters from [A-Z][a-z][0-9]-._~
        """
        random_bytes = secrets.token_bytes(32)
        # base64url encode (no padding)
        code_verifier = base64.urlsafe_b64encode(random_bytes).decode("utf-8").rstrip("=")
        return code_verifier

    def _generate_code_challenge(self, code_verifier: str) -> str:
        """Generate SHA256 code challenge from code verifier for PKCE.

        Per RFC 7636, code_challenge = BASE64URL(SHA256(ASCII(code_verifier)))
        """
        sha256_hash = hashlib.sha256(code_verifier.encode("ascii")).digest()
        # base64url encode (no padding)
        code_challenge = base64.urlsafe_b64encode(sha256_hash).decode("utf-8").rstrip("=")
        return code_challenge

    def generate_auth_url(self, app_key: str) -> str:
        """Generate authorization URL with PKCE parameters.

        Args:
            app_key: Dropbox app key (client ID)

        Returns:
            Authorization URL to redirect the user to

        Raises:
            RuntimeError: If Dropbox SDK is not installed
        """
        if dropbox is None:
            raise RuntimeError("Dropbox SDK is not installed. Please install 'dropbox' package.")

        # Generate PKCE parameters
        self._code_verifier = self._generate_code_verifier()
        code_challenge = self._generate_code_challenge(self._code_verifier)

        # Build authorization URL with PKCE
        params = {
            "client_id": app_key,
            "response_type": "code",
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "token_access_type": "offline",  # Request refresh token for long-term access
        }
        authorize_url = f"https://www.dropbox.com/oauth2/authorize?{urlencode(params)}"

        logger.info("Generated PKCE-protected authorization URL")
        return authorize_url

    async def exchange_code_for_token(self, auth_code: str, app_key: str) -> dict[str, Any]:
        """Exchange authorization code for access token using PKCE.

        Args:
            auth_code: Authorization code from user
            app_key: Dropbox app key (client ID)

        Returns:
            Token response from Dropbox containing access_token and other fields

        Raises:
            RuntimeError: If Dropbox SDK is not installed or no code_verifier is set
            ValueError: If token exchange fails or response is invalid
        """
        if dropbox is None:
            raise RuntimeError("Dropbox SDK is not installed. Please install 'dropbox' package.")

        if not self._code_verifier:
            raise RuntimeError("No PKCE code_verifier found. Must call generate_auth_url first.")

        try:
            # Exchange authorization code for access token with PKCE
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
                token_response = await client.post(
                    "https://api.dropboxapi.com/oauth2/token",
                    data={
                        "code": auth_code,
                        "grant_type": "authorization_code",
                        "client_id": app_key,
                        "code_verifier": self._code_verifier,
                    },
                )

            # Clear code_verifier immediately after use (success or failure)
            self._code_verifier = None

            if token_response.status_code != 200:
                error_data = token_response.json() if token_response.text else {}
                error_msg = error_data.get(
                    "error_description", error_data.get("error", "Unknown error")
                )
                logger.warning("Token exchange failed: %s", error_msg)
                raise ValueError(f"Token exchange failed: {error_msg}")

            token_data = token_response.json()
            if not token_data.get("access_token"):
                raise ValueError("No access token received from Dropbox")

            logger.info("Successfully exchanged code for token with PKCE")
            return token_data

        except Exception:
            # Clear code_verifier on any error
            self._code_verifier = None
            raise

    def clear_state(self) -> None:
        """Clear the stored code verifier (useful for error recovery)."""
        self._code_verifier = None

    @property
    def has_verifier(self) -> bool:
        """Check if a code verifier is currently stored."""
        return self._code_verifier is not None
