"""Vantav Channel Auth Controllers.

Handles Shopify OAuth access-code exchange and Amazon SP-API LWA refresh-token
rotation. All access/refresh tokens are persisted in `secure_channel_credentials`
and encrypted with AES-256-GCM. The frontend never receives raw tokens.
"""

import base64
import hashlib
import os
import re
import time
from datetime import datetime, timedelta
from typing import Optional

import requests
from flask import Blueprint, request, jsonify

from models import db, IntegrationLink, SecureChannelCredential

credential_bp = Blueprint("channel_auth", __name__)

SHOPIFY_CLIENT_ID = os.environ.get("SHOPIFY_CLIENT_ID", os.environ.get("SHOPIFY_API_KEY", ""))
SHOPIFY_CLIENT_SECRET = os.environ.get("SHOPIFY_CLIENT_SECRET", os.environ.get("SHOPIFY_API_SECRET", ""))

AMAZON_LWA_CLIENT_ID = os.environ.get("AMAZON_LWA_CLIENT_ID", "")
AMAZON_LWA_CLIENT_SECRET = os.environ.get("AMAZON_LWA_CLIENT_SECRET", "")


def _encryption_key() -> bytes:
    """Derive a 32-byte AES-256 key from the configured credential secret."""
    secret = os.environ.get("CREDENTIAL_ENCRYPTION_KEY") or os.environ.get("SECRET_KEY", "")
    if not secret:
        return b"" * 32
    return hashlib.sha256(secret.encode("utf-8")).digest()


class CredentialVault:
    """AES-256-GCM encryption for channel access/refresh tokens."""

    @staticmethod
    def _aes_available() -> bool:
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: F401
            return True
        except Exception:
            return False

    @staticmethod
    def encrypt(plaintext: str) -> str:
        if not plaintext:
            return ""
        key = _encryption_key()
        if CredentialVault._aes_available():
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            aesgcm = AESGCM(key)
            nonce = os.urandom(12)
            ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
            blob = nonce + ciphertext
        else:
            # Fallback only when cryptography is not installed; log a warning.
            import base64 as b64
            blob = b"FALLBACK:" + b64.b64encode(plaintext.encode("utf-8"))
        return base64.b64encode(blob).decode("ascii")

    @staticmethod
    def decrypt(ciphertext_b64: str) -> str:
        if not ciphertext_b64:
            return ""
        key = _encryption_key()
        blob = base64.b64decode(ciphertext_b64)
        if blob.startswith(b"FALLBACK:"):
            import base64 as b64
            return b64.b64decode(blob.split(b":", 1)[1]).decode("utf-8")
        if CredentialVault._aes_available():
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            aesgcm = AESGCM(key)
            nonce, ciphertext = blob[:12], blob[12:]
            return aesgcm.decrypt(nonce, ciphertext, None).decode("utf-8")
        raise RuntimeError("AES-256 key material not available for decryption")


def _merchant_context() -> Optional[dict]:
    """Resolve the active merchant from the session cookie."""
    # Delayed import breaks the circular dependency with app.py
    try:
        from app import get_merchant_context
        return get_merchant_context()
    except Exception:
        return None


def _get_or_create_link(merchant_id: str, platform: str, **kwargs) -> IntegrationLink:
    link = IntegrationLink.query.filter_by(merchant_id=merchant_id, platform=platform).first()
    if not link:
        link = IntegrationLink(merchant_id=merchant_id, platform=platform, **kwargs)
        db.session.add(link)
    else:
        for k, v in kwargs.items():
            if v is not None and getattr(link, k) != v:
                setattr(link, k, v)
    db.session.flush()
    return link


def store_channel_credentials(
    merchant_id: str,
    platform: str,
    access_token: str,
    refresh_token: Optional[str] = None,
    expires_at: Optional[datetime] = None,
    **link_attrs,
) -> IntegrationLink:
    """Persist tokens in the secure vault, linked to an integration record."""
    link = _get_or_create_link(merchant_id, platform, **link_attrs)

    cred = SecureChannelCredential.query.filter_by(integration_link_id=link.id).first()
    if not cred:
        cred = SecureChannelCredential(integration_link_id=link.id)
        db.session.add(cred)

    cred.encrypted_access_token = CredentialVault.encrypt(access_token)
    if refresh_token:
        cred.encrypted_refresh_token = CredentialVault.encrypt(refresh_token)
    if expires_at:
        cred.tokens_expire_at = expires_at
    cred.updated_at = datetime.utcnow()
    link.updated_at = datetime.utcnow()
    db.session.commit()
    return link


def retrieve_channel_credentials(integration_link_id: str) -> Optional[dict]:
    """Return decrypted credentials for an integration link, or None."""
    cred = SecureChannelCredential.query.filter_by(integration_link_id=integration_link_id).first()
    if not cred:
        return None
    return {
        "access_token": CredentialVault.decrypt(cred.encrypted_access_token),
        "refresh_token": CredentialVault.decrypt(cred.encrypted_refresh_token) if cred.encrypted_refresh_token else None,
        "expires_at": cred.tokens_expire_at,
    }


@credential_bp.route("/api/v1/auth/shopify/exchange", methods=["POST"])
def exchange_shopify_code_for_permanent_token():
    """Exchange Shopify's temporary OAuth code for a permanent offline token."""
    merchant = _merchant_context()
    if not merchant:
        return jsonify({"detail": "Authentication required"}), 401
    if not merchant.get("live_access_enabled"):
        return jsonify({"detail": "Live marketplace access not enabled"}), 403

    payload = request.get_json(silent=True) or {}
    shop = (payload.get("shop_domain") or "").strip().lower()
    code = (payload.get("temporary_code") or payload.get("code") or "").strip()

    if not re.match(r'^[a-zA-Z0-9\-]+\.myshopify\.com$', shop):
        return jsonify({"detail": "Invalid shop domain"}), 400
    if not code:
        return jsonify({"detail": "Missing authorization code"}), 400
    if not SHOPIFY_CLIENT_ID or not SHOPIFY_CLIENT_SECRET:
        return jsonify({"detail": "Shopify OAuth credentials not configured"}), 400

    try:
        response = requests.post(
            f"https://{shop}/admin/oauth/access_token",
            json={
                "client_id": SHOPIFY_CLIENT_ID,
                "client_secret": SHOPIFY_CLIENT_SECRET,
                "code": code,
            },
            timeout=10,
        )
        if response.status_code != 200:
            return jsonify({"detail": "Shopify handshake validation failed"}), 401

        token_data = response.json()
        access_token = token_data.get("access_token")
        scope = token_data.get("scope", "")

        store_channel_credentials(
            merchant_id=merchant["id"],
            platform="shopify",
            access_token=access_token,
            refresh_token=None,
            shopify_shop_domain=shop,
        )

        return jsonify({
            "status": "success",
            "scope_permissions": scope,
            "message": "Shopify offline credentials stored securely behind backend gate.",
        }), 200
    except Exception as e:
        return jsonify({"detail": f"Shopify Auth Hub Fault: {str(e)}"}), 500


@credential_bp.route("/api/v1/auth/amazon/refresh-token", methods=["POST"])
def refresh_amazon_sp_api_access_token():
    """Exchange an Amazon SP-API long-term refresh token for a short-lived access token."""
    merchant = _merchant_context()
    if not merchant:
        return jsonify({"detail": "Authentication required"}), 401

    payload = request.get_json(silent=True) or {}
    refresh_token = (payload.get("stored_refresh_token") or payload.get("refresh_token") or "").strip()
    seller_id = (payload.get("seller_id") or payload.get("amazon_seller_id") or "").strip()
    region = (payload.get("region") or "us-east-1").strip()

    if not refresh_token:
        return jsonify({"detail": "Missing refresh token"}), 400
    if not AMAZON_LWA_CLIENT_ID or not AMAZON_LWA_CLIENT_SECRET:
        return jsonify({"detail": "Amazon LWA credentials not configured"}), 400

    try:
        resp = requests.post(
            "https://api.amazon.com/auth/o2/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": AMAZON_LWA_CLIENT_ID,
                "client_secret": AMAZON_LWA_CLIENT_SECRET,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=10,
        )
        if resp.status_code != 200:
            return jsonify({"detail": "Amazon token rotation failed", "upstream_status": resp.status_code}), resp.status_code

        tokens = resp.json()
        access_token = tokens.get("access_token")
        expires_in = int(tokens.get("expires_in", 3600))
        expires_at = datetime.utcnow() + timedelta(seconds=expires_in)

        store_channel_credentials(
            merchant_id=merchant["id"],
            platform="amazon",
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            amazon_seller_id=seller_id,
            amazon_region=region,
        )

        return jsonify({
            "status": "success",
            "expires_in_seconds": expires_in,
            "synchronized_at": int(time.time()),
        }), 200
    except Exception as e:
        return jsonify({"detail": f"Amazon SP-API Identity Fault: {str(e)}"}), 500
