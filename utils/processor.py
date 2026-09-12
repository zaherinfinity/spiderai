"""
Video → Khmer pipeline (web version).
Mirrors the Telegram bot logic. Heavy ML parts are optional;
Falls back to re-muxed video if optional ML packages are unavailable.
"""
import os
import re
import time
import shutil
import asyncio
import logging
import subprocess
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

from config import Config

logger = logging.getLogger("processor")
logger.setLevel(logging.INFO)

executor = ThreadPoolExecutor(max_workers=2)

KHMER_RE = re.compile(r"[\u1780-\u17FF]")
BAD_PATTERNS = [
    "error 500", "server error", "there was error", "that's all we know",
    "please try again", "unusual traffic", "captcha", "<html", "1500.",
]


def has_khmer(text: str) -> bool:
    return bool(KHMER_RE.search(text or ""))


def is_bad_translation(text: str) -> bool:
    if not text or len(text.strip()) < 2:
        return True
    lower = text.lower()
    for pat in BAD_PATTERNS:
        if pat in lower:
            return True
    if re.search(r"<[a-z][^>]*>", text):
        return True
    if not has_khmer(text):
        return True
    return False


def get_media_duration(path: str) -> float:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=30,
        )
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def check_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=10, check=True)
        subprocess.run(["ffprobe", "-version"], capture_output=True, timeout=10, check=True)
        return True
    except Exception:
        return False


def transcribe_video(video_path: str, model_size: str = "base"):
    """Extract audio + Whisper transcription."""
    from faster_whisper import WhisperModel

    job_dir = os.path.dirname(video_path)
    audio_path = os.path.join(job_dir, "audio.wav")

    subprocess.run(
        ["ffmpeg", "-y", "-i", video_path, "-vn",
         "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", audio_path],
        check=True, capture_output=True, timeout=300,
    )
    if not os.path.exists(audio_path):
        raise Exception("Could not extract audio")

    models = [model_size, "tiny"] if model_size != "tiny" else ["tiny"]
    last_err = None
    for m in models:
        try:
            model = WhisperModel(m, device="cpu", compute_type="int8")
            gen, info = model.transcribe(
                audio_path, beam_size=5, vad_filter=False, word_timestamps=False,
            )
            segments, parts = [], []
            for s in gen:
                t = s.text.strip()
                if not t:
                    continue
                segments.append({"start": float(s.start), "end": float(s.end), "text": t})
                parts.append(t)
            full = " ".join(parts).strip()
            try:
                os.remove(audio_path)
            except Exception:
                pass
            if full:
                return full, info.language, segments
        except Exception as e:
            last_err = e
            continue
    try:
        os.remove(audio_path)
    except Exception:
        pass
    raise Exception(f"Transcription failed: {last_err}")


def translate_one(text: str) -> str:
    try:
        import translators as ts
        engines = ["google", "bing", "alibaba", "yandex"]
        for engine in engines:
            for _ in range(2):
                try:
                    r = ts.translate_text(
                        text, translator=engine,
                        from_language="auto", to_language="km",
                    )
                    if r and not is_bad_translation(r):
                        return r.strip()
                except Exception:
                    time.sleep(0.4)
        return ""
    except Exception:
        return ""


async def _edge_tts(text: str, path: str, retries: int = 3) -> bool:
    for attempt in range(retries):
        try:
            proc = await asyncio.create_subprocess_exec(
                "edge-tts", "--voice", Config.KHMER_VOICE,
                "--text", text, "--write-media", path,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            if proc.returncode == 0 and os.path.exists(path) and os.path.getsize(path) > 1000:
                return True
        except Exception:
            pass
        await asyncio.sleep(1.2 * (attempt + 1))
    return False


def _gtts(text: str, path: str) -> bool:
    try:
        from gtts import gTTS
        gTTS(text=text, lang="km").save(path)
        return os.path.exists(path) and os.path.getsize(path) > 1000
    except Exception:
        return False


async def tts_one(text: str, path: str) -> bool:
    if await _edge_tts(text, path):
        return True
    return await asyncio.to_thread(_gtts, text, path)


async def build_aligned_audio(segments_khmer, job_dir: str, total_duration: float) -> str:
    chunk_paths = []
    for i, seg in enumerate(segments_khmer):
        start, end, text = seg["start"], seg["end"], seg["text"]
        seg_dur = max(0.5, end - start)
        raw = os.path.join(job_dir, f"chunk_{i}_raw.mp3")
        fit = os.path.join(job_dir, f"chunk_{i}_fit.mp3")
        if not await tts_one(text, raw):
            continue
        raw_dur = get_media_duration(raw)
        if raw_dur <= 0:
            continue
        ratio = raw_dur / seg_dur

        def chain(r):
            filt = []
            while r < 0.5:
                filt.append("atempo=0.5")
                r /= 0.5
            while r > 2.0:
                filt.append("atempo=2.0")
                r /= 2.0
            filt.append(f"atempo={r:.6f}")
            return ",".join(filt)

        if abs(ratio - 1.0) < 0.03:
            shutil.copy(raw, fit)
        else:
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-i", raw, "-filter:a", chain(ratio),
                     "-t", str(seg_dur), "-c:a", "libmp3lame", "-b:a", "128k", fit],
                    capture_output=True, timeout=120,
                )
                if not os.path.exists(fit):
                    shutil.copy(raw, fit)
            except Exception:
                shutil.copy(raw, fit)
        chunk_paths.append((start, fit))
        try:
            os.remove(raw)
        except Exception:
            pass

    if not chunk_paths:
        raise Exception("No TTS chunks produced")

    inputs = []
    for _, p in chunk_paths:
        inputs += ["-i", p]
    parts, labels = [], []
    for idx, (start_s, _) in enumerate(chunk_paths):
        ms = int(start_s * 1000)
        lbl = f"a{idx}"
        parts.append(f"[{idx}:a]adelay={ms}|{ms}[{lbl}]")
        labels.append(f"[{lbl}]")
    fstr = ";".join(parts) + f";{''.join(labels)}amix=inputs={len(chunk_paths)}:normalize=0[out]"
    out = os.path.join(job_dir, "khmer_aligned.mp3")
    subprocess.run(
        ["ffmpeg", "-y"] + inputs + [
            "-filter_complex", fstr, "-map", "[out]",
            "-t", str(total_duration),
            "-c:a", "libmp3lame", "-b:a", "128k", out,
        ],
        check=True, capture_output=True, timeout=600,
    )
    for _, p in chunk_paths:
        try:
            os.remove(p)
        except Exception:
            pass
    return out


def extract_background_music(video_path: str, output_path: str) -> bool:
    if not Config.REMOVE_VOCALS:
        return False
    try:
        import demucs.separate
    except ImportError:
        return False
    job_dir = os.path.dirname(video_path)
    temp_dir = os.path.join(job_dir, "demucs_out")
    os.makedirs(temp_dir, exist_ok=True)
    raw = os.path.join(job_dir, "raw_audio.wav")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", video_path, "-vn",
             "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "2", raw],
            check=True, capture_output=True, timeout=300,
        )
        demucs.separate.main([
            "-n", "htdemucs", "--two-stems", "vocals", "-o", temp_dir, raw,
        ])
    except Exception:
        try:
            os.remove(raw)
        except Exception:
            pass
        return False
    no_vocals = None
    for root, _, files in os.walk(temp_dir):
        for f in files:
            if f == "no_vocals.wav":
                no_vocals = os.path.join(root, f)
                break
    if not no_vocals or not os.path.exists(no_vocals):
        return False
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", no_vocals,
             "-c:a", "libmp3lame", "-b:a", "192k", output_path],
            check=True, capture_output=True, timeout=300,
        )
    except Exception:
        return False
    finally:
        try:
            os.remove(raw)
        except Exception:
            pass
        shutil.rmtree(temp_dir, ignore_errors=True)
    return os.path.exists(output_path)


def merge_audio_video(video_path: str, audio_path: str, output_path: str) -> str:
    job_dir = os.path.dirname(video_path)
    khmer_wav = os.path.join(job_dir, "khmer_voice.wav")
    subprocess.run(
        ["ffmpeg", "-y", "-i", audio_path,
         "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "2", khmer_wav],
        check=True, capture_output=True, timeout=300,
    )
    bg_music = os.path.join(job_dir, "background_music.mp3")
    if extract_background_music(video_path, bg_music):
        bg_wav = os.path.join(job_dir, "background_music.wav")
        subprocess.run(
            ["ffmpeg", "-y", "-i", bg_music,
             "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "2", bg_wav],
            check=True, capture_output=True, timeout=300,
        )
    else:
        bg_wav = os.path.join(job_dir, "original_audio.wav")
        subprocess.run(
            ["ffmpeg", "-y", "-i", video_path, "-vn",
             "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "2", bg_wav],
            check=True, capture_output=True, timeout=300,
        )

    filter_complex = (
        f"[0:a]volume={Config.BG_VOLUME}[bg];"
        f"[1:a]volume=1.0[voice];"
        f"[bg][voice]amix=inputs=2:duration=longest:normalize=0[aout]"
    )
    video_dur = get_media_duration(video_path)
    mixed = os.path.join(job_dir, "mixed.m4a")
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", bg_wav, "-i", khmer_wav,
         "-filter_complex", filter_complex, "-map", "[aout]",
         "-t", str(video_dur), "-c:a", "aac", "-b:a", "192k", mixed],
        capture_output=True, text=True, timeout=600,
    )
    if result.returncode != 0:
        raise Exception(f"FFmpeg merge failed: {result.stderr[:300]}")
    subprocess.run(
        ["ffmpeg", "-y", "-i", video_path, "-i", mixed,
         "-c:v", "copy", "-c:a", "aac",
         "-map", "0:v:0", "-map", "1:a:0", "-shortest", output_path],
        check=True, capture_output=True, timeout=600,
    )
    for f in (khmer_wav, bg_music, mixed,
              os.path.join(job_dir, "background_music.wav"),
              os.path.join(job_dir, "original_audio.wav")):
        try:
            os.remove(f)
        except Exception:
            pass
    return output_path


def process_video(
    video_path: str,
    job_id: str,
    progress_cb: Optional[Callable[[str, int, str], None]] = None,
) -> str:
    """
    Full pipeline. progress_cb(status, percent, message)
    Returns output path on success.
    """
    job_dir = os.path.dirname(video_path)
    output = os.path.join(job_dir, "khmer_dubbed.mp4")

    def upd(status: str, pct: int, msg: str = ""):
        if progress_cb:
            progress_cb(status, pct, msg)

    try:
        if not check_ffmpeg():
            raise Exception("FFmpeg not available on server")

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        # 1 Transcribe
        upd("transcribing", 10, "Extracting speech…")
        original, lang, segments = transcribe_video(video_path, Config.WHISPER_MODEL)
        if not original or not segments:
            raise Exception("No speech detected in video")
        upd("transcribing", 25, f"Detected language: {lang}")

        # 2 Translate
        upd("translating", 35, "Translating to Khmer…")
        sk = []
        for seg in segments:
            kh = translate_one(seg["text"])
            if kh and not is_bad_translation(kh):
                sk.append({"start": seg["start"], "end": seg["end"], "text": kh})
        if not sk:
            raise Exception("Translation unavailable")
        upd("translating", 50, f"Translated {len(sk)} segments")

        # 3 TTS + align
        upd("tts", 55, "Generating Khmer voice…")
        total_dur = get_media_duration(video_path)
        aligned = loop.run_until_complete(build_aligned_audio(sk, job_dir, total_dur))
        upd("tts", 70, "Voice ready")

        # 4 Mix
        upd("mixing", 75, "Removing vocals + mixing music…")
        merge_audio_video(video_path, aligned, output)
        upd("mixing", 90, "Audio mixed")

        if not os.path.exists(output) or os.path.getsize(output) < 1000:
            raise Exception("Output video generation failed")

        upd("completed", 100, "Done")
        return output, original, lang, " ".join(s["text"] for s in sk)

    except Exception as e:
        logger.error(traceback.format_exc())
        # Last-resort: re-mux original so user can still download something
        try:
            fallback = os.path.join(job_dir, "khmer_dubbed.mp4")
            if check_ffmpeg() and not os.path.isfile(fallback):
                subprocess.run(
                    ["ffmpeg", "-y", "-i", video_path, "-c", "copy", "-movflags", "+faststart", fallback],
                    capture_output=True, timeout=300,
                )
                if os.path.isfile(fallback) and os.path.getsize(fallback) > 500:
                    upd("completed", 100, f"Pipeline partial: {str(e)[:120]}")
                    return fallback, "", "unknown", ""
        except Exception:
            pass
        upd("failed", 0, str(e)[:400])
        raise
    finally:
        # cleanup temp files — NEVER delete final output (khmer_dubbed.mp4)
        try:
            keep = {"khmer_dubbed.mp4", "input.mp4", "output.mp4", "result.mp4"}
            for f in os.listdir(job_dir):
                if f in keep:
                    continue
                if f.startswith((
                    "chunk_", "khmer_aligned", "khmer_voice", "audio.",
                    "background_", "mixed", "raw_audio", "original_audio",
                )) or f.endswith((".wav", ".mp3", ".m4a")) and f not in keep:
                    try:
                        os.remove(os.path.join(job_dir, f))
                    except Exception:
                        pass
            ddir = os.path.join(job_dir, "demucs_out")
            if os.path.exists(ddir):
                shutil.rmtree(ddir, ignore_errors=True)
        except Exception:
            pass
