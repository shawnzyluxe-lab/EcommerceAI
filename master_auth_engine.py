"""Hardened multi-tenant session registry and signed token engine.

Provides password hashing, HMAC-signed session tokens, a volatile session vault,
and Flask RBAC decorators. Designed to be imported by app.py and applied to
high-sensitivity endpoints such as the Action Gate approve path.
"""
import base64
import hashlib
import hmac
import logging
import os
import secrets
from datetime import datetime, timedelta
from functools import wraps
from typing import Any, Dict, Optional

from flask import g, jsonify, request
from werkzeug.security import check_password_hash, generate_password_hash

from models import db, ActiveSessionVault, MerchantProfile, UserAuthentication

logger = logging.getLogger(__name__)

AUTH_SECRET_KEY = os.environ.get("AUTH_SECRET_KEY") or os.environ.get("SECRET_KEY")
if not AUTH_SECRET_KEY:
    logger.warning("AUTH_SECRET_KEY and SECRET_KEY are unset; generating an ephemeral token key. Tokens will not survive restarts.")
    AUTH_SECRET_KEY = secrets.token_hex(32)

TOKEN_TTL_HOURS = int(os.environ.get("SESSION_TOKEN_TTL_HOURS", "8"))

_CLEARANCE_RANK = {"merchant": 1, "engineer": 2, "admin": 3}


def _is_strong_hash(password_hash: str) -> bool:
    """Detect modern Werkzeug hashes (pbkdf2, scrypt, argon2)."""
    return password_hash.startswith(("pbkdf2:", "scrypt:", "argon2"))


def _hash_sha256_legacy(password: str) -> str:
    """Deterministic SHA-256 footprint for legacy/demo compatibility only."""
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def hash_password(password: str) -> str:
    """Return a strong Werkzeug hash for new credentials."""
    return generate_password_hash(password, method="pbkdf2:sha256")


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password against a Werkzeug or legacy SHA-256 hash."""
    if not password_hash:
        return False
    if _is_strong_hash(password_hash):
        try:
            return check_password_hash(password_hash, password)
        except Exception:
            return False
    # Legacy fallback: allow migration from deterministic SHA-256 hashes.
    return hmac.compare_digest(password_hash, _hash_sha256_legacy(password))


class VantavSecurityTokenEngine:
    """HMAC-SHA256 signed session token engine."""

    @staticmethod
    def generate_signed_session_token(user_id: str, merchant_id: str) -> str:
        """Build a tamper-evident session token with timestamp and signature."""
        timestamp = str(int(datetime.utcnow().timestamp()))
        raw_message = f"{user_id}:{merchant_id}:{timestamp}"
        signature = hmac.new(
            AUTH_SECRET_KEY.encode("utf-8"),
            raw_message.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        combined_token = f"{raw_message}:{base64.b64encode(signature).decode('utf-8')}"
        return base64.b64encode(combined_token.encode("utf-8")).decode("utf-8")

    @staticmethod
    def verify_and_parse_session_token(token_b64: str) -> Optional[Dict[str, Any]]:
        """Decode, verify signature, and validate TTL. Returns None on any failure."""
        try:
            decoded_combined = base64.b64decode(token_b64.encode("utf-8")).decode("utf-8")
            user_id, merchant_id, timestamp, b64_sig = decoded_combined.split(":")

            rebuilt_message = f"{user_id}:{merchant_id}:{timestamp}"
            expected_sig = hmac.new(
                AUTH_SECRET_KEY.encode("utf-8"),
                rebuilt_message.encode("utf-8"),
                hashlib.sha256,
            ).digest()

            if not hmac.compare_digest(base64.b64encode(expected_sig).decode("utf-8"), b64_sig):
                return None

            issued_at = int(timestamp)
            expires_at = issued_at + (TOKEN_TTL_HOURS * 3600)
            if int(datetime.utcnow().timestamp()) > expires_at:
                return None

            return {"user_id": user_id, "merchant_id": merchant_id, "issued_at": issued_at}
        except Exception:
            return None


def _clearance_for_email(email: str) -> str:
    """Map configured master emails to admin/engineer clearance."""
    master_admins = {e.strip().lower() for e in os.environ.get("MASTER_ADMIN_EMAILS", "").split(",") if e.strip()}
    engineers = {e.strip().lower() for e in os.environ.get("ENGINEER_EMAILS", "").split(",") if e.strip()}
    email_lower = email.lower()
    if email_lower in master_admins:
        return "admin"
    if email_lower in engineers:
        return "engineer"
    return "merchant"


def _create_user_from_profile(profile: MerchantProfile) -> UserAuthentication:
    """Provision a hardened auth record from an existing MerchantProfile."""
    clearance = _clearance_for_email(profile.admin_email or "")
    user = UserAuthentication(
        id=profile.merchant_id or secrets.token_hex(16),
        merchant_id=profile.merchant_id,
        email=(profile.admin_email or "").lower(),
        password_hash=profile.password_hash or hash_password(secrets.token_hex(16)),
        clearance_level=clearance,
        account_status="active",
    )
    db.session.add(user)
    db.session.commit()
    return user


def authenticate_user(email: str, password: str) -> Optional[UserAuthentication]:
    """Validate credentials against the hardened user authentication ledger."""
    email = (email or "").strip().lower()
    if not email or not password:
        return None

    user = UserAuthentication.query.filter_by(email=email, account_status="active").first()
    if user and verify_password(password, user.password_hash):
        return user

    # One-time migration path: seed from MerchantProfile if no hardened record exists.
    if not user:
        profile = MerchantProfile.query.filter_by(admin_email=email).first()
        if profile and profile.password_hash and verify_password(password, profile.password_hash):
            return _create_user_from_profile(profile)

    return None


def issue_session(user: UserAuthentication, ip_address: Optional[str] = None) -> str:
    """Create a signed session in the vault and return the token."""
    token = VantavSecurityTokenEngine.generate_signed_session_token(str(user.id), user.merchant_id)
    expires_at = datetime.utcnow() + timedelta(hours=TOKEN_TTL_HOURS)
    vault = ActiveSessionVault(
        session_token=token,
        user_id=user.id,
        merchant_id=user.merchant_id,
        ip_address=ip_address or request.remote_addr,
        expires_at=expires_at,
    )
    db.session.add(vault)
    db.session.commit()
    return token


def get_session_context(token: str) -> Optional[Dict[str, Any]]:
    """Verify a token signature and lookup the active vault record."""
    parsed = VantavSecurityTokenEngine.verify_and_parse_session_token(token)
    if not parsed:
        return None

    vault = ActiveSessionVault.query.get(token)
    if not vault or vault.expires_at < datetime.utcnow():
        return None

    user = UserAuthentication.query.get(parsed["user_id"])
    if not user or user.account_status != "active":
        return None

    return {
        "user_id": user.id,
        "merchant_id": user.merchant_id,
        "clearance": user.clearance_level,
        "ip_address": vault.ip_address,
        "issued_at": parsed["issued_at"],
        "token": token,
    }


def _has_clearance(clearance: str, required: str) -> bool:
    return _CLEARANCE_RANK.get(clearance, 0) >= _CLEARANCE_RANK.get(required, 0)


def require_clearance(required_level: str):
    """Flask decorator factory: enforce signed X-Session-Token + RBAC clearance."""
    def decorator(endpoint_function):
        @wraps(endpoint_function)
        def wrapper(*args, **kwargs):
            token = request.headers.get("X-Session-Token")
            if not token:
                return jsonify({"detail": "Unauthorized: missing X-Session-Token header."}), 401

            ctx = get_session_context(token)
            if not ctx:
                return jsonify({"detail": "Unauthorized: invalid or expired session token."}), 401

            if not _has_clearance(ctx.get("clearance", ""), required_level):
                return jsonify({"detail": f"Forbidden: {required_level} clearance required."}), 403

            g.session_ctx = ctx
            return endpoint_function(*args, **kwargs)
        return wrapper
    return decorator


def authenticate(email: str, password: str) -> Optional[Dict[str, Any]]:
    """High-level authenticate + issue_session helper."""
    user = authenticate_user(email, password)
    if not user:
        return None
    token = issue_session(user)
    return {
        "session_token": token,
        "merchant_id": user.merchant_id,
        "clearance_level": user.clearance_level,
        "expires_at": (datetime.utcnow() + timedelta(hours=TOKEN_TTL_HOURS)).isoformat() + "Z",
    }
