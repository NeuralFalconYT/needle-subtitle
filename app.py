#!/usr/bin/env python3
from __future__ import annotations
import inspect
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import wave

# ============================================================
# ENVIRONMENT  (Hugging Face Space vs local machine)
# ============================================================

try:
    import spaces
except ImportError:
    spaces = None

# Hugging Face sets SPACE_ID automatically on every Space
ON_SPACES = bool(os.environ.get("SPACE_ID"))
IS_LOCAL = not ON_SPACES


def gpu(fn):
    """@spaces.GPU only on Hugging Face; a no-op locally."""
    if spaces is not None and ON_SPACES:
        return spaces.GPU(fn)
    return fn


# ============================================================
# CONFIG
# ============================================================

LANGUAGES = {
    "English": "en",
    "French": "fr",
    "Spanish": "es",
    "German": "de",
    "Dutch": "nl",
    "Italian": "it",
    "Polish": "pl",
}

# ---- Needle chunking ----
MAX_CHUNK = 29.0
SEARCH_BACK = 5.0
MIN_CHUNK = 5.0

# ---- Horizontal (YouTube / movie) rules ----
MAX_CHARS_PER_LINE = 38
MAX_LINES = 2
MAX_DURATION = 7.0
MIN_DURATION = 1.0
MAX_MERGE_DURATION = 5.5
MIN_GAP = 0.04
TAIL = 0.15
PAUSE_SPLIT = 0.80
MIN_WORDS_SPLIT = 3

# ---- Vertical (Shorts / Reels) rules ----
SHORT_PAUSE = 0.35        # pause that always starts a new cue
SHORT_MAX_CHARS = 22      # keep cues narrow for phone screens
SHORT_MIN_DURATION = 0.30
SHORT_HOLD = 0.12
SHORT_GAP = 0.02

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


# ============================================================
# SMALL HELPERS
# ============================================================

def fmt_dur(seconds: float) -> str:
    s = max(0, int(round(seconds)))
    h, r = divmod(s, 3600)
    m, s = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def clean_path(raw: str) -> str:
    """Accepts "path", 'path', r"path", & 'path' (PowerShell drag-drop)."""
    s = raw.strip()
    if s.startswith("& "):
        s = s[2:].strip()
    if s[:2].lower() in ('r"', "r'"):
        s = s[1:]
    s = s.strip().strip("\"'").strip()
    return os.path.abspath(os.path.expanduser(s))


def srt_time(seconds: float) -> str:
    ms = max(0, int(round(float(seconds) * 1000)))
    h, r = divmod(ms, 3_600_000)
    m, r = divmod(r, 60_000)
    s, ms = divmod(r, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(path: str, cues) -> int:
    n = 0
    with open(path, "w", encoding="utf-8-sig", newline="\n") as f:
        for start, end, text in cues:
            if end <= start or not text.strip():
                continue
            n += 1
            f.write(f"{n}\n{srt_time(start)} --> {srt_time(end)}\n{text}\n\n")
    return n


def make_bar(frac: float, t0: float, label: str, extra: str = "", width: int = 30) -> str:
    frac = min(1.0, max(0.0, frac))
    filled = int(width * frac)
    elapsed = time.perf_counter() - t0
    eta = (elapsed / frac - elapsed) if frac > 0.001 else 0
    return (f"[{'█' * filled}{'░' * (width - filled)}] {frac * 100:5.1f}%  "
            f"ETA {fmt_dur(eta)}  {label} {extra}").strip()


# ============================================================
# AUDIO
# ============================================================

def probe_duration(path: str) -> float:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", path],
            capture_output=True, text=True)
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def extract_audio(src: str, wav: str, on_progress, stop=None) -> None:
    total = probe_duration(src)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostats",
           "-progress", "pipe:1", "-y", "-i", src, "-vn",
           "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", wav]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)
    except FileNotFoundError:
        raise RuntimeError("ffmpeg not found. Install it and add it to PATH.")
    for line in proc.stdout:
        if stop is not None and stop.is_set():
            proc.kill()
            proc.wait()
            return
        if line.startswith(("out_time_us=", "out_time_ms=")):
            val = line.split("=", 1)[1].strip()
            if val.lstrip("-").isdigit() and total > 0:
                on_progress(int(val) / 1e6 / total)
    proc.wait()
    err = proc.stderr.read()
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg failed:\n" + err)


def wav_info(path: str) -> dict:
    with wave.open(path, "rb") as wf:
        return {"channels": wf.getnchannels(), "width": wf.getsampwidth(),
                "rate": wf.getframerate(), "duration": wf.getnframes() / wf.getframerate()}


def extract_wav_chunk(wav: str, out: str, start: float, end: float) -> None:
    with wave.open(wav, "rb") as src:
        rate = src.getframerate()
        src.setpos(int(start * rate))
        data = src.readframes(int(end * rate) - int(start * rate))
        with wave.open(out, "wb") as dst:
            dst.setnchannels(src.getnchannels())
            dst.setsampwidth(src.getsampwidth())
            dst.setframerate(rate)
            dst.writeframes(data)


def create_chunks(duration: float, speech):
    chunks, current = [], 0.0
    while current < duration:
        if duration - current <= MAX_CHUNK:
            chunks.append((current, duration, "FINAL"))
            break
        hard = current + MAX_CHUNK
        lo = hard - SEARCH_BACK
        cands = [float(s["end"]) for s in speech
                 if lo <= float(s["end"]) <= hard
                 and float(s["end"]) >= current + MIN_CHUNK]
        if cands:
            chunks.append((current, max(cands), "VAD"))
            current = max(cands)
        else:
            chunks.append((current, hard, "HARD"))
            current = hard
    return chunks


# ============================================================
# TEXT UTILITIES
# ============================================================

SENTENCE_END = re.compile(r"""[.!?。！？]+["'”’)\]]*$""")
SOFT_BREAK = re.compile(r"""[,;:—–…]+["'”’)\]]*$""")
ABBREV = {"mr.", "mrs.", "ms.", "dr.", "prof.", "sr.", "jr.", "st.", "vs.", "no."}
WEAK_END = {"a", "an", "the", "and", "but", "or", "so", "to", "of", "in", "on",
            "at", "for", "with", "my", "your", "his", "her", "our", "their",
            "is", "are", "was", "were", "i", "we", "you", "he", "she", "it",
            "they", "if", "that", "because", "than", "as", "by", "from",
            # a few common French / Spanish / German / Italian function words
            "le", "la", "les", "un", "une", "de", "du", "des", "et", "el", "los",
            "las", "una", "y", "en", "der", "die", "das", "und", "ein", "il",
            "lo", "di", "che", "e"}
WEAK_START = {"and", "but", "or", "so", "because", "that", "which", "who",
              "when", "if", "while", "then", "et", "mais", "y", "pero",
              "und", "aber", "e", "ma"}


def wtext(ws) -> str:
    return " ".join(w["word"] for w in ws)


def is_sentence_end(token: str) -> bool:
    t = token.lower()
    if t in ABBREV or t.endswith("...") or t.endswith("…"):
        return False
    return bool(SENTENCE_END.search(token))


def update_quote_state(state: bool, token: str) -> bool:
    for ch in token:
        if ch == "“":
            state = True
        elif ch == "”":
            state = False
        elif ch == '"':
            state = not state
    return state


# ============================================================
# 1) HORIZONTAL  (YouTube / movies, sentence level)
# ============================================================

def break_lines(text: str):
    if len(text) <= MAX_CHARS_PER_LINE:
        return [text]
    toks = text.split()
    best, best_score = None, None
    for i in range(1, len(toks)):
        a, b = " ".join(toks[:i]), " ".join(toks[i:])
        if len(a) > MAX_CHARS_PER_LINE or len(b) > MAX_CHARS_PER_LINE:
            continue
        score = abs(len(a) - len(b))
        if a[-1] in ",;:.?!…—":
            score -= 8
        if toks[i - 1].lower().strip(",.;:") in WEAK_END:
            score += 10
        if toks[i].lower() in WEAK_START:
            score -= 4
        if len(a) > len(b):
            score += 3
        if best_score is None or score < best_score:
            best, best_score = [a, b], score
    return best


def fits(ws) -> bool:
    if ws[-1]["end"] - ws[0]["start"] > MAX_DURATION:
        return False
    text = wtext(ws)
    if len(text) > MAX_CHARS_PER_LINE * MAX_LINES:
        return False
    return break_lines(text) is not None


def split_score(ws, i: int) -> float:
    left, right = ws[:i], ws[i:]
    last = left[-1]["word"]
    score = 0.0
    if is_sentence_end(last):
        score += 30
    elif SOFT_BREAK.search(last):
        score += 12
    gap = right[0]["start"] - left[-1]["end"]
    score += min(gap, 1.0) * 20
    lc, rc = len(wtext(left)), len(wtext(right))
    score -= abs(lc - rc) / max(1, lc + rc) * 25
    if last.lower().strip(",.;:!?") in WEAK_END:
        score -= 15
    if right[0]["word"].lower() in WEAK_START:
        score += 5
    if wtext(left).count('"') % 2 == 1:
        score -= 12
    if fits(left):
        score += 10
    return score


def split_to_fit(ws):
    if len(ws) < 2 or fits(ws):
        return [ws]
    n = len(ws)
    m = MIN_WORDS_SPLIT if n >= 2 * MIN_WORDS_SPLIT else 1
    best_i, best = None, None
    for i in range(m, n - m + 1):
        s = split_score(ws, i)
        if best is None or s > best:
            best_i, best = i, s
    if best_i is None:
        best_i = n // 2
    return split_to_fit(ws[:best_i]) + split_to_fit(ws[best_i:])


def sentence_units(words):
    units, cur, in_quote = [], [], False
    for i, w in enumerate(words):
        cur.append(w)
        in_quote = update_quote_state(in_quote, w["word"])
        boundary = False
        if i + 1 < len(words):
            if words[i + 1]["start"] - w["end"] >= PAUSE_SPLIT:
                boundary = True
            elif is_sentence_end(w["word"]) and not in_quote:
                boundary = True
        if boundary:
            units.append(cur)
            cur, in_quote = [], False
    if cur:
        units.append(cur)
    return units


def build_horizontal(words):
    if not words:
        return []
    fragments = []
    for unit in sentence_units(words):
        fragments.extend(split_to_fit(unit))

    groups = []
    for frag in fragments:
        if groups:
            prev = groups[-1]
            gap = frag[0]["start"] - prev[-1]["end"]
            merged = prev + frag
            if (gap < PAUSE_SPLIT and fits(merged)
                    and merged[-1]["end"] - merged[0]["start"] <= MAX_MERGE_DURATION):
                groups[-1] = merged
                continue
        groups.append(frag)

    cues = []
    for i, g in enumerate(groups):
        start, last_end = g[0]["start"], g[-1]["end"]
        end = max(last_end + TAIL, start + MIN_DURATION)
        if i + 1 < len(groups):
            end = min(end, groups[i + 1][0]["start"] - MIN_GAP)
        end = max(end, min(last_end, start + 0.2))
        if end <= start:
            end = start + 0.2
        cues.append((start, end, "\n".join(break_lines(wtext(g)) or [wtext(g)])))
    return cues


# ============================================================
# 2) VERTICAL  (Shorts / Reels, 1-3 words)
# ============================================================

def build_vertical(words, max_words: int = 3, upper: bool = True, strip: bool = True):
    if not words:
        return []
    max_words = max(1, int(max_words))

    groups, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        reason = None
        if nxt is None:
            reason = "last"
        elif nxt["start"] - w["end"] >= SHORT_PAUSE:
            reason = "pause"
        elif is_sentence_end(w["word"]) or SOFT_BREAK.search(w["word"]):
            reason = "punct"          # even a single comma starts a new cue
        elif len(cur) >= max_words:
            reason = "size"
        elif len(wtext(cur)) + 1 + len(nxt["word"]) > SHORT_MAX_CHARS:
            reason = "chars"

        if reason is None:
            continue
        # never leave "the / to / and" dangling at the end of a cue
        if (reason in ("size", "chars") and len(cur) > 1
                and cur[-1]["word"].lower().strip(",.;:!?") in WEAK_END):
            carry = cur.pop()
            groups.append(cur)
            cur = [carry]
        else:
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)

    cues = []
    for i, g in enumerate(groups):
        start, last_end = g[0]["start"], g[-1]["end"]
        nxt_start = groups[i + 1][0]["start"] if i + 1 < len(groups) else None

        end = last_end + SHORT_HOLD
        if nxt_start is not None and nxt_start - last_end < 0.30:
            end = nxt_start - SHORT_GAP        # continuous flow, no flicker
        end = max(end, start + SHORT_MIN_DURATION)
        if nxt_start is not None:
            end = min(end, nxt_start - SHORT_GAP)
        if end <= start:
            end = start + 0.1

        toks = [t["word"].rstrip(",;:.…") if strip else t["word"] for t in g]
        text = " ".join(t for t in toks if t)
        cues.append((start, end, text.upper() if upper else text))
    return cues


# ============================================================
# 3) WORD LEVEL
# ============================================================

def style_word(text: str, upper: bool, strip: bool) -> str:
    if strip:
        text = text.rstrip(",;:.…")
    return text.upper() if upper else text


def build_word_level(words, upper: bool = False, strip: bool = False):
    cues = []
    for w in words:
        text = style_word(w["word"], upper, strip)
        if text:
            cues.append((w["start"], w["end"], text))
    return cues


# ============================================================
# PIPELINE  (runs in a worker thread, reports through `emit`)
# ============================================================

PREVIEW_CUES = 400   # preview shows the first N cues; the downloaded file has everything


def read_preview(path: str, n_cues: int) -> str:
    with open(path, encoding="utf-8-sig") as f:
        text = f.read()
    if n_cues <= PREVIEW_CUES:
        return text
    head = "\n\n".join(text.split("\n\n")[:PREVIEW_CUES])
    return head + f"\n\n... preview = first {PREVIEW_CUES} of {n_cues:,} cues. Download the file for all."


from silero_vad import load_silero_vad, get_speech_timestamps
import numpy as np
import torch


@gpu
def vad_detection(wav_file):
    with wave.open(wav_file, "rb") as wf:
        audio = wf.readframes(wf.getnframes())

    audio = np.frombuffer(audio, dtype=np.int16).astype(np.float32)
    audio /= 32768.0

    wav_tensor = torch.from_numpy(audio)

    speech = get_speech_timestamps(
        wav_tensor,
        load_silero_vad(),
        sampling_rate=16000,
        return_seconds=True,
    )

    del wav_tensor
    return speech


def run_pipeline(src, language, out_dir, max_words, upper, strip, emit, stop: threading.Event):
    """
    emit(kind, payload):  ("log", str) | ("bar", str)
    The UI log only gets short summary lines; full detail goes to <name>_log.txt.
    """
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(src))[0]
    p = lambda suffix: os.path.join(out_dir, f"{stem}_{suffix}")
    wav_file = p("16k_mono.wav")
    log_file = p("log.txt")
    open(log_file, "w").close()

    def log(msg, ui=True):
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        if ui:
            emit("log", msg)

    t_total = time.perf_counter()
    log(f"▶ {os.path.basename(src)}  ·  {language}")
    log(f"input: {src} | out dir: {out_dir}", ui=False)

    # ---- 1. audio ----
    t0 = time.perf_counter()
    same = os.path.abspath(src) == os.path.abspath(wav_file)
    if not same and os.path.exists(wav_file) and os.path.getmtime(wav_file) >= os.path.getmtime(src):
        log("cached WAV found, skipping ffmpeg", ui=False)
    elif not same:
        extract_audio(src, wav_file, lambda f: emit("bar", make_bar(f, t0, "extracting audio")), stop)
        if stop.is_set():
            if os.path.exists(wav_file):
                os.remove(wav_file)        # half-written WAV must not be reused
            log("■ stopped", ui=True)
            return None
    info = wav_info(wav_file)
    if (info["rate"], info["channels"], info["width"]) != (16000, 1, 2):
        raise RuntimeError(f"WAV must be 16 kHz / mono / 16-bit, got {info}")
    duration = info["duration"]
    log(f"✔ audio ready  ·  {fmt_dur(duration)}")
    if stop.is_set():
        log("■ stopped", ui=True)
        return None

    # ---- 2. VAD ----
    emit("bar", "loading Silero VAD ...")
    speech = vad_detection(wav_file)
    if stop.is_set():
        log("■ stopped", ui=True)
        return None
    speech_dur = sum(float(s["end"]) - float(s["start"]) for s in speech)
    log(f"✔ voice detection  ·  {fmt_dur(speech_dur)} of speech")

    # ---- 3. chunks ----
    chunks = create_chunks(duration, speech)
    log(f"✔ chunking  ·  {len(chunks):,} chunks (≤ 29 s)")
    log(f"{sum(c[2] == 'VAD' for c in chunks)} VAD cuts, "
        f"{sum(c[2] == 'HARD' for c in chunks)} hard cuts", ui=False)

    # ---- 4. Needle ----
    import needle
    words, errors = [], 0
    t0 = time.perf_counter()
    with tempfile.TemporaryDirectory() as tmp:
        chunk_file = os.path.join(tmp, "chunk.wav")
        for i, (start, end, reason) in enumerate(chunks, 1):
            if stop.is_set():
                log("■ stopped", ui=True)
                return None
            extract_wav_chunk(wav_file, chunk_file, start, end)
            try:
                result = needle.transcribe(chunk_file, language=language, word_timestamps=True)
            except Exception as exc:
                errors += 1
                log(f"chunk {i} failed: {str(exc)[:160]}", ui=False)
                continue
            for w in result.get("words", []):
                try:
                    text = str(w.get("word", "")).strip()
                    ws, we = float(w["start"]) + start, float(w["end"]) + start
                except (KeyError, TypeError, ValueError):
                    continue
                if text and we > ws:
                    words.append({"word": text, "start": ws, "end": we})
            emit("bar", make_bar(end / duration, t0, "transcribing",
                                 f"chunk {i}/{len(chunks)} · {len(words):,} words"))
    words.sort(key=lambda w: (w["start"], w["end"]))
    log(f"✔ transcription  ·  {len(words):,} words" + (f"  ({errors} chunks failed)" if errors else ""))
    if not words:
        raise RuntimeError("Needle returned no words. Check the language and the audio.")

    # ---- 5. subtitles ----
    outputs, counts = {}, {}
    for key, suffix, cues in (
        ("horizontal", "youtube_sentence.srt", build_horizontal(words)),
        ("vertical", "shorts_vertical.srt", build_vertical(words, max_words, upper, strip)),
        ("word", "word_level.srt", build_word_level(words, upper, strip)),
    ):
        path = p(suffix)
        counts[key] = write_srt(path, cues)
        outputs[key] = (path, read_preview(path, counts[key]))
        log(f"saved {path} ({counts[key]:,} cues)", ui=False)
    log(f"✔ subtitles  ·  YouTube {counts['horizontal']:,}  |  "
        f"Shorts {counts['vertical']:,}  |  Words {counts['word']:,}")

    total = time.perf_counter() - t_total
    log(f"★ done in {fmt_dur(total)}  ·  {duration / total:.1f}x realtime")
    log(f"📁 {out_dir}")
    emit("bar", f"[{'█' * 30}] 100.0%  done in {fmt_dur(total)}")
    return outputs


# ============================================================
# GRADIO UI  (clean, dark, modern AI-site look)
# ============================================================

HF_URL = "https://huggingface.co/Cactus-Compute/needle3"
GH_URL = "https://github.com/NeuralFalconYT/needle-subtitle"

GITHUB_SVG = (
    '<svg viewBox="0 0 16 16" width="15" height="15" fill="currentColor" aria-hidden="true">'
    '<path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/></svg>'
)

HEADER = f"""
<div id="hero">
  <h1 class="title">NEEDLE SUBTITLE</h1>
  <p class="tagline">Subtitles for YouTube, Shorts, Instagram &amp; TikTok</p>
  <div class="links">
    <a href="{HF_URL}" target="_blank" rel="noopener">🤗&nbsp; Cactus-Compute/needle3</a>
    <a href="{GH_URL}" target="_blank" rel="noopener">{GITHUB_SVG}&nbsp; NeuralFalconYT/needle-subtitle</a>
  </div>
</div>
"""

CSS = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

:root, .dark, .gradio-container {
  --body-background-fill: transparent;
  --background-fill-primary: #101015;
  --background-fill-secondary: #101015;
  --block-background-fill: transparent;
  --block-border-color: rgba(255,255,255,.08);
  --border-color-primary: rgba(255,255,255,.08);
  --input-background-fill: #0a0a0e;
  --input-border-color: rgba(255,255,255,.10);
  --body-text-color: #ececf1;
  --body-text-color-subdued: #8b8b9a;
  --block-label-text-color: #9a9aab;
  --block-title-text-color: #c9c9d6;
  --color-accent: #8b7cf6;
}
body, .gradio-container {
  font-family: 'Inter', 'Segoe UI', system-ui, sans-serif !important;
  background:
    radial-gradient(ellipse 70% 45% at 50% -8%, rgba(124, 92, 255, .28), transparent 70%),
    radial-gradient(ellipse 40% 30% at 90% 10%, rgba(56, 189, 248, .10), transparent 70%),
    #09090c !important;
  background-attachment: fixed !important;
}
.gradio-container {
    width: 100% !important;
    max-width: 1320px !important;

    margin-left: auto !important;
    margin-right: auto !important;

    padding-left: 24px !important;
    padding-right: 24px !important;

    box-sizing: border-box !important;
}


footer { display: none !important; }

/* ---------- hero ---------- */
#hero { text-align: center; padding: 38px 10px 26px; }
#hero .title {
  margin: 0; font-size: clamp(30px, 5.5vw, 54px); font-weight: 800; letter-spacing: -.5px;
  background: linear-gradient(180deg, #ffffff 25%, #a99bff 100%);
  -webkit-background-clip: text; background-clip: text;
  -webkit-text-fill-color: transparent; color: transparent;
}
#hero .tagline { margin: 10px 0 20px; color: #8b8b9a; font-size: 15px; }
#hero .links { display: flex; gap: 10px; justify-content: center; flex-wrap: wrap; }
#hero .links a {
  display: inline-flex; align-items: center; padding: 7px 16px; border-radius: 999px;
  color: #dcdcea !important; text-decoration: none !important; font-size: 13.5px; font-weight: 500;
  background: rgba(255,255,255,.04); border: 1px solid rgba(255,255,255,.12);
  transition: all .2s ease;
}
#hero .links a:hover { border-color: #8b7cf6; background: rgba(139,124,246,.12); transform: translateY(-1px); }

/* ---------- cards ---------- */
.card {
  background: rgba(255,255,255,.025) !important;
  border: 1px solid rgba(255,255,255,.08) !important; border-radius: 18px !important;
  padding: 18px !important;
  box-shadow: 0 10px 40px rgba(0,0,0,.35);
}
/* dropdown list must sit right under the field and above other content */
ul.options { z-index: 9999 !important; }
.card h3 { color: #c9c9d6 !important; font-size: 12px !important; font-weight: 600 !important;
           letter-spacing: 2px; text-transform: uppercase; margin-bottom: 4px; }
.block { border-radius: 12px !important; }

/* ---------- inputs ---------- */
textarea, input[type=text] { color: #ececf1 !important; }
textarea::placeholder, input::placeholder { color: #5d5d6c !important; }
textarea:focus, input:focus { border-color: #8b7cf6 !important; box-shadow: 0 0 0 3px rgba(139,124,246,.18) !important; }
#status textarea, #log textarea, .srt textarea {
  font-family: 'JetBrains Mono', 'Consolas', 'Courier New', monospace !important; font-size: 12.5px !important;
  background: #0a0a0e !important;
}
#status textarea { color: #b9aeff !important; }
#log textarea { color: #aab4c4 !important; }

/* previews: every tab scrolls the same way */
.srt textarea {
  color: #d4d4e0 !important; height: 330px !important; max-height: 330px !important;
  overflow-y: auto !important; resize: vertical;
}

/* ---------- buttons ---------- */
#go {
  background: linear-gradient(135deg, #8b7cf6, #6366f1) !important; color: #fff !important;
  border: none !important; font-weight: 600 !important; font-size: 15px !important;
  border-radius: 12px !important; transition: all .2s ease;
  box-shadow: 0 6px 24px rgba(99,102,241,.40);
}
#go:hover { transform: translateY(-1px); box-shadow: 0 10px 30px rgba(99,102,241,.55); filter: brightness(1.08); }
#stop {
  background: rgba(255,255,255,.04) !important; color: #e4e4ee !important;
  border: 1px solid rgba(255,255,255,.14) !important; border-radius: 12px !important; font-weight: 500 !important;
}
#stop:hover { border-color: #f87171 !important; color: #fca5a5 !important; background: rgba(248,113,113,.08) !important; }

/* ---------- tabs ---------- */
button[role=tab] { color: #8b8b9a !important; font-weight: 500; }
button[role=tab][aria-selected=true] { color: #fff !important; border-color: #8b7cf6 !important; }
"""

# one run at a time: the Stop button sets this flag, the worker checks it between steps
RUN = {"stop": threading.Event()}


def request_stop():
    RUN["stop"].set()
    return "■ stopping ... (finishing the current step)"


def build_ui():
    import gradio as gr

    def handler(file_path, path_text, language_name, max_words, upper, strip):
        def pack(status=None, log=None, files=(None, None, None), texts=(None, None, None),
                 idle=None):
            """idle=True re-enables the Generate button, None leaves it unchanged."""
            u = gr.update
            return (
                u() if status is None else status,
                u() if log is None else log,
                *[u() if f is None else f for f in files],
                *[u() if t is None else t for t in texts],
                u() if idle is None else u(interactive=idle),
            )

        # ---- resolve input ----
        # The path box is only honoured locally; on Spaces it is ignored
        # (also prevents reading arbitrary server files via a crafted path).
        if IS_LOCAL and path_text and path_text.strip():
            src = clean_path(path_text)
            out_dir = os.path.dirname(src)
        elif file_path:
            src = file_path
            stem = os.path.splitext(os.path.basename(src))[0]
            out_dir = os.path.abspath(os.path.join("needle_output", stem))
        else:
            yield pack("drop a file first" if ON_SPACES
                       else "paste a path or drop a file first", idle=True)
            return
        if not os.path.isfile(src):
            yield pack(f"file not found: {src}", idle=True)
            return

        q: queue.Queue = queue.Queue()
        stop = threading.Event()
        RUN["stop"] = stop
        language = LANGUAGES.get(language_name, "en")
        box = {}

        def worker():
            try:
                box["out"] = run_pipeline(src, language, out_dir, max_words, upper, strip,
                                          lambda k, v: q.put((k, v)), stop)
            except Exception as exc:
                box["err"] = f"{type(exc).__name__}: {exc}"
                q.put(("log", "✘ " + box["err"]))
                print(traceback.format_exc())
            finally:
                q.put(("end", None))

        threading.Thread(target=worker, daemon=True).start()

        lines, status, finished = [], "starting ...", False
        try:
            while not finished:
                try:
                    items = [q.get(timeout=0.4)]
                except queue.Empty:
                    continue
                while True:
                    try:
                        items.append(q.get_nowait())
                    except queue.Empty:
                        break
                for kind, payload in items:
                    if kind == "log":
                        lines.append(payload)
                        print(payload)
                    elif kind == "bar":
                        status = payload
                    elif kind == "end":
                        finished = True
                if not finished:
                    prefix = "■ stopping ...  " if stop.is_set() else ""
                    yield pack(prefix + status, "\n".join(lines))
        finally:
            stop.set()        # browser closed / generator closed -> worker exits too

        out = box.get("out")
        if box.get("err"):
            yield pack("failed", "\n".join(lines), idle=True)
        elif out is None:
            yield pack("stopped", "\n".join(lines), idle=True)
        else:
            yield pack(
                "done ✔",
                "\n".join(lines),
                files=(out["horizontal"][0], out["vertical"][0], out["word"][0]),
                texts=(out["horizontal"][1], out["vertical"][1], out["word"][1]),
                idle=True,
            )

    blocks_kwargs = {"title": "Needle Subtitle"}
    launch_kwargs = {}
    theme = gr.themes.Base(primary_hue="violet", secondary_hue="indigo", neutral_hue="zinc")
    if "css" in inspect.signature(gr.Blocks.__init__).parameters:
        blocks_kwargs.update(css=CSS, theme=theme)
    else:
        launch_kwargs.update(css=CSS, theme=theme)

    def preview_box():
        return gr.Textbox(label="preview", lines=12, max_lines=12, interactive=False,
                          autoscroll=False, elem_classes="srt")

    with gr.Blocks(**blocks_kwargs) as demo:
        gr.HTML(HEADER)
        with gr.Row():
            # ---------------- LEFT ----------------
            with gr.Column(scale=1, min_width=340, elem_classes="card"):
                gr.Markdown("### Input")
                # Local-only: hidden on Hugging Face Spaces
                path_in = gr.Textbox(
                    label="Paste file path  (local only)",
                    info="Works only when this app runs on your own computer.",
                    placeholder='C:\\videos\\long.mp4   (quotes or r"" are fine)',
                    lines=1,
                    visible=IS_LOCAL)
                file_in = gr.File(
                    label=("drag & drop video / audio" if ON_SPACES
                           else "...or drag & drop video / audio"),
                    type="filepath")
                lang = gr.Dropdown(list(LANGUAGES), value="English", label="Language")
                with gr.Accordion("Shorts & word-level options", open=False):
                    words_per_cue = gr.Slider(1, 3, value=3, step=1,
                                              label="Max words per cue (Shorts)")
                    upper = gr.Checkbox(value=True, label="UPPERCASE  (Shorts + word level)")
                    strip = gr.Checkbox(value=True,
                                        label="Remove trailing , . ; :  (Shorts + word level, keeps ? !)")
                go = gr.Button("Generate subtitles", variant="primary", elem_id="go")
                stop_btn = gr.Button("Stop", elem_id="stop")

            # ---------------- RIGHT ----------------
            with gr.Column(scale=2, elem_classes="card"):
                gr.Markdown("### Output")
                status = gr.Textbox(label="status", interactive=False, lines=1, elem_id="status")
                logbox = gr.Textbox(label="summary", interactive=False, lines=6, max_lines=6,
                                    autoscroll=True, elem_id="log")
                with gr.Tabs():
                    with gr.Tab("1 · YouTube / horizontal"):
                        f1 = gr.File(label="sentence-level SRT")
                        t1 = preview_box()
                    with gr.Tab("2 · Shorts / Reels vertical"):
                        f2 = gr.File(label="1-3 word SRT")
                        t2 = preview_box()
                    with gr.Tab("3 · Word level"):
                        f3 = gr.File(label="word-level SRT")
                        t3 = preview_box()

        def clear_outputs():
            """Runs instantly on click: wipe old results so it is obvious a new run started."""
            u = gr.update
            return ("starting ...", "", u(value=None), u(value=None), u(value=None),
                    "", "", "", u(interactive=False))

        all_outputs = [status, logbox, f1, f2, f3, t1, t2, t3, go]
        go.click(clear_outputs, inputs=None, outputs=all_outputs, queue=False).then(
            handler,
            inputs=[file_in, path_in, lang, words_per_cue, upper, strip],
            outputs=all_outputs,
        )
        # Stop is its own event (not `cancels=`): it sets a flag the worker checks
        stop_btn.click(request_stop, inputs=None, outputs=[status], queue=False)

    return demo, launch_kwargs


if __name__ == "__main__":
    if not shutil.which("ffmpeg"):
        print("[!] ffmpeg not found on PATH - audio extraction will fail.")
    demo, launch_kwargs = build_ui()
    demo.queue().launch(inbrowser=IS_LOCAL, **launch_kwargs)
