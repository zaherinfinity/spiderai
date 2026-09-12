"""Security helpers: rate limiting, Cloudflare Turnstile, client IP."""
from __future__ import annotations

import time
import threading
from collections import defaultdict, deque
from typing import Deque, Dict, Optional, Tuple

import requests
from flask import Request

from config import Config

_lock = threading.Lock()
# key -> deque of timestamps
_buckets: Dict[str, Deque[float]] = defaultdict(deque)


def client_ip(req: Request) -> str:
    """Prefer Cloudflare / proxy headers, then remote_addr."""
    cf = req.headers.get("CF-Connecting-IP")
    if cf:
        return cf.strip()
    xff = req.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip()
    real = req.headers.get("X-Real-IP")
    if real:
        return real.strip()
    return (req.remote_addr or "0.0.0.0").strip()


def _prune(dq: Deque[float], window: float, now: float) -> None:
    while dq and now - dq[0] > window:
        dq.popleft()


def rate_limit_ok(
    key: str,
    max_requests: int,
    window_sec: Optional[int] = None,
) -> Tuple[bool, int]:
    """
    Sliding-window rate limit.
    Returns (allowed, remaining).
    """
    window = float(window_sec or Config.RATE_LIMIT_WINDOW_SEC)
    now = time.time()
    with _lock:
        dq = _buckets[key]
        _prune(dq, window, now)
        if len(dq) >= max_requests:
            return False, 0
        dq.append(now)
        remaining = max(0, max_requests - len(dq))
        return True, remaining


def verify_turnstile(token: str, ip: str = None) -> Tuple[bool, str]:
    """Validate Cloudflare Turnstile response token. Pass-through if disabled."""
    if not Config.TURNSTILE_ENABLED:
        return True, "disabled"
    if not token:
        return False, "missing_token"
    try:
        r = requests.post(
            "https://challenges.cloudflare.com/turnstile/v0/siteverify",
            data={
                "secret": Config.TURNSTILE_SECRET_KEY,
                "response": token,
                "remoteip": ip or "",
            },
            timeout=8,
        )
        data = r.json()
        if data.get("success"):
            return True, "ok"
        return False, ",".join(data.get("error-codes") or ["failed"])
    except Exception as e:
        return False, str(e)[:120]


def security_headers(response):
    """Apply baseline security headers (CDN-friendly)."""
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    # Allow Turnstile + self
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://challenges.cloudflare.com https://cdnjs.cloudflare.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com data:; "
        "img-src 'self' data: blob:; "
        "media-src 'self' blob:; "
        "connect-src 'self' https://challenges.cloudflare.com; "
        "frame-src https://challenges.cloudflare.com; "
        "base-uri 'self'; form-action 'self'"
    )
    return response
