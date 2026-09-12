# AI Translate Video — Full Stack Web (2026)

Flask + Supabase full-stack app mirroring the Telegram **Video → Khmer Bot**.

**Powered by SPIDER ZAHER** · **Supportor [@foundcount1](https://t.me/foundcount1)**

## Features

- Modern glass / neon UI with **live 3D Three.js background**
- Upload video → Whisper transcribe → multi-engine translate to Khmer → Edge-TTS / gTTS → Demucs vocal removal → FFmpeg mix (keep music)
- Live job progress polling
- User dashboard + guest login
- **Full admin panel**: stats, users, ban/unban, jobs, settings, broadcast log
- Supabase (Postgres) database with schema in `schema.sql`
- Max 20 MB uploads (configurable)

## Quick start

```bash
cd ai_translate_video
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install flask flask-cors python-dotenv supabase psycopg2-binary werkzeug requests Pillow

# Optional (full pipeline):
# pip install -r requirements.txt
# sudo apt install -y ffmpeg
# npm not required

# Apply schema in Supabase SQL Editor: schema.sql

export FLASK_APP=app.py
python app.py
# → http://0.0.0.0:5000
```

### Admin login

- Username: `admin` (from `.env` `ADMIN_USERNAME`)
- Password: `SpiderZaher2026!` (from `.env` `ADMIN_PASSWORD`) — **change in production**

Guest: any username (password optional).

## Environment

Credentials are in `.env` (Supabase URL, anon + service role keys, Postgres, admin password, supportor handle).

## Project layout

```
app.py              # Flask routes
config.py
schema.sql          # Supabase tables
utils/db.py         # Supabase helpers
utils/processor.py  # Video pipeline
templates/          # Jinja UI
static/css|js|img   # 2026 UI + 3D bg
```

## Pipeline steps (same as Telegram bot)

1. Transcribe (faster-whisper)
2. Translate → km (translators)
3. Khmer TTS (edge-tts / gTTS) + time align
4. Demucs optional vocal strip + mix BG at 20%
5. Mux video + new audio

## Supportor

**@foundcount1** — credited on home, footer, admin, and dashboard.


## Deploy

### Vercel (web UI only)
Uses lightweight `requirements.txt` (no torch/whisper).

1. Set Environment Variables in Vercel (from `.env`: Supabase keys, `SECRET_KEY`, `ADMIN_API_KEY`, Turnstile keys, etc.)
2. Deploy the repo — do **not** install `requirements-full.txt` on Vercel

Video dubbing needs FFmpeg + ML packages; run that on a **VPS** with:
```bash
pip install -r requirements-full.txt
sudo apt install -y ffmpeg
```

### Bundle size error
If Vercel says bundle > 500 MB, ensure `requirements.txt` has no `torch`, `demucs`, or `faster-whisper`.
