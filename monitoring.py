# Copyright (c) 2026 Vantav / Shawnzyluxe. All rights reserved.
# This file is part of the Vantav Commerce Platform and is proprietary software.
# Unauthorized copying, modification, distribution, or use is strictly prohibited.
# See LICENSE for the full proprietary license terms.

"""Production hardening, request metrics, SLA monitoring, and alerting."""
import json
import logging
import os
import smtplib
import statistics
import time
from collections import deque, defaultdict
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from threading import Lock
from typing import Any, Dict, List, Optional

import requests
from flask import request
from twilio.rest import Client as TwilioClient

from models import db, SystemExceptionLog, PendingAction, MerchantChannel, TenantOAuthToken, BusinessMetric, Alert

logger = logging.getLogger("shawnzyluxe_core.monitoring")

MAX_REQUEST_SAMPLES = 1000

# Finalized SLA thresholds (overridable by env for tuning)
SLOW_P95_MS = float(os.environ.get("SLA_SLOW_P95_MS", "1000"))
ERROR_RATE_THRESHOLD = float(os.environ.get("SLA_ERROR_RATE_THRESHOLD", "1.0"))
MAX_PENDING_ACTIONS = int(os.environ.get("SLA_MAX_PENDING_ACTIONS", "50"))
MAX_CHANNEL_SYNC_AGE_SECONDS = int(os.environ.get("SLA_MAX_CHANNEL_SYNC_AGE_SECONDS", "3600"))
DB_LATENCY_MS_THRESHOLD = float(os.environ.get("SLA_DB_LATENCY_MS", "300"))

# Alert channel configuration. ALERT_EMAIL supports comma-separated addresses.
ALERT_EMAILS = [e.strip() for e in os.environ.get("ALERT_EMAIL", "").split(",") if e.strip()]
ALERT_PHONE = os.environ.get("ALERT_PHONE", "")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
SLA_WEBHOOK_URL = os.environ.get("SLA_WEBHOOK_URL", "")

_metrics_lock = Lock()
_request_samples: deque = deque(maxlen=MAX_REQUEST_SAMPLES)
_error_samples: deque = deque(maxlen=MAX_REQUEST_SAMPLES // 4)
_sla_alerts: deque = deque(maxlen=100)
_db_latency_ms: deque = deque(maxlen=100)


def log_system_exception(module_origin: str, error_severity: str, exception_msg: str):
    """Persist a system exception to the database."""
    try:
        db.session.add(SystemExceptionLog(
            module_origin=module_origin,
            error_severity=error_severity,
            exception_msg=exception_msg,
        ))
        db.session.commit()
    except Exception:
        logger.exception("Failed to persist SystemExceptionLog")


def record_request(path: str, method: str, status_code: int, duration_ms: float):
    """Record a request sample for metrics and SLA checks."""
    sample = {
        "timestamp": datetime.utcnow().isoformat(),
        "path": path,
        "method": method,
        "status_code": status_code,
        "duration_ms": duration_ms,
    }
    with _metrics_lock:
        _request_samples.append(sample)
        if status_code >= 500 or (status_code >= 400 and status_code != 404):
            _error_samples.append(sample)


def record_db_latency(duration_ms: float):
    """Record a database latency sample."""
    with _metrics_lock:
        _db_latency_ms.append(duration_ms)


def _percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    k = (len(sorted_vals) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] * (c - k) + sorted_vals[c] * (k - f)


def current_metrics() -> Dict[str, Any]:
    """Return rolling request and SLA metrics."""
    with _metrics_lock:
        samples = list(_request_samples)
        errors = list(_error_samples)
        db_lat = list(_db_latency_ms)

    durations = [s["duration_ms"] for s in samples]
    now = datetime.utcnow()
    one_minute_ago = now - timedelta(minutes=1)
    recent = [s for s in samples if datetime.fromisoformat(s["timestamp"]) > one_minute_ago]
    recent_errors = [s for s in errors if datetime.fromisoformat(s["timestamp"]) > one_minute_ago]

    total = len(samples)
    error_count = len(errors)
    recent_total = max(len(recent), 1)
    recent_error_count = len(recent_errors)
    error_rate = round((recent_error_count / recent_total) * 100, 2)

    one_hour_ago = now - timedelta(hours=1)
    recent_1h = [s for s in samples if datetime.fromisoformat(s["timestamp"]) > one_hour_ago]
    durations_1h = [s["duration_ms"] for s in recent_1h]
    request_count_1h = len(recent_1h)
    mean_latency_ms = round(statistics.mean(durations_1h), 2) if durations_1h else 0.0
    slow_request_count = sum(1 for d in durations_1h if d > SLOW_P95_MS)

    twenty_four_hours_ago = now - timedelta(hours=24)
    login_paths = ("/api/v1/auth/login", "/site-login", "/api/v1/site/login")
    failed_logins_24h = sum(
        1
        for s in samples
        if datetime.fromisoformat(s["timestamp"]) > twenty_four_hours_ago
        and s["status_code"] in (401, 403)
        and any(p in s["path"] for p in login_paths)
    )

    status_counts: Dict[int, int] = defaultdict(int)
    for s in samples:
        status_counts[s["status_code"]] += 1

    top_routes: Dict[str, int] = defaultdict(int)
    for s in samples:
        top_routes[s["path"]] += 1

    return {
        "total_requests": total,
        "total_errors": error_count,
        "requests_per_minute": len(recent),
        "request_count": request_count_1h,
        "error_rate_percent": error_rate,
        "error_rate": round(error_rate / 100, 4) if error_rate is not None else 0.0,
        "p95_latency_ms": round(_percentile(durations, 95), 2),
        "mean_latency_ms": mean_latency_ms,
        "slow_request_count": slow_request_count,
        "db_p95_ms": round(_percentile(db_lat, 95), 2) if db_lat else 0.0,
        "failed_logins_24h": failed_logins_24h,
        "latency_ms": {
            "p50": round(_percentile(durations, 50), 2),
            "p95": round(_percentile(durations, 95), 2),
            "p99": round(_percentile(durations, 99), 2),
            "min": round(min(durations), 2) if durations else 0.0,
            "max": round(max(durations), 2) if durations else 0.0,
            "mean": mean_latency_ms,
        },
        "db_latency_ms": {
            "p50": round(_percentile(db_lat, 50), 2) if db_lat else 0.0,
            "p95": round(_percentile(db_lat, 95), 2) if db_lat else 0.0,
            "samples": len(db_lat),
        },
        "status_distribution": dict(status_counts),
        "top_routes": dict(sorted(top_routes.items(), key=lambda kv: kv[1], reverse=True)[:10]),
        "generated_at": now.isoformat(),
    }


def db_health_check() -> Dict[str, Any]:
    """Check database connectivity and latency."""
    start = time.perf_counter()
    try:
        BusinessMetric.query.first()
        latency_ms = (time.perf_counter() - start) * 1000
        record_db_latency(latency_ms)
        return {"healthy": True, "latency_ms": round(latency_ms, 2)}
    except Exception as e:
        record_db_latency(9999.0)
        logger.critical(f"DB health check failed: {e}")
        log_system_exception("DB_HEALTH", "CRITICAL", str(e))
        return {"healthy": False, "error": str(e)}


def storage_health_check() -> Dict[str, Any]:
    """Check generated storage write access."""
    generated_dir = os.environ.get("GENERATED_DIR", "/tmp/generated")
    try:
        os.makedirs(generated_dir, exist_ok=True)
        probe = os.path.join(generated_dir, ".health_probe")
        with open(probe, "w") as f:
            f.write("PROBE_OK")
        os.remove(probe)
        return {"healthy": True}
    except Exception as e:
        logger.critical(f"Storage health check failed: {e}")
        log_system_exception("STORAGE_HEALTH", "CRITICAL", str(e))
        return {"healthy": False, "error": str(e)}


def deep_health() -> Dict[str, Any]:
    """Full production health check for monitoring dashboards."""
    status = "HEALTHY"
    db_check = db_health_check()
    storage_check = storage_health_check()
    if not db_check["healthy"] or not storage_check["healthy"]:
        status = "DEGRADED"

    channels = _channel_sync_status()
    stale = [c for c in channels if c.get("stale")]
    if stale:
        status = "DEGRADED" if status == "HEALTHY" else status

    return {
        "status": status,
        "timestamp": datetime.utcnow().isoformat(),
        "database": db_check,
        "storage": storage_check,
        "channels": channels,
        "pending_actions": _pending_action_count(),
    }


def _pending_action_count() -> int:
    try:
        return PendingAction.query.filter_by(status="pending").count()
    except Exception as e:
        logger.error(f"Pending action count failed: {e}")
        return -1


def _channel_sync_status() -> List[Dict[str, Any]]:
    """Return sync freshness for each connected channel."""
    try:
        channels = []
        mcs = MerchantChannel.query.all()
        for mc in mcs:
            token = TenantOAuthToken.query.filter_by(
                merchant_id=mc.merchant_id, platform_id=mc.channel_id
            ).first()
            last_sync = token.updated_at if token else None
            age = (datetime.utcnow() - last_sync).total_seconds() if last_sync else None
            stale = age is not None and age > MAX_CHANNEL_SYNC_AGE_SECONDS
            channels.append({
                "merchant_id": mc.merchant_id,
                "channel": mc.channel_id,
                "last_sync": last_sync.isoformat() if last_sync else None,
                "age_seconds": age,
                "stale": stale,
            })
        return channels
    except Exception as e:
        logger.error(f"Channel sync status failed: {e}")
        return []


def check_sla() -> List[Dict[str, Any]]:
    """Run SLA checks and return active alerts."""
    alerts: List[Dict[str, Any]] = []
    metrics = current_metrics()

    if metrics["latency_ms"]["p95"] > SLOW_P95_MS:
        alerts.append({
            "severity": "warn",
            "type": "slow_requests",
            "message": f"P95 latency is {metrics['latency_ms']['p95']}ms (threshold {SLOW_P95_MS}ms)",
            "value": metrics["latency_ms"]["p95"],
            "threshold": SLOW_P95_MS,
        })

    if metrics["error_rate_percent"] > ERROR_RATE_THRESHOLD:
        alerts.append({
            "severity": "crit",
            "type": "high_error_rate",
            "message": f"Error rate is {metrics['error_rate_percent']}% over last minute (threshold {ERROR_RATE_THRESHOLD}%)",
            "value": metrics["error_rate_percent"],
            "threshold": ERROR_RATE_THRESHOLD,
        })

    db_check = db_health_check()
    if not db_check["healthy"]:
        alerts.append({
            "severity": "crit",
            "type": "database_unhealthy",
            "message": f"Database health check failed: {db_check.get('error')}",
        })
    elif db_check.get("latency_ms", 0) > DB_LATENCY_MS_THRESHOLD:
        alerts.append({
            "severity": "warn",
            "type": "slow_database",
            "message": f"DB latency is {db_check['latency_ms']}ms (threshold {DB_LATENCY_MS_THRESHOLD}ms)",
            "value": db_check["latency_ms"],
            "threshold": DB_LATENCY_MS_THRESHOLD,
        })

    pending = _pending_action_count()
    if pending > MAX_PENDING_ACTIONS:
        alerts.append({
            "severity": "warn",
            "type": "action_backlog",
            "message": f"{pending} pending actions exceed backlog threshold ({MAX_PENDING_ACTIONS})",
            "value": pending,
            "threshold": MAX_PENDING_ACTIONS,
        })

    for ch in _channel_sync_status():
        if ch.get("stale"):
            alerts.append({
                "severity": "warn",
                "type": "stale_channel_sync",
                "message": f"{ch['channel']} for {ch['merchant_id']} last synced {int(ch['age_seconds'])}s ago",
                "value": ch["age_seconds"],
                "threshold": MAX_CHANNEL_SYNC_AGE_SECONDS,
            })

    try:
        crit_alerts = Alert.query.filter_by(severity="crit").count()
        if crit_alerts > 0:
            alerts.append({
                "severity": "crit",
                "type": "critical_alerts",
                "message": f"{crit_alerts} critical business alerts are open",
                "value": crit_alerts,
                "threshold": 0,
            })
    except Exception as e:
        logger.error(f"Alert count failed: {e}")

    with _metrics_lock:
        _sla_alerts.clear()
        _sla_alerts.extend(alerts)

    for alert in alerts:
        send_alert(alert)

    return alerts


def send_alert(alert: Dict[str, Any]) -> bool:
    """Dispatch an alert to configured channels."""
    success = False
    subject = f"[{alert['severity'].upper()}] Vantav SLA Alert: {alert['type']}"
    body = f"{alert['message']}\n\nValue: {alert.get('value')}\nThreshold: {alert.get('threshold')}\nTime: {datetime.utcnow().isoformat()}"
    payload = {
        "severity": alert["severity"],
        "type": alert["type"],
        "message": alert["message"],
        "timestamp": datetime.utcnow().isoformat(),
    }

    if ALERT_EMAILS:
        for email in ALERT_EMAILS:
            try:
                _send_email(email, subject, body)
                success = True
            except Exception as e:
                logger.error(f"Alert email to {email} failed: {e}")

    if ALERT_PHONE:
        try:
            _send_sms(ALERT_PHONE, body[:1600])
            success = True
        except Exception as e:
            logger.error(f"Alert SMS failed: {e}")

    if DISCORD_WEBHOOK_URL:
        try:
            requests.post(
                DISCORD_WEBHOOK_URL,
                json={"content": f"**{subject}**\n{alert['message']}"},
                timeout=10,
            )
            success = True
        except Exception as e:
            logger.error(f"Discord alert failed: {e}")

    if SLA_WEBHOOK_URL:
        try:
            requests.post(SLA_WEBHOOK_URL, json=payload, timeout=10)
            success = True
        except Exception as e:
            logger.error(f"SLA webhook failed: {e}")

    return success


def _send_email(to: str, subject: str, body: str):
    smtp_server = os.environ.get("SMTP_SERVER", "")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USERNAME", "")
    smtp_pass = os.environ.get("SMTP_PASSWORD", "")
    from_addr = smtp_user or "alerts@vantavcommerce.com"
    if not smtp_server:
        raise RuntimeError("SMTP_SERVER not configured")
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to
    with smtplib.SMTP(smtp_server, smtp_port) as server:
        if smtp_port == 587:
            server.starttls()
        if smtp_user and smtp_pass:
            server.login(smtp_user, smtp_pass)
        server.sendmail(from_addr, [to], msg.as_string())


def _send_sms(to: str, body: str):
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    from_number = os.environ.get("TWILIO_FROM_NUMBER", "")
    if not all([account_sid, auth_token, from_number]):
        raise RuntimeError("Twilio not configured")
    client = TwilioClient(account_sid, auth_token)
    client.messages.create(body=body, from_=from_number, to=to)


def security_headers(response):
    """Apply production security headers to a Flask response."""
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://www.google.com https://www.gstatic.com https://www.googletagmanager.com https://connect.facebook.net https://analytics.tiktok.com https://cdn.tailwindcss.com https://www.tailwindcss.com https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data: https:; "
        "connect-src 'self' https://api.render.com https://www.google-analytics.com https://www.facebook.com; "
        "frame-ancestors 'none';"
    )
    if request_is_https():
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
    return response


def request_is_https() -> bool:
    """Best-effort HTTPS detection for HSTS, including behind a TLS-terminating proxy."""
    if os.environ.get("HTTPS", "off").lower() in ("on", "1"):
        return True
    try:
        forwarded = (request.headers.get("X-Forwarded-Proto") or "").split(",")[0].strip().lower()
        if forwarded:
            return forwarded == "https"
        return bool(request.is_secure)
    except Exception:
        return False


def register_app(app):
    """Attach monitoring hooks to a Flask app."""
    @app.before_request
    def _monitoring_before():
        request._monitoring_start = time.perf_counter()

    @app.after_request
    def _monitoring_after(response):
        try:
            duration = (time.perf_counter() - request._monitoring_start) * 1000
        except AttributeError:
            duration = 0.0
        try:
            record_request(
                path=request.path,
                method=request.method,
                status_code=response.status_code,
                duration_ms=duration,
            )
        except Exception:
            pass
        return security_headers(response)

    @app.teardown_appcontext
    def _log_unhandled_errors(exception):
        if exception:
            log_system_exception("TEARDOWN", "CRITICAL", str(exception))
