#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI Translate Video — Full-stack Flask web app (2026)
Mirrors Telegram Video → Khmer Bot with modern UI, 3D background,
Supabase backend, full admin panel, supporter @foundcount1
"""
import os
import uuid
import time
import secrets
from functools import wraps
from datetime import datetime, timezone

from flask import (
    Flask, render_template, request, redirect, url_for, session,
    jsonify, send_from_directory, send_file, flash, abort, Response,
)
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

from config import Config
from utils import db
from utils import security as sec
from utils.processor import process_video, check_ffmpeg, executor

app = Flask(__name__)
app.config.from_object(Config)
app.secret_key = Config.SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = Config.MAX_CONTENT_LENGTH

os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)
os.makedirs(os.path.join(app.root_path, "static", "img"), exist_ok=True)

# In-memory progress for active jobs (job_id -> dict)

def apply_runtime_settings():
    """Load DB settings into live Config (no restart required)."""
    try:
        rs = db.get_runtime_settings()
    except Exception:
        return
    try:
        mb = int(rs.get("max_file_size_mb") or 100)
        mb = max(1, min(mb, 500))
        Config.MAX_CONTENT_LENGTH = mb * 1024 * 1024
        app.config["MAX_CONTENT_LENGTH"] = Config.MAX_CONTENT_LENGTH
    except Exception:
        pass
    Config.WHISPER_MODEL = rs.get("whisper_model") or Config.WHISPER_MODEL
    Config.KHMER_VOICE = rs.get("khmer_voice") or Config.KHMER_VOICE
    try:
        Config.BG_VOLUME = float(rs.get("bg_volume", Config.BG_VOLUME))
    except Exception:
        pass
    Config.REMOVE_VOCALS = bool(rs.get("remove_vocals"))
    Config.SUPPORTOR = rs.get("supportor") or Config.SUPPORTOR
    Config.SITE_NAME = rs.get("site_name") or Config.SITE_NAME
    Config.POWERED_BY = rs.get("powered_by") or Config.POWERED_BY
    app.config["_runtime"] = rs


# load once at startup
try:
    apply_runtime_settings()
except Exception:
    pass

JOB_PROGRESS = {}

# Paths exempt from global rate limit noise
_RATE_EXEMPT_PREFIX = ("/static/", "/health", "/favicon")


@app.before_request
def _security_before():
    path = request.path or "/"
    if path.startswith(_RATE_EXEMPT_PREFIX):
        return None

    # Browser verification gate — required once per session before browsing
    verify_exempt = (
        path == "/verify"
        or path == "/health"
        or path.startswith("/api/admin/login")
        or path.startswith("/static/")
    )
    if not verify_exempt and not session.get("human_verified"):
        if path.startswith("/api/"):
            return jsonify({"ok": False, "error": "verification_required", "verify_url": "/verify"}), 403
        return redirect(url_for("verify_human", next=request.full_path if request.query_string else path))

    # Maintenance mode (admin routes still allowed)
    try:
        rs = app.config.get("_runtime") or {}
        if rs.get("maintenance_mode") and not session.get("is_admin"):
            if not path.startswith("/admin") and path not in ("/login", "/health", "/verify"):
                return render_template("maintenance.html"), 503
    except Exception:
        pass
    ip = sec.client_ip(request)
    # Global rate limit
    ok, remaining = sec.rate_limit_ok(f"g:{ip}", Config.RATE_LIMIT_MAX_REQUESTS)
    if not ok:
        if path.startswith("/api/"):
            return jsonify({"ok": False, "error": "Rate limit exceeded. Try again later."}), 429
        flash("Too many requests. Please wait a moment.", "error")
        return render_template("429.html"), 429
    # Stricter limits on auth endpoints
    if path in ("/login", "/register", "/admin/login", "/verify") and request.method == "POST":
        ok2, _ = sec.rate_limit_ok(f"auth:{ip}", Config.RATE_LIMIT_MAX_AUTH)
        if not ok2:
            flash("Too many login attempts. Please wait.", "error")
            return render_template("429.html"), 429
    if path == "/upload" and request.method == "POST":
        ok3, _ = sec.rate_limit_ok(f"up:{ip}", Config.RATE_LIMIT_MAX_UPLOAD)
        if not ok3:
            flash("Upload rate limit reached. Please wait.", "error")
            return redirect(url_for("upload"))
    return None


@app.after_request
def _security_after(response):
    return sec.security_headers(response)


@app.context_processor
def inject_globals():
    unread = 0
    uid = session.get("user_id")
    if uid and uid != "admin" and not session.get("is_admin"):
        try:
            unread = db.unread_count(uid)
        except Exception:
            unread = 0
    rs = app.config.get("_runtime") or {}
    return {
        "site_name": rs.get("site_name") or Config.SITE_NAME,
        "powered_by": rs.get("powered_by") or Config.POWERED_BY,
        "supportor": rs.get("supportor") or Config.SUPPORTOR,
        "app_version": Config.VERSION,
        "max_mb": Config.MAX_CONTENT_LENGTH // (1024 * 1024),
        "unread_notifications": unread,
        "turnstile_site_key": Config.TURNSTILE_SITE_KEY,
        "turnstile_enabled": Config.TURNSTILE_ENABLED,
        "site_announcement": rs.get("site_announcement") or "",
        "allow_register": rs.get("allow_register", True),
    }



def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in Config.ALLOWED_EXTENSIONS



def _check_turnstile() -> bool:
    """Return True if Turnstile disabled or token valid."""
    if not Config.TURNSTILE_ENABLED:
        return True
    token = request.form.get("cf-turnstile-response") or ""
    ok, reason = sec.verify_turnstile(token, sec.client_ip(request))
    if not ok:
        flash("Verification failed. Please complete the security check.", "error")
        return False
    return True


def _require_human_verified():
    """Session flag set by /verify screen (always required)."""
    return bool(session.get("human_verified"))

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return decorated


def user_required(f):
    """Logged-in normal user only (not pure admin session without user)."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login", next=request.path))
        if session.get("is_admin") and session.get("user_id") == "admin":
            # Admin can still use site, but prefer dedicated admin panel
            pass
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    """Web admin panel — admin session only (separate from user accounts)."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("is_admin"):
            flash("Access denied.", "error")
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return decorated


def admin_api_required(f):
    """Admin JSON API — X-Admin-Key or Bearer token matching ADMIN_API_KEY."""
    @wraps(f)
    def decorated(*args, **kwargs):
        key = (
            request.headers.get("X-Admin-Key")
            or request.headers.get("Authorization", "").replace("Bearer ", "").strip()
            or request.args.get("api_key")
        )
        if not key or key != Config.ADMIN_API_KEY:
            return jsonify({"ok": False, "error": "Unauthorized — invalid admin API key"}), 401
        return f(*args, **kwargs)
    return decorated


# ═══════════════════════════════════════════════════════════
# PUBLIC PAGES
# ═══════════════════════════════════════════════════════════
@app.route("/")
def index():
    if not session.get("human_verified"):
        return redirect(url_for("verify_human", next="/"))
    stats = db.get_stats()
    return render_template(
        "index.html",
        stats=stats,
        supportor=Config.SUPPORTOR,
        site_name=Config.SITE_NAME,
        powered_by=Config.POWERED_BY,
        version=Config.VERSION,
        max_mb=Config.MAX_CONTENT_LENGTH // (1024 * 1024),
    )


@app.route("/register", methods=["GET", "POST"])
def register():
    rs = app.config.get("_runtime") or {}
    if rs.get("allow_register") is False:
        flash("Registration is currently closed.", "error")
        return redirect(url_for("login"))
    if session.get("user_id") and not session.get("is_admin"):
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        password2 = request.form.get("password2") or ""
        email = (request.form.get("email") or "").strip() or None
        full_name = (request.form.get("full_name") or "").strip() or None

        if len(password) < 6:
            flash("Password must be at least 6 characters.", "error")
            return render_template("register.html")
        if password != password2:
            flash("Passwords do not match.", "error")
            return render_template("register.html")
        if not _check_turnstile():
            return render_template("register.html")
        try:
            ph = generate_password_hash(password)
            user = db.register_user(
                username=username,
                password_hash=ph,
                email=email,
                full_name=full_name,
            )
            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user.get("username") or username
            session["full_name"] = user.get("full_name") or username
            session["is_admin"] = False
            flash("Account created. Welcome!", "success")
            return redirect(url_for("dashboard"))
        except ValueError as e:
            flash(str(e), "error")
        except Exception as e:
            msg = str(e)
            if "PGRST205" in msg or "does not exist" in msg.lower():
                flash("Database not ready. Run schema.sql in Supabase SQL Editor.", "error")
            else:
                flash(f"Register failed: {msg[:160]}", "error")
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    """User login only — admin must use /admin/login"""
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""

        # Block using admin credentials on user login form
        # Do not reveal admin entry — treat as normal failed login
        if username.lower() == Config.ADMIN_USERNAME.lower():
            flash("Invalid username or password.", "error")
            return render_template("login.html")

        if not password:
            flash("Password required.", "error")
            return render_template("login.html")
        if not _check_turnstile():
            return render_template("login.html")

        try:
            user = db.authenticate_user(username, password, check_password_hash)
            if not user:
                flash("Invalid username or password.", "error")
                return render_template("login.html")
            if user.get("_banned") or user.get("is_banned"):
                flash("Your account is banned.", "error")
                return render_template("login.html")
            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user.get("username") or username
            session["full_name"] = user.get("full_name") or username
            session["is_admin"] = False
            flash(f"Welcome, {session['full_name']}!", "success")
            nxt = request.args.get("next") or url_for("dashboard")
            return redirect(nxt)
        except Exception as e:
            flash(f"Login error: {str(e)[:120]}", "error")
    return render_template("login.html")


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    """Hidden admin entry — API key only (not linked in public UI)."""
    if not session.get("human_verified"):
        return redirect(url_for("verify_human", next="/admin/login"))
    if session.get("is_admin"):
        return redirect(url_for("admin_dashboard"))
    if request.method == "POST":
        api_key = (request.form.get("api_key") or "").strip()
        if not _check_turnstile():
            return render_template("admin/login.html")
        if api_key and api_key == Config.ADMIN_API_KEY:
            session["user_id"] = "admin"
            session["username"] = "admin"
            session["full_name"] = "Administrator"
            session["is_admin"] = True
            session["human_verified"] = True
            flash("Admin access granted.", "success")
            return redirect(url_for("admin_dashboard"))
        flash("Invalid API key.", "error")
    return render_template("admin/login.html")


@app.route("/api/admin/login", methods=["POST"])
def api_admin_login():
    """
    Login to admin panel via API key.
    Header: X-Admin-Key: <key>
    or JSON: {"api_key": "<key>"}
    Sets browser session cookie for /admin panel.
    """
    data = request.get_json(silent=True) or {}
    key = (
        request.headers.get("X-Admin-Key")
        or request.headers.get("Authorization", "").replace("Bearer ", "").strip()
        or data.get("api_key")
        or request.form.get("api_key")
    )
    # rate limit handled in before_request for /api/admin/login path partially
    ip = sec.client_ip(request)
    ok, _ = sec.rate_limit_ok(f"adminlogin:{ip}", Config.RATE_LIMIT_MAX_AUTH)
    if not ok:
        return jsonify({"ok": False, "error": "rate_limited"}), 429
    if not key or key != Config.ADMIN_API_KEY:
        return jsonify({"ok": False, "error": "invalid_api_key"}), 401
    session.clear()
    session["user_id"] = "admin"
    session["username"] = "admin"
    session["full_name"] = "Administrator"
    session["is_admin"] = True
    session["human_verified"] = True
    return jsonify({
        "ok": True,
        "message": "Admin session created",
        "redirect": url_for("admin_dashboard"),
    })



@app.route("/verify", methods=["GET", "POST"])
def verify_human():
    """First-visit browser verification screen before site access."""
    nxt = request.args.get("next") or request.form.get("next") or url_for("index")
    # prevent open redirect
    if not nxt.startswith("/"):
        nxt = url_for("index")
    if session.get("human_verified") and request.method == "GET":
        return redirect(nxt)
    if request.method == "POST":
        if Config.TURNSTILE_ENABLED:
            token = (request.form.get("cf-turnstile-response") or "").strip()
            if not token:
                flash("Please complete the Cloudflare check.", "error")
                return render_template("verify.html", next=nxt)
            if not _check_turnstile():
                return render_template("verify.html", next=nxt)
        session["human_verified"] = True
        session["human_verified_at"] = __import__("time").time()
        # success → home (or safe next)
        # Prefer home after Cloudflare success
        dest = url_for("index")
        if nxt and nxt.startswith("/") and not nxt.startswith("/verify"):
            dest = nxt.split("?")[0] or dest
        return redirect(dest)
    return render_template("verify.html", next=nxt)

@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out.", "info")
    return redirect(url_for("index"))


@app.route("/dashboard")
@login_required
def dashboard():
    user_id = session.get("user_id")
    jobs = []
    try:
        all_jobs = db.list_jobs(limit=50)
        jobs = [j for j in all_jobs if j.get("user_id") == user_id][:20]
    except Exception:
        pass
    return render_template(
        "dashboard.html",
        jobs=jobs,
        supportor=Config.SUPPORTOR,
    )


@app.route("/upload", methods=["GET", "POST"])
@login_required
def upload():
    if Config.TURNSTILE_ENABLED and not session.get("human_verified"):
        return redirect(url_for("verify_human", next=request.path))
    if request.method == "GET":
        return render_template("upload.html", max_mb=Config.MAX_CONTENT_LENGTH // (1024 * 1024))

    if "video" not in request.files:
        flash("No video selected.", "error")
        return redirect(url_for("upload"))

    f = request.files["video"]
    if not f or not f.filename:
        flash("No file chosen.", "error")
        return redirect(url_for("upload"))

    if not allowed_file(f.filename):
        flash("Invalid file type. Use mp4, mov, avi, mkv, webm.", "error")
        return redirect(url_for("upload"))

    max_mb = Config.MAX_CONTENT_LENGTH / (1024 * 1024)
    if request.content_length and request.content_length > Config.MAX_CONTENT_LENGTH:
        flash(f"File too large. Max {int(max_mb)} MB.", "error")
        return redirect(url_for("upload"))

    user_id = session.get("user_id")
    if user_id and user_id != "admin" and db.is_banned(user_id):
        flash("You are banned.", "error")
        return redirect(url_for("index"))

    job_id = str(uuid.uuid4())
    job_dir = os.path.join(Config.UPLOAD_FOLDER, job_id)
    os.makedirs(job_dir, exist_ok=True)
    filename = secure_filename(f.filename)
    input_path = os.path.join(job_dir, "input.mp4")
    f.save(input_path)
    actual_size = os.path.getsize(input_path) / (1024 * 1024)
    max_mb = Config.MAX_CONTENT_LENGTH / (1024 * 1024)
    if actual_size > max_mb:
        try:
            os.remove(input_path)
        except Exception:
            pass
        flash(f"File too large ({actual_size:.1f} MB). Max {int(max_mb)} MB.", "error")
        return redirect(url_for("upload"))

    try:
        job = db.create_job(
            user_id=user_id if user_id != "admin" else None,
            filename=filename,
            size_mb=round(actual_size, 2),
        )
        job_id = job["id"]
        # move to job-named dir if needed
        new_dir = os.path.join(Config.UPLOAD_FOLDER, job_id)
        if job_dir != new_dir:
            os.makedirs(new_dir, exist_ok=True)
            os.rename(input_path, os.path.join(new_dir, "input.mp4"))
            try:
                os.rmdir(job_dir)
            except Exception:
                pass
            job_dir = new_dir
            input_path = os.path.join(job_dir, "input.mp4")
    except Exception as e:
        # fallback local only
        job = {"id": job_id}
        print(f"[DB] create_job failed: {e}")

    JOB_PROGRESS[job_id] = {
        "status": "pending",
        "progress": 0,
        "message": "Queued…",
        "output_url": None,
        "error": None,
    }

    def progress_cb(status, pct, msg=""):
        prev = JOB_PROGRESS.get(job_id) or {}
        JOB_PROGRESS[job_id] = {
            "status": status,
            "progress": pct,
            "message": msg,
            "output_url": prev.get("output_url"),
            "download_url": prev.get("download_url"),
            "error": msg if status == "failed" else None,
        }
        try:
            db.update_job(job_id, status=status, progress=pct, error_message=msg if status == "failed" else None)
        except Exception:
            pass

    def run_job():
        try:
            progress_cb("downloading", 5, "Preparing…")
            out_path, original, lang, translated = process_video(
                input_path, job_id, progress_cb=progress_cb,
            )
            # ensure standard filename for media/download routes
            final_path = os.path.join(job_dir, "khmer_dubbed.mp4")
            if out_path and os.path.isfile(out_path) and os.path.abspath(out_path) != os.path.abspath(final_path):
                try:
                    import shutil as _sh
                    _sh.copy2(out_path, final_path)
                except Exception:
                    final_path = out_path
            elif out_path and os.path.isfile(out_path):
                final_path = out_path

            if not os.path.isfile(final_path) or os.path.getsize(final_path) < 500:
                raise Exception("Output video missing after processing")

            watch = f"/media/{job_id}"
            dl = f"/download/{job_id}"
            JOB_PROGRESS[job_id].update({
                "status": "completed",
                "progress": 100,
                "message": "Ready",
                "output_url": watch,
                "download_url": dl,
                "error": None,
            })
            try:
                db.update_job(
                    job_id,
                    status="completed",
                    progress=100,
                    language_detected=lang,
                    original_text=(original or "")[:2000],
                    translated_text=(translated or "")[:2000],
                    output_path=watch,
                )
                if user_id and user_id != "admin":
                    db.increment_video_count(user_id)
            except Exception as e:
                print(f"[DB] update after complete: {e}")
        except Exception as e:
            progress_cb("failed", 0, str(e)[:400])
            try:
                db.update_job(job_id, status="failed", error_message=str(e)[:500])
            except Exception:
                pass

    executor.submit(run_job)
    return redirect(url_for("job_status", job_id=job_id))


@app.route("/job/<job_id>")
@login_required
def job_status(job_id):
    progress = JOB_PROGRESS.get(job_id, {})
    job = None
    try:
        job = db.get_job(job_id)
    except Exception:
        pass
    return render_template(
        "job.html",
        job_id=job_id,
        progress=progress,
        job=job,
    )


def _job_output_path(job_id: str) -> str:
    """Absolute path to dubbed mp4 if present."""
    # primary name
    for name in ("khmer_dubbed.mp4", "output.mp4", "result.mp4"):
        p = os.path.join(Config.UPLOAD_FOLDER, job_id, name)
        if os.path.isfile(p) and os.path.getsize(p) > 500:
            return p
    return ""


def _job_urls(job_id: str) -> dict:
    has = bool(_job_output_path(job_id))
    return {
        "output_url": url_for("media_file", job_id=job_id) if has else None,
        "download_url": url_for("download", job_id=job_id) if has else None,
        "has_file": has,
    }


@app.route("/api/job/<job_id>")
def api_job(job_id):
    data = JOB_PROGRESS.get(job_id)
    if data:
        urls = _job_urls(job_id)
        # prefer live progress; attach file URLs when ready
        out = dict(data)
        if urls["has_file"]:
            out["output_url"] = urls["output_url"]
            out["download_url"] = urls["download_url"]
            if out.get("status") in ("completed", "mixing", "uploading") or out.get("progress", 0) >= 90:
                out["status"] = "completed"
                out["progress"] = 100
        return jsonify(out)

    try:
        job = db.get_job(job_id)
        if job:
            urls = _job_urls(job_id)
            status = job.get("status") or "unknown"
            err = job.get("error_message")
            if urls["has_file"]:
                status = "completed"
                msg = "Ready"
                err = None
            elif status == "completed" and not urls["has_file"]:
                status = "failed"
                msg = "Output file missing on server. Please upload again."
                err = msg
            else:
                msg = err or status
            data = {
                "status": status,
                "progress": 100 if status == "completed" else (job.get("progress") or 0),
                "message": msg,
                "output_url": urls["output_url"],
                "download_url": urls["download_url"],
                "error": err if status == "failed" else None,
                "language": job.get("language_detected"),
                "original": job.get("original_text"),
                "translated": job.get("translated_text"),
                "has_file": urls["has_file"],
            }
            return jsonify(data)
    except Exception:
        pass
    return jsonify({"status": "unknown", "progress": 0, "message": "Not found", "has_file": False})


@app.route("/media/<job_id>")
def media_file(job_id):
    """Stream result video for in-browser playback (Range support for seeking)."""
    path = _job_output_path(job_id)
    if not path:
        abort(404)

    file_size = os.path.getsize(path)
    range_header = request.headers.get("Range")

    if range_header and range_header.startswith("bytes="):
        try:
            rng = range_header.replace("bytes=", "").strip()
            start_s, _, end_s = rng.partition("-")
            start = int(start_s) if start_s else 0
            end = int(end_s) if end_s else file_size - 1
            if end >= file_size:
                end = file_size - 1
            if start > end or start < 0:
                start, end = 0, file_size - 1
            length = end - start + 1
            with open(path, "rb") as f:
                f.seek(start)
                data = f.read(length)
            rv = Response(data, 206, mimetype="video/mp4")
            rv.headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
            rv.headers["Accept-Ranges"] = "bytes"
            rv.headers["Content-Length"] = str(length)
            rv.headers["Cache-Control"] = "private, max-age=3600"
            return rv
        except Exception as ex:
            print("[media range error]", ex)

    return send_file(
        path,
        mimetype="video/mp4",
        conditional=True,
        max_age=3600,
        as_attachment=False,
    )



@app.route("/download/<job_id>")
@login_required
def download(job_id):
    path = _job_output_path(job_id)
    if not path:
        flash("Video file not found. Processing may have failed.", "error")
        return redirect(url_for("job_status", job_id=job_id))
    from flask import send_file
    return send_file(
        path,
        mimetype="video/mp4",
        as_attachment=True,
        download_name=f"khmer_dubbed_{job_id[:8]}.mp4",
        max_age=0,
    )


# ═══════════════════════════════════════════════════════════
# ADMIN PANEL
# ═══════════════════════════════════════════════════════════
@app.route("/admin")
@admin_required
def admin_dashboard():
    stats = db.get_stats()
    try:
        users = db.list_users(limit=20)
        jobs = db.list_jobs(limit=15)
    except Exception:
        users, jobs = [], []
    return render_template(
        "admin/dashboard.html",
        stats=stats,
        users=users,
        jobs=jobs,
        supportor=Config.SUPPORTOR,
    )


@app.route("/admin/users")
@admin_required
def admin_users():
    users = []
    try:
        users = db.list_users(limit=100)
    except Exception:
        pass
    return render_template("admin/users.html", users=users)


@app.route("/admin/jobs")
@admin_required
def admin_jobs():
    jobs = []
    try:
        jobs = db.list_jobs(limit=50)
    except Exception:
        pass
    return render_template("admin/jobs.html", jobs=jobs)


@app.route("/admin/ban/<user_id>", methods=["POST"])
@admin_required
def admin_ban(user_id):
    reason = request.form.get("reason", "Banned by admin")
    try:
        db.ban_user(user_id, reason=reason, admin_id=session.get("user_id"))
        db.log_admin(session.get("user_id"), "ban", user_id, {"reason": reason})
        flash("User banned.", "success")
    except Exception as e:
        flash(f"Ban failed: {e}", "error")
    return redirect(url_for("admin_users"))


@app.route("/admin/unban/<user_id>", methods=["POST"])
@admin_required
def admin_unban(user_id):
    try:
        db.unban_user(user_id)
        db.log_admin(session.get("user_id"), "unban", user_id)
        flash("User unbanned.", "success")
    except Exception as e:
        flash(f"Unban failed: {e}", "error")
    return redirect(url_for("admin_users"))


@app.route("/admin/settings", methods=["GET", "POST"])
@admin_required
def admin_settings():
    if request.method == "POST":
        try:
            max_mb = int(request.form.get("max_file_size_mb") or 100)
            max_mb = max(1, min(max_mb, 500))
            bg = float(request.form.get("bg_volume") or 0.2)
            bg = max(0.0, min(bg, 1.0))
            whisper = (request.form.get("whisper_model") or "base").strip()
            if whisper not in ("tiny", "base", "small", "medium"):
                whisper = "base"
            voice = (request.form.get("khmer_voice") or Config.KHMER_VOICE).strip()
            updates = {
                "site_name": (request.form.get("site_name") or Config.SITE_NAME).strip()[:80],
                "powered_by": (request.form.get("powered_by") or Config.POWERED_BY).strip()[:80],
                "supportor": (request.form.get("supportor") or Config.SUPPORTOR).strip()[:60],
                "max_file_size_mb": max_mb,
                "whisper_model": whisper,
                "khmer_voice": voice[:80],
                "bg_volume": bg,
                "remove_vocals": request.form.get("remove_vocals") == "on",
                "maintenance_mode": request.form.get("maintenance_mode") == "on",
                "allow_register": request.form.get("allow_register") == "on",
                "site_announcement": (request.form.get("site_announcement") or "").strip()[:500],
            }
            db.save_settings(updates)
            apply_runtime_settings()
            db.log_admin(session.get("user_id"), "settings_update", meta=updates)
            flash("Settings saved and applied.", "success")
        except Exception as e:
            flash(f"Save failed: {str(e)[:160]}", "error")
        return redirect(url_for("admin_settings"))

    try:
        settings = db.get_runtime_settings()
    except Exception:
        settings = {}
    settings["ffmpeg"] = check_ffmpeg()
    settings["max_file_size_mb"] = Config.MAX_CONTENT_LENGTH // (1024 * 1024)
    settings["whisper_model"] = Config.WHISPER_MODEL
    settings["khmer_voice"] = Config.KHMER_VOICE
    settings["bg_volume"] = Config.BG_VOLUME
    settings["remove_vocals"] = Config.REMOVE_VOCALS
    settings["site_name"] = Config.SITE_NAME
    settings["powered_by"] = Config.POWERED_BY
    settings["supportor"] = Config.SUPPORTOR
    # prefer runtime dict
    try:
        rs = db.get_runtime_settings()
        settings.update(rs)
        settings["ffmpeg"] = check_ffmpeg()
    except Exception:
        pass
    return render_template("admin/settings.html", settings=settings)



@app.route("/admin/broadcast", methods=["GET", "POST"])
@admin_required
def admin_broadcast():
    """Send notification to all users (update / alert / breaking)."""
    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        body = (request.form.get("message") or request.form.get("body") or "").strip()
        ntype = (request.form.get("type") or "update").strip().lower()
        if ntype not in ("update", "alert", "breaking", "info"):
            ntype = "update"
        if not title or not body:
            flash("Title and message are required.", "error")
            return redirect(url_for("admin_broadcast"))
        try:
            n = db.create_notification(
                title=title,
                body=body,
                ntype=ntype,
                created_by=str(session.get("username") or "admin"),
            )
            db.log_admin(session.get("user_id"), "notification", target_id=n.get("id"), meta={
                "title": title[:120], "type": ntype,
            })
            flash(f"Notification sent to all users ({ntype}).", "success")
        except Exception as e:
            flash(f"Failed to send: {str(e)[:150]}", "error")
        return redirect(url_for("admin_broadcast"))
    items = []
    try:
        items = db.list_notifications(limit=30, active_only=False)
    except Exception:
        pass
    return render_template("admin/broadcast.html", notifications=items)


@app.route("/admin/notifications/<notif_id>/deactivate", methods=["POST"])
@admin_required
def admin_deactivate_notification(notif_id):
    try:
        db.deactivate_notification(notif_id)
        flash("Notification deactivated.", "success")
    except Exception as e:
        flash(str(e)[:120], "error")
    return redirect(url_for("admin_broadcast"))


# ── User notifications ─────────────────────────────────────
@app.route("/notifications")
@login_required
def notifications():
    uid = session.get("user_id")
    if not uid or uid == "admin":
        flash("Login as a user to view notifications.", "info")
        return redirect(url_for("dashboard"))
    items = []
    try:
        items = db.get_user_notifications(uid, limit=50)
    except Exception as e:
        flash(f"Could not load notifications: {str(e)[:100]}", "error")
    return render_template("notifications.html", notifications=items)


@app.route("/notifications/<notif_id>/read", methods=["POST"])
@login_required
def notification_read(notif_id):
    uid = session.get("user_id")
    if uid and uid != "admin":
        db.mark_notification_read(uid, notif_id)
    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json:
        return jsonify({"ok": True})
    return redirect(url_for("notifications"))


@app.route("/notifications/read-all", methods=["POST"])
@login_required
def notifications_read_all():
    uid = session.get("user_id")
    n = 0
    if uid and uid != "admin":
        n = db.mark_all_notifications_read(uid)
    flash(f"Marked {n} as read.", "success")
    return redirect(url_for("notifications"))


@app.route("/api/notifications")
@login_required
def api_notifications():
    uid = session.get("user_id")
    if not uid or uid == "admin":
        return jsonify({"ok": True, "items": [], "unread": 0})
    items = db.get_user_notifications(uid, limit=30)
    return jsonify({
        "ok": True,
        "items": items,
        "unread": sum(1 for x in items if not x.get("is_read")),
    })


@app.route("/api/admin/notifications", methods=["GET", "POST"])
@admin_api_required
def api_admin_notifications():
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        title = (data.get("title") or "").strip()
        body = (data.get("body") or data.get("message") or "").strip()
        ntype = (data.get("type") or "update").strip().lower()
        if not title or not body:
            return jsonify({"ok": False, "error": "title and body required"}), 400
        n = db.create_notification(title=title, body=body, ntype=ntype, created_by="api")
        return jsonify({"ok": True, "notification": n})
    items = db.list_notifications(limit=50, active_only=False)
    return jsonify({"ok": True, "notifications": items})


# ═══════════════════════════════════════════════════════════
# ADMIN API (use X-Admin-Key header — not user login)
# ═══════════════════════════════════════════════════════════
@app.route("/api/admin/stats")
@admin_api_required
def api_admin_stats():
    return jsonify({"ok": True, "stats": db.get_stats()})


@app.route("/api/admin/users")
@admin_api_required
def api_admin_users():
    limit = min(int(request.args.get("limit", 50)), 200)
    return jsonify({"ok": True, "users": db.list_users(limit=limit)})


@app.route("/api/admin/jobs")
@admin_api_required
def api_admin_jobs():
    limit = min(int(request.args.get("limit", 50)), 200)
    status = request.args.get("status")
    return jsonify({"ok": True, "jobs": db.list_jobs(limit=limit, status=status)})


@app.route("/api/admin/ban/<user_id>", methods=["POST"])
@admin_api_required
def api_admin_ban(user_id):
    data = request.get_json(silent=True) or {}
    reason = data.get("reason") or request.form.get("reason") or "Banned via API"
    try:
        db.ban_user(user_id, reason=reason, admin_id="api")
        db.log_admin("api", "ban", user_id, {"reason": reason})
        return jsonify({"ok": True, "message": "banned", "user_id": user_id})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/api/admin/unban/<user_id>", methods=["POST"])
@admin_api_required
def api_admin_unban(user_id):
    try:
        db.unban_user(user_id)
        db.log_admin("api", "unban", user_id)
        return jsonify({"ok": True, "message": "unbanned", "user_id": user_id})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/api/admin/settings", methods=["GET", "POST"])
@admin_api_required
def api_admin_settings():
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        try:
            db.save_settings(data)
            apply_runtime_settings()
            return jsonify({"ok": True, "settings": db.get_runtime_settings()})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 400
    try:
        rs = db.get_runtime_settings()
    except Exception:
        rs = {}
    rs["ffmpeg"] = check_ffmpeg()
    rs["version"] = Config.VERSION
    return jsonify({"ok": True, "settings": rs})


# ═══════════════════════════════════════════════════════════
# HEALTH / STATIC
# ═══════════════════════════════════════════════════════════
@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "version": Config.VERSION,
        "ffmpeg": check_ffmpeg(),
        "time": datetime.now(timezone.utc).isoformat(),
    })


@app.errorhandler(413)
def too_large(e):
    flash("File too large.", "error")
    return redirect(url_for("upload")), 413


@app.errorhandler(404)
def not_found(e):
    return render_template("404.html"), 404


if __name__ == "__main__":
    print("=" * 60)
    print("  AI Translate Video — Web Edition v14.0")
    print(f"  Supportor: {Config.SUPPORTOR}")
    print(f"  Powered by: {Config.POWERED_BY}")
    print("=" * 60)
    app.run(host="0.0.0.0", port=5000, debug=True, threaded=True)
