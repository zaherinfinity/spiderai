"""Supabase client + helper functions for AI Translate Video."""
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from supabase import create_client, Client
from config import Config

_supabase: Optional[Client] = None
_admin: Optional[Client] = None


def get_client(service: bool = False) -> Client:
    global _supabase, _admin
    if service:
        if _admin is None:
            _admin = create_client(
                Config.SUPABASE_URL,
                Config.SUPABASE_SERVICE_ROLE_KEY,
            )
        return _admin
    if _supabase is None:
        _supabase = create_client(
            Config.SUPABASE_URL,
            Config.SUPABASE_ANON_KEY,
        )
    return _supabase


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Users ──────────────────────────────────────────────────
def _bump_user_count():
    try:
        sb = get_client(service=True)
        stats = sb.table("stats").select("total_users").eq("id", 1).execute()
        total = (stats.data[0]["total_users"] if stats.data else 0) + 1
        sb.table("stats").update({"total_users": total, "updated_at": now_iso()}).eq("id", 1).execute()
    except Exception:
        pass


def get_user_by_username(username: str) -> Optional[Dict]:
    sb = get_client(service=True)
    res = sb.table("users").select("*").eq("username", username).execute()
    return res.data[0] if res.data else None


def get_user_by_email(email: str) -> Optional[Dict]:
    sb = get_client(service=True)
    res = sb.table("users").select("*").eq("email", email).execute()
    return res.data[0] if res.data else None


def register_user(
    username: str,
    password_hash: str,
    email: str = None,
    full_name: str = None,
) -> Dict[str, Any]:
    """Create a normal user account (role=user). Raises ValueError if taken."""
    sb = get_client(service=True)
    username = (username or "").strip().lower()
    if not username or len(username) < 3:
        raise ValueError("Username must be at least 3 characters")
    if not username.replace("_", "").replace("-", "").isalnum():
        raise ValueError("Username: letters, numbers, _ and - only")
    if len(username) > 32:
        raise ValueError("Username max 32 characters")
    if get_user_by_username(username):
        raise ValueError("Username already taken")
    if email:
        email = email.strip().lower()
        if get_user_by_email(email):
            raise ValueError("Email already registered")

    payload = {
        "username": username,
        "email": email or f"{username}@user.local",
        "full_name": full_name or username,
        "password_hash": password_hash,
        "role": "user",
        "videos_count": 0,
        "is_banned": False,
        "last_seen": now_iso(),
        "created_at": now_iso(),
    }
    res = sb.table("users").insert(payload).execute()
    _bump_user_count()
    return res.data[0] if res.data else payload


def authenticate_user(username: str, password: str, check_password_fn) -> Optional[Dict]:
    """Verify username + password. check_password_fn(hash, password) -> bool."""
    u = get_user_by_username((username or "").strip().lower())
    if not u:
        # also try email
        u = get_user_by_email((username or "").strip().lower())
    if not u or not u.get("password_hash"):
        return None
    if not check_password_fn(u["password_hash"], password):
        return None
    if u.get("is_banned"):
        return {"_banned": True, **u}
    sb = get_client(service=True)
    sb.table("users").update({"last_seen": now_iso()}).eq("id", u["id"]).execute()
    return u


def get_or_create_user(
    email: str = None,
    username: str = None,
    full_name: str = None,
    telegram_id: str = None,
    password_hash: str = None,
) -> Dict[str, Any]:
    sb = get_client(service=True)
    if email:
        res = sb.table("users").select("*").eq("email", email).execute()
        if res.data:
            u = res.data[0]
            sb.table("users").update({"last_seen": now_iso()}).eq("id", u["id"]).execute()
            return u
    if username:
        res = sb.table("users").select("*").eq("username", username).execute()
        if res.data:
            u = res.data[0]
            sb.table("users").update({"last_seen": now_iso()}).eq("id", u["id"]).execute()
            return u
    if telegram_id:
        res = sb.table("users").select("*").eq("telegram_id", telegram_id).execute()
        if res.data:
            u = res.data[0]
            sb.table("users").update({"last_seen": now_iso()}).eq("id", u["id"]).execute()
            return u

    payload = {
        "email": email,
        "username": username or (email.split("@")[0] if email else "guest"),
        "full_name": full_name or username or "Guest",
        "telegram_id": telegram_id,
        "password_hash": password_hash,
        "role": "user",
        "videos_count": 0,
        "last_seen": now_iso(),
        "created_at": now_iso(),
    }
    res = sb.table("users").insert(payload).execute()
    _bump_user_count()
    return res.data[0] if res.data else payload


def get_user_by_id(uid: str) -> Optional[Dict]:
    sb = get_client(service=True)
    res = sb.table("users").select("*").eq("id", uid).execute()
    return res.data[0] if res.data else None


def list_users(limit: int = 50, offset: int = 0) -> List[Dict]:
    sb = get_client(service=True)
    res = (
        sb.table("users")
        .select("id,username,email,full_name,role,is_banned,videos_count,last_seen,created_at")
        .order("last_seen", desc=True)
        .range(offset, offset + limit - 1)
        .execute()
    )
    return res.data or []


def ban_user(user_id: str, reason: str = "", admin_id: str = None) -> bool:
    sb = get_client(service=True)
    sb.table("users").update({"is_banned": True}).eq("id", user_id).execute()
    sb.table("banned").insert({
        "user_id": user_id,
        "reason": reason,
        "banned_by": admin_id,
    }).execute()
    return True


def unban_user(user_id: str) -> bool:
    sb = get_client(service=True)
    sb.table("users").update({"is_banned": False}).eq("id", user_id).execute()
    sb.table("banned").delete().eq("user_id", user_id).execute()
    return True


def is_banned(user_id: str) -> bool:
    u = get_user_by_id(user_id)
    return bool(u and u.get("is_banned"))


# ── Jobs ───────────────────────────────────────────────────
def create_job(user_id: str, filename: str, size_mb: float) -> Dict:
    sb = get_client(service=True)
    payload = {
        "user_id": user_id,
        "original_filename": filename,
        "status": "pending",
        "progress": 0,
        "file_size_mb": size_mb,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    res = sb.table("jobs").insert(payload).execute()
    return res.data[0]


def update_job(job_id: str, **kwargs) -> None:
    sb = get_client(service=True)
    kwargs["updated_at"] = now_iso()
    if kwargs.get("status") == "completed":
        kwargs["completed_at"] = now_iso()
    sb.table("jobs").update(kwargs).eq("id", job_id).execute()


def get_job(job_id: str) -> Optional[Dict]:
    sb = get_client(service=True)
    res = sb.table("jobs").select("*").eq("id", job_id).execute()
    return res.data[0] if res.data else None


def list_jobs(limit: int = 30, status: str = None) -> List[Dict]:
    sb = get_client(service=True)
    q = sb.table("jobs").select("*").order("created_at", desc=True).limit(limit)
    if status:
        q = q.eq("status", status)
    res = q.execute()
    return res.data or []


def increment_video_count(user_id: str) -> None:
    sb = get_client(service=True)
    u = get_user_by_id(user_id)
    if u:
        sb.table("users").update({
            "videos_count": (u.get("videos_count") or 0) + 1,
            "last_seen": now_iso(),
        }).eq("id", user_id).execute()
    try:
        stats = sb.table("stats").select("total_videos").eq("id", 1).execute()
        total = (stats.data[0]["total_videos"] if stats.data else 0) + 1
        sb.table("stats").update({"total_videos": total, "updated_at": now_iso()}).eq("id", 1).execute()
    except Exception:
        pass


def get_stats() -> Dict:
    sb = get_client(service=True)
    try:
        res = sb.table("stats").select("*").eq("id", 1).execute()
        if res.data:
            return res.data[0]
    except Exception:
        pass
    return {
        "total_videos": 0,
        "total_users": 0,
        "total_failed": 0,
        "started_at": now_iso(),
    }


def log_admin(admin_id: str = None, action: str = "", target_id: str = None, meta: dict = None):
    sb = get_client(service=True)
    try:
        payload = {
            "action": action or "unknown",
            "target_id": str(target_id) if target_id else None,
            "meta": meta or {},
        }
        # admin_id column is uuid — only set when valid uuid
        if admin_id and len(str(admin_id)) == 36 and str(admin_id).count("-") == 4:
            payload["admin_id"] = admin_id
        sb.table("admin_logs").insert(payload).execute()
    except Exception:
        pass


def get_settings() -> Dict:
    """Raw key->value from settings table (JSON values decoded by supabase)."""
    sb = get_client(service=True)
    try:
        res = sb.table("settings").select("*").execute()
        out = {}
        for row in (res.data or []):
            k = row.get("key")
            v = row.get("value")
            # unwrap JSON-encoded strings stored as "\"base\""
            if isinstance(v, str) and len(v) >= 2 and v[0] == '"' and v[-1] == '"':
                try:
                    import json as _json
                    v = _json.loads(v)
                except Exception:
                    pass
            out[k] = v
        return out
    except Exception:
        return {}


def save_settings(updates: Dict[str, Any]) -> bool:
    """Upsert settings keys. Values stored as JSON-compatible."""
    sb = get_client(service=True)
    now = now_iso()
    for key, value in updates.items():
        if key is None:
            continue
        sb.table("settings").upsert({
            "key": str(key),
            "value": value,
            "updated_at": now,
        }).execute()
    return True


def get_runtime_settings() -> Dict[str, Any]:
    """Merged defaults + DB for live site operation."""
    from config import Config
    raw = get_settings()

    def _bool(v, default=False):
        if v is None:
            return default
        if isinstance(v, bool):
            return v
        return str(v).lower() in ("1", "true", "yes", "on")

    def _float(v, default=0.0):
        try:
            return float(v)
        except Exception:
            return default

    def _int(v, default=0):
        try:
            return int(float(v))
        except Exception:
            return default

    def _str(v, default=""):
        if v is None:
            return default
        return str(v).strip() or default

    return {
        "site_name": _str(raw.get("site_name"), Config.SITE_NAME),
        "powered_by": _str(raw.get("powered_by"), Config.POWERED_BY),
        "supportor": _str(raw.get("supportor"), Config.SUPPORTOR),
        "max_file_size_mb": _int(raw.get("max_file_size_mb"), Config.MAX_CONTENT_LENGTH // (1024 * 1024)),
        "whisper_model": _str(raw.get("whisper_model"), Config.WHISPER_MODEL),
        "khmer_voice": _str(raw.get("khmer_voice"), Config.KHMER_VOICE),
        "bg_volume": _float(raw.get("bg_volume"), Config.BG_VOLUME),
        "remove_vocals": _bool(raw.get("remove_vocals"), Config.REMOVE_VOCALS),
        "maintenance_mode": _bool(raw.get("maintenance_mode"), False),
        "allow_register": _bool(raw.get("allow_register"), True),
        "site_announcement": _str(raw.get("site_announcement"), ""),
    }


# ── Notifications ──────────────────────────────────────────
def create_notification(
    title: str,
    body: str,
    ntype: str = "update",
    created_by: str = None,
) -> Dict[str, Any]:
    sb = get_client(service=True)
    if ntype not in ("update", "alert", "breaking", "info"):
        ntype = "info"
    payload = {
        "title": (title or "").strip()[:200],
        "body": (body or "").strip()[:4000],
        "type": ntype,
        "is_active": True,
        "created_by": created_by,
        "created_at": now_iso(),
    }
    res = sb.table("notifications").insert(payload).execute()
    return res.data[0] if res.data else payload


def list_notifications(limit: int = 50, active_only: bool = True) -> List[Dict]:
    sb = get_client(service=True)
    q = sb.table("notifications").select("*").order("created_at", desc=True).limit(limit)
    if active_only:
        q = q.eq("is_active", True)
    res = q.execute()
    return res.data or []


def deactivate_notification(notif_id: str) -> bool:
    sb = get_client(service=True)
    sb.table("notifications").update({"is_active": False}).eq("id", notif_id).execute()
    return True


def get_user_notifications(user_id: str, limit: int = 40) -> List[Dict]:
    """Active notifications with is_read flag for this user."""
    sb = get_client(service=True)
    notifs = list_notifications(limit=limit, active_only=True)
    if not notifs:
        return []
    reads = []
    try:
        res = (
            sb.table("notification_reads")
            .select("notification_id")
            .eq("user_id", user_id)
            .execute()
        )
        reads = [r["notification_id"] for r in (res.data or [])]
    except Exception:
        pass
    out = []
    for n in notifs:
        item = dict(n)
        item["is_read"] = n["id"] in reads
        out.append(item)
    return out


def unread_count(user_id: str) -> int:
    items = get_user_notifications(user_id, limit=100)
    return sum(1 for n in items if not n.get("is_read"))


def mark_notification_read(user_id: str, notif_id: str) -> bool:
    sb = get_client(service=True)
    try:
        sb.table("notification_reads").upsert({
            "notification_id": notif_id,
            "user_id": user_id,
            "read_at": now_iso(),
        }).execute()
        return True
    except Exception:
        try:
            # fallback insert ignore
            existing = (
                sb.table("notification_reads")
                .select("id")
                .eq("notification_id", notif_id)
                .eq("user_id", user_id)
                .execute()
            )
            if not existing.data:
                sb.table("notification_reads").insert({
                    "notification_id": notif_id,
                    "user_id": user_id,
                    "read_at": now_iso(),
                }).execute()
            return True
        except Exception:
            return False


def mark_all_notifications_read(user_id: str) -> int:
    items = get_user_notifications(user_id, limit=100)
    n = 0
    for item in items:
        if not item.get("is_read"):
            if mark_notification_read(user_id, item["id"]):
                n += 1
    return n
