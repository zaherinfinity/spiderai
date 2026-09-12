import os
from pathlib import Path
from dotenv import load_dotenv

_BASE = Path(__file__).resolve().parent
load_dotenv(_BASE / ".env")

class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "ai-translate-video-2026-default")
    SUPABASE_URL = os.getenv("SUPABASE_URL")
    SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")
    SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET")
    POSTGRES_URL = os.getenv("POSTGRES_URL")
    POSTGRES_URL_NON_POOLING = os.getenv("POSTGRES_URL_NON_POOLING")

    MAX_CONTENT_LENGTH = int(os.getenv("MAX_CONTENT_LENGTH", 100 * 1024 * 1024))
    # Vercel filesystem is read-only except /tmp
    _upload_env = os.getenv("UPLOAD_FOLDER", "").strip()
    if _upload_env:
        UPLOAD_FOLDER = _upload_env
    elif os.getenv("VERCEL") or os.getenv("AWS_LAMBDA_FUNCTION_NAME"):
        UPLOAD_FOLDER = "/tmp/ai_translate_uploads"
    else:
        UPLOAD_FOLDER = str(_BASE / "static" / "uploads")
    ALLOWED_EXTENSIONS = {"mp4", "mov", "avi", "mkv", "webm", "m4v"}

    ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "SpiderZaher2026!")
    ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "spider-zaher-admin-api-2026-secure-key")

    SUPPORTOR = os.getenv("SUPPORTOR_HANDLE", "@foundcount1")
    KHMER_VOICE = os.getenv("KHMER_VOICE", "km-KH-SreymomNeural")
    WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base")
    BG_VOLUME = float(os.getenv("BG_VOLUME", "0.20"))
    REMOVE_VOCALS = os.getenv("REMOVE_VOCALS", "true").lower() == "true"

    SITE_NAME = "AI Translate Video"
    POWERED_BY = "SPIDER ZAHER"
    VERSION = "14.3-web"

    # Cloudflare Turnstile
    TURNSTILE_SITE_KEY = os.getenv("TURNSTILE_SITE_KEY", "").strip()
    TURNSTILE_SECRET_KEY = os.getenv("TURNSTILE_SECRET_KEY", "").strip()
    TURNSTILE_ENABLED = bool(TURNSTILE_SITE_KEY and TURNSTILE_SECRET_KEY)

    # Rate limits
    RATE_LIMIT_WINDOW_SEC = int(os.getenv("RATE_LIMIT_WINDOW_SEC", "60"))
    RATE_LIMIT_MAX_REQUESTS = int(os.getenv("RATE_LIMIT_MAX_REQUESTS", "120"))
    RATE_LIMIT_MAX_AUTH = int(os.getenv("RATE_LIMIT_MAX_AUTH", "10"))
    RATE_LIMIT_MAX_UPLOAD = int(os.getenv("RATE_LIMIT_MAX_UPLOAD", "8"))
