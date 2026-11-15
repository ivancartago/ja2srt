"""Japanese audio -> English .srt.

Two translation modes:
  offline : Whisper's built-in speech translation, runs entirely on this computer (free, rougher English)
  claude  : Japanese is transcribed locally, then the Claude API decides the wording and writes the English

Command line:  python pipeline.py audio.mp3 [--mode offline|claude] [--api-key KEY] [--model NAME] [--no-check]
"""
import argparse, difflib, json, os, re, subprocess, sys, tempfile, textwrap, time, urllib.error, urllib.request
from pathlib import Path

import numpy as np
import soundfile as sf

import fixes

ROOT = Path(__file__).resolve().parent
MODELS = ROOT / "models"
CFG = json.loads((ROOT / "models.json").read_text(encoding="utf-8"))   # which speech models to use
SR = 16000
THREADS = max(1, min(os.cpu_count() or 2, 8))
DEFAULT_CLAUDE_MODEL = "claude-sonnet-5-5"
API_VERSION = "2026-10-21"

# Text Whisper tends to invent on silence, tones or music. Segments that are only this are dropped.
JA_NOISE = {"", "ん", "うん", "あ", "お", "はい", "音楽", "拍手", "笑", "ご視聴ありがとうございました", "ご視聴ありがとうございました。",
            "おやすみなさい", "お疲れ様でした", "ありがとうございました", "チャンネル登録お願いします"}
EN_NOISE = re.compile(r"^\W*(thank(s| you)( you)?( so much| very much)?( for (watching|listening))?|bye(-| )?bye|bye|"
                      r"please subscribe.*|subtitles by.*|\[?(music|applause|laughter)\]?|you|oh|ah|hmm+|okay|ok|yes|yeah)?\W*$", re.I)


# ---------------------------------------------------------------- audio

def load_audio(path):
    """Decode any audio/video file to 16 kHz mono float32 using ffmpeg."""
    try:
        import imageio_ffmpeg
        ff = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        ff = "ffmpeg"
    with tempfile.TemporaryDirectory() as d:
        wav = os.path.join(d, "a.wav")
        r = subprocess.run([ff, "-nostdin", "-loglevel", "error", "-y", "-i", str(path), "-vn", "-ac", "1", "-ar", str(SR), wav],
                           capture_output=True, text=True)
        if r.returncode != 0 or not os.path.exists(wav):
            raise RuntimeError("Could not read this file as audio. " + (r.stderr.strip()[-300:] or ""))
        audio, sr = sf.read(wav, dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if len(audio) < SR:
        raise RuntimeError("The audio is shorter than one second.")
    return audio


# ---------------------------------------------------------------- models

_cache = {}

def _need(*paths):
    missing = [str(p) for p in paths if not Path(p).exists()]
    if missing:
        raise RuntimeError("Speech models are missing (" + Path(missing[0]).parent.name + "). Run:  python setup_models.py")

def _whisper(name, task):
    import sherpa_onnx
    key = (name, task)
    if key not in _cache:
        d = MODELS / f"sherpa-onnx-whisper-{name}"
        enc, dec, tok = d / f"{name}-encoder.int8.onnx", d / f"{name}-decoder.int8.onnx", d / f"{name}-tokens.txt"
        _need(enc, dec, tok)
        _cache[key] = sherpa_onnx.OfflineRecognizer.from_whisper(
            encoder=str(enc), decoder=str(dec), tokens=str(tok), language="ja", task=task, num_threads=THREADS)
    return _cache[key]

def _zipformer():
    import sherpa_onnx
    if "zip" not in _cache:
        al = CFG["aligner"]; d = MODELS / al["dir"]
        f = [d / al["encoder"], d / al["decoder"], d / al["joiner"], d / al["tokens"]]
        _need(*f)
        _cache["zip"] = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=str(f[0]), decoder=str(f[1]), joiner=str(f[2]), tokens=str(f[3]), num_threads=THREADS)
    return _cache["zip"]

def offline_model_name():
    """Largest offline translation model that has been downloaded."""
    for n in CFG["offline_whisper_choices"]:
        if n != CFG["transcribe_whisper"] and (MODELS / f"sherpa-onnx-whisper-{n}" / f"{n}-encoder.int8.onnx").exists():
            return n
    return None

def _decode(rec, audio, a, b):
    st = rec.create_stream()
    st.accept_waveform(SR, audio[max(0, int(a * SR)):int(b * SR)])
    rec.decode_stream(st)
    return st.result


# ---------------------------------------------------------------- step 1: find speech

def find_speech(audio, max_len):
    """Voice activity detection -> list of (start_sec, end_sec)."""
    import sherpa_onnx
    _need(MODELS / CFG["vad"])
    cfg = sherpa_onnx.VadModelConfig()
    cfg.silero_vad.model = str(MODELS / CFG["vad"])
    cfg.silero_vad.threshold = 0.4
    cfg.silero_vad.min_silence_duration = 0.3
    cfg.silero_vad.min_speech_duration = 0.15
    cfg.silero_vad.max_speech_duration = max_len
    cfg.sample_rate = SR
    vad = sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=120)
    w = cfg.silero_vad.window_size
    out = []
    def drain():
        while not vad.empty():
            s = vad.front
            out.append((s.start / SR, s.start / SR + len(s.samples) / SR))
            vad.pop()
    for i in range(0, len(audio) - w + 1, w):
        vad.accept_waveform(audio[i:i + w]); drain()
    vad.flush(); drain()
    return out


# ---------------------------------------------------------------- text helpers

_kk = None
_D = "〇一二三四五六七八九"

def _num(m):
    n = int(m.group())
    if n == 0:
        return "零"
    if n >= 100000000:
        return m.group()
    s = ""
    for u, nm in ((10000, "万"), (1000, "千"), (100, "百"), (10, "十")):
        q, n = divmod(n, u)
        if q:
            s += ("" if q == 1 and u < 10000 else (_num(re.match(r"\d+", str(q))) if q > 9 else _D[q])) + nm
    return s + (_D[n] if n else "")

def hira(s):
    """Normalise Japanese text to hiragana so different spellings of the same words can be compared."""
    global _kk
    if _kk is None:
        import pykakasi
        _kk = pykakasi.kakasi()
    s = s.translate(str.maketrans("0123456789", "0123456789"))
    s = re.sub(r"\d+", _num, s)
    s = re.sub(r"[、。!?!?⁉…・\s「」『』ー〜,.\-\"'()()]", "", s)
    return "".join(x["hira"] for x in _kk.convert(s))

def wrap(en):
    if " / " in en:
        return "\n".join("- " + p.strip() for p in en.split(" / ")[:2])
    if len(en) <= 42:
        return en
    words, best = en.split(), None
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        if len(a) <= 46 and len(b) <= 46 and (best is None or abs(len(a) - len(b)) < best[0]):
            best = (abs(len(a) - len(b)), a + "\n" + b)
    if best:
        return best[1]
    return "\n".join(textwrap.wrap(en, max(46, (len(en) + 1) // 2 + 6))[:2])

def split_english(en, limit=84):
    """Split an over-long subtitle into pieces at sentence or clause boundaries."""
    if len(en) <= limit:
        return [en]
    n = -(-len(en) // limit)                                    # number of pieces, each about the same length
    target = len(en) / n
    parts = re.split(r"(?<=[.!?])\s+|(?<=[,;:])\s+", en)
    out, cur = [], ""
    for p in parts:
        if cur and len(out) < n - 1 and len(cur) + len(p) / 2 > target:
            out.append(cur); cur = p
        else:
            cur = (cur + " " + p).strip()
    if cur:
        out.append(cur)
    final = []
    for o in out:
        if len(o) > limit:
            k = -(-len(o) // limit)
            final.extend(textwrap.wrap(o, len(o) // k + 10))
        else:
            final.append(o)
    return final

def collapse_repeats(en):
    """Whisper sometimes loops ('and the x, and the x, ...'). Keep one copy of a phrase repeated 3+ times."""
    for _ in range(3):
        en = re.sub(r"(\b[^,.!?]{2,40}?[,.!?]?\s+)\1{2,}", r"\1", en)
        en = re.sub(r"(\b\w+\b)(\s+\1\b){3,}", r"\1", en)
    return en.strip()

def stamp(t):
    ms = max(0, int(round(t * 1000)))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


# ---------------------------------------------------------------- timing

def make_srt(cues):
    """cues: list of dicts with start, end (speech times) and en. Returns (srt_text, display_list)."""
    cues = sorted(cues, key=lambda c: c["start"])
    out = []
    for i, c in enumerate(cues):
        st = c["start"] - 0.10                                  # appear just before the line is spoken
        if out:
            st = max(st, out[-1][1] + 0.04)
        nxt = cues[i + 1]["start"] - 0.10 if i + 1 < len(cues) else 1e9
        want = max(c["end"] + 0.40, st + max(1.3, len(c["en"]) / 17.0))   # linger a little, and long enough to read
        en_t = min(want, c["end"] + 2.0, nxt - 0.04)
        en_t = max(en_t, min(c["end"], nxt - 0.04), st + 0.3)
        out.append([max(0.0, st), en_t, c["en"]])
    text = "".join(f"{i}\n{stamp(a)} --> {stamp(b)}\n{wrap(en)}\n\n" for i, (a, b, en) in enumerate(out, 1))
    return text, out


class Aligner:
    """Splits one stretch of speech into several cues using character timings from a second recogniser."""

    def __init__(self, audio, segs, progress):
        fr = int(0.02 * SR); n = len(audio) // fr
        rms = np.sqrt((audio[:n * fr].reshape(n, fr) ** 2).mean(1))
        self.rms = np.convolve(rms, np.ones(5) / 5, "same")
        rec = _zipformer()
        wins = []
        for a, b in segs:                                       # merge neighbouring segments into windows of <= 22 s
            if wins and a - wins[-1][1] < 2.0 and b - wins[-1][0] <= 22:
                wins[-1][1] = b
            else:
                wins.append([a, b])
        toks = set()
        self.window_text = []
        for i, (a, b) in enumerate(wins):
            a0 = max(0, a - 0.3)
            r = _decode(rec, audio, a0, b + 0.3)
            for t, tm in zip(r.tokens, r.timestamps):
                toks.add((round(a0 + tm, 3), t))
            self.window_text.append((a0, b + 0.3, r.text.strip()))
            progress(i + 1, len(wins))
        self.toks = sorted(toks)
        self.wins = wins

    def snap(self, t, w):
        a = max(0, int((t - w) / 0.02)); b = min(len(self.rms) - 1, int((t + w) / 0.02))
        if b <= a:
            return t
        return (a + int(np.argmin(self.rms[a:b + 1]))) * 0.02 + 0.01

    def text_between(self, a, b):
        return "".join(t for tm, t in self.toks if a - 0.2 <= tm <= b + 0.2)

    def split(self, S, E, ja_pieces):
        """Return boundary times [S, t1, ..., E] for the given Japanese pieces spoken between S and E."""
        hs = [hira(j) or "あ" for j in ja_pieces]
        full = "".join(hs)
        Z, ZT = [], []
        for tm, t in self.toks:
            if S - 0.4 <= tm <= E + 0.4:
                for ch in hira(t):
                    Z.append(ch); ZT.append(tm)
        mp = {}
        for a, b, k in difflib.SequenceMatcher(None, full, "".join(Z), autojunk=False).get_matching_blocks():
            for i in range(k):
                mp[a + i] = ZT[b + i]
        bounds, pos = [S], 0
        for h in hs[:-1]:
            pos += len(h)
            prop = S + (E - S) * pos / len(full)
            nxt = next((mp[p] for p in range(pos, min(pos + 4, len(full))) if p in mp), None)
            prv = next((mp[p] for p in range(pos - 1, max(pos - 5, -1), -1) if p in mp), None)
            if nxt is not None and prv is not None and nxt - prv < 2.5:
                t = self.snap(max(prv + 0.04, nxt - 0.14), 0.12)
            elif nxt is not None:
                t = self.snap(nxt - 0.14, 0.12)
            elif prv is not None and abs(prv + 0.3 - prop) < 2.5:
                t = self.snap(prv + 0.3, 0.2)
            else:
                t = self.snap(prop, 0.3)
            bounds.append(round(min(max(t, bounds[-1] + 0.5), E - 0.3), 2))
        bounds.append(E)
        if any(b2 - b1 < 0.3 for b1, b2 in zip(bounds, bounds[1:])):     # not enough room: share the time evenly by length
            bounds, pos = [S], 0
            for h in hs[:-1]:
                pos += len(h); bounds.append(round(S + (E - S) * pos / len(full), 2))
            bounds.append(E)
        return bounds


# ---------------------------------------------------------------- mode A: offline

def run_offline(audio, progress):
    name = offline_model_name()
    if not name:
        raise RuntimeError("The offline translation model is not installed. Run:  python setup_models.py --offline-model medium")
    progress("Finding speech", 0.02)
    segs = find_speech(audio, max_len=6.0)
    ja_rec, en_rec = _whisper(CFG["transcribe_whisper"], "transcribe"), _whisper(name, "translate")
    cues = []
    for i, (a, b) in enumerate(segs):
        progress(f"Translating speech ({i + 1}/{len(segs)})", 0.05 + 0.93 * i / max(1, len(segs)))
        ja = _decode(ja_rec, audio, a, b).text.strip()
        if ja.strip("。、!? ") in JA_NOISE or len(hira(ja)) < 3 or b - a < 0.25:
            continue
        en = _decode(en_rec, audio, a - 0.15, b + 0.15).text.strip()
        en = collapse_repeats(re.sub(r"\s+", " ", en) + " ")
        if EN_NOISE.match(en) and len(hira(ja)) < 6:
            continue
        if not en or re.search(r"[぀-ヿ一-鿿]", en):     # model answered in Japanese: skip rather than show it
            continue
        pieces = split_english(en)
        total = sum(len(p) for p in pieces)
        t = a
        for p in pieces:
            d = (b - a) * len(p) / total
            cues.append({"start": round(t, 2), "end": round(t + d, 2), "ja": ja, "en": p}); t += d
    notes = [f"Translated offline with Whisper {name}. Expect rougher English and less reliable names than Claude mode."]
    return cues, notes, []


# ---------------------------------------------------------------- mode B: Claude API

PROMPT = """You are preparing English subtitles for a Japanese recording. I will give you speech segments in order. Each has an id, its start and end time in seconds, and two or three automatic transcripts of the same audio made by different speech recognisers. The recognisers make different mistakes, so compare them to work out what was actually said.

For each segment return its subtitle cues:
- "ja": the Japanese actually spoken in that cue, as best you can tell (plain text, no speaker labels).
- "en": natural, concise English for that cue, at most 84 characters. If two speakers talk in one cue, separate them with " / ".
- A short segment is one cue. Split a long segment into several cues at natural phrase boundaries, in spoken order, so that each cue is roughly 2 to 6 seconds of speech. The "ja" pieces of a segment, joined together, must cover everything said in it.
- When a sentence runs across cues or segments, word the English so each cue matches what is being said at that moment and the sentence still reads naturally across them.
- Return an empty list of cues for a segment that is not real speech: recogniser inventions on silence or music (for example a lone "ん", "はい", or "ご視聴ありがとうございました" where nothing fits), or noise that cannot be understood. Do not guess at unintelligible speech.
- Keep names consistent. Use the name spellings already decided (given below) and add any new names you settle on to "names".
- Use given-name-then-family-name order, "Mr./Ms." for さん in formal speech, double quotes for titles.

Reply with JSON only, no commentary, in exactly this shape:
{"segments": [{"id": 0, "cues": [{"ja": "...", "en": "..."}]}], "names": {"日本語の名前": "English spelling"}}
"""

def call_claude(api_key, model, user_text, max_tokens=16000):
    body = json.dumps({"model": model, "max_tokens": max_tokens,
                       "messages": [{"role": "user", "content": PROMPT + "\n\n" + user_text}]}).encode()
    last = ""
    for attempt in range(4):
        req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body, method="POST", headers={
            "x-api-key": api_key, "anthropic-version": API_VERSION, "content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                data = json.loads(r.read().decode())
            return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:400]
            try:
                detail = json.loads(detail)["error"]["message"]
            except Exception:
                pass
            last = f"Claude API error {e.code}: {detail}"
            if e.code in (401, 403):
                raise RuntimeError("The Claude API key was rejected. Check the key and try again.")
            if e.code in (400, 404):
                raise RuntimeError(last + f"  (model name used: {model})")
            time.sleep(5 * (attempt + 1))                        # rate limit or server busy: wait and retry
        except (urllib.error.URLError, TimeoutError) as e:
            last = f"Could not reach the Claude API: {e}"
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(last)

def parse_reply(text):
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b <= a:
        raise ValueError("no JSON in reply")
    data = json.loads(text[a:b + 1])
    out = {}
    for s in data.get("segments", []):
        cues = [{"ja": str(c.get("ja", "")).strip(), "en": re.sub(r"\s+", " ", str(c.get("en", ""))).strip()}
                for c in s.get("cues", []) if str(c.get("en", "")).strip()]
        out[int(s["id"])] = cues
    return out, {str(k): str(v) for k, v in (data.get("names") or {}).items()}

def run_claude(audio, progress, api_key, model, ask=None, check=True):
    """`ask(user_text) -> reply text` can be supplied instead of an API key (used for testing)."""
    ask = ask or (lambda t: call_claude(api_key, model, t))
    progress("Finding speech", 0.02)
    segs = find_speech(audio, max_len=8.0)
    merged = []
    for a, b in segs:                                            # rejoin pieces that were only cut by the length cap
        if merged and a - merged[-1][1] < 0.1 and b - merged[-1][0] <= 16:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    segs = [tuple(m) for m in merged]
    turbo = _whisper(CFG["transcribe_whisper"], "transcribe")
    first = []
    for i, (a, b) in enumerate(segs):
        progress(f"Transcribing Japanese ({i + 1}/{len(segs)})", 0.04 + 0.26 * i / max(1, len(segs)))
        first.append(_decode(turbo, audio, a, b).text.strip())
    al = Aligner(audio, segs, lambda i, n: progress(f"Timing characters ({i}/{n})", 0.30 + 0.15 * i / n))
    context = []
    for i, (a, b) in enumerate(al.wins):                          # third reading, with longer context
        progress(f"Second listen ({i + 1}/{len(al.wins)})", 0.45 + 0.15 * i / len(al.wins))
        context.append((a, b, _decode(turbo, audio, a - 0.3, b + 0.3).text.strip()))

    items = []
    for i, (a, b) in enumerate(segs):
        z = al.text_between(a, b)
        if first[i].strip("。、!? ") in JA_NOISE and len(hira(z)) < 3:
            continue                                             # obvious non-speech: not worth sending
        items.append({"id": i, "start": round(a, 2), "end": round(b, 2), "a": first[i], "b": z})
    names, result, notes, CH = {}, {}, [], 30
    saved = fixes.load_words()
    for c0 in range(0, len(items), CH):
        chunk = items[c0:c0 + CH]
        progress(f"Translating with Claude ({min(c0 + CH, len(items))}/{len(items)} segments)", 0.60 + 0.25 * c0 / max(1, len(items)))
        lo, hi = chunk[0]["start"], chunk[-1]["end"]
        ctx = "\n".join(f"[{a:.1f}-{b:.1f}] {t}" for a, b, t in context if b >= lo and a <= hi and t)
        prev = [c["en"] for i in sorted(result) for c in result[i]][-6:]
        user = ("Name spellings already decided: " + (json.dumps(names, ensure_ascii=False) if names else "none yet") + "\n\n"
                + (("Wording the user has chosen (always write the form on the right instead of the one on the left): "
                    + "; ".join(f'"{w["wrong"]}" -> "{w["right"]}"' for w in saved) + "\n\n") if saved else "")
                + ("The previous subtitles ended with:\n" + "\n".join(prev) + "\n\n" if prev else "")
                + "Longer-context transcript of this stretch (third recogniser pass, times in seconds):\n" + ctx + "\n\n"
                + "Segments (\"a\" and \"b\" are the two per-segment transcripts):\n"
                + "\n".join(json.dumps(it, ensure_ascii=False) for it in chunk))
        got = None
        for attempt in range(2):
            try:
                got, new_names = parse_reply(ask(user)); break
            except (ValueError, KeyError, json.JSONDecodeError):
                got = None
        if got is None:
            raise RuntimeError("Claude's reply could not be read as JSON twice in a row. Try again, or try another model.")
        names.update(new_names)
        missing = [it["id"] for it in chunk if it["id"] not in got]
        if missing:
            notes.append(f"{len(missing)} speech segment(s) between {stamp(lo)[:8]} and {stamp(hi)[:8]} came back without a translation.")
        result.update({k: v for k, v in got.items() if 0 <= k < len(segs)})

    cues = []
    for i in sorted(result):
        a, b = segs[i]
        pieces = result[i]
        if not pieces:
            continue
        if len(pieces) > 1 and (b - a) / len(pieces) < 0.7:      # too many pieces for the time available: merge
            pieces = [{"ja": "".join(p["ja"] for p in pieces), "en": " ".join(p["en"] for p in pieces)}]
        bounds = [a, b] if len(pieces) == 1 else al.split(a, b, [p["ja"] for p in pieces])
        for p, s, e in zip(pieces, bounds, bounds[1:]):
            for q in split_english(p["en"]) if len(p["en"]) > 90 else [p["en"]]:
                cues.append({"start": round(s, 2), "end": round(e, 2), "ja": p["ja"], "en": q, "multi": len(pieces) > 1})
    # an over-long English line that was split shares one time slot: divide it
    fixed, i = [], 0
    while i < len(cues):
        j = i
        while j + 1 < len(cues) and cues[j + 1]["start"] == cues[i]["start"] and cues[j + 1]["end"] == cues[i]["end"]:
            j += 1
        grp = cues[i:j + 1]; tot = sum(len(c["en"]) for c in grp); t = grp[0]["start"]; span = grp[0]["end"] - grp[0]["start"]
        for c in grp:
            d = span * len(c["en"]) / tot
            fixed.append(dict(c, start=round(t, 2), end=round(t + d, 2))); t += d
        i = j + 1
    cues = fixed

    flags = []
    if check:
        for i, c in enumerate(cues):
            progress(f"Checking sync ({i + 1}/{len(cues)})", 0.86 + 0.13 * i / max(1, len(cues)))
            want = hira(c["ja"])
            if len(want) < 4:
                continue
            heard = hira(_decode(turbo, audio, c["start"] - 0.2, c["end"] + 0.2).text)
            cov = sum(k for _, _, k in difflib.SequenceMatcher(None, want, heard, autojunk=False).get_matching_blocks()) / len(want)
            if cov < 0.45:
                flags.append({"time": stamp(c["start"] - 0.1)[:8], "en": c["en"], "match": round(cov, 2)})
        notes.append(f"Sync check: re-listened to {len(cues)} cues; {len(flags)} could not be confirmed in their time slot "
                     "(often just a misheard word, sometimes a subtitle that is a second or two off).")
    else:
        notes.append("Sync check was skipped.")
    if names:
        notes.append("Name spellings were decided by ear and may differ from the official ones: " + ", ".join(sorted(set(names.values()))) + ".")
    return cues, notes, flags


# ---------------------------------------------------------------- entry point

def run(path, mode="offline", api_key="", model="", check=True, progress=None, ask=None):
    """Returns {"srt": text, "cues": n, "notes": [...], "flags": [...], "duration": seconds}."""
    progress = progress or (lambda msg, frac: None)
    progress("Reading audio", 0.0)
    audio = load_audio(path)
    if mode == "claude":
        if not (api_key or ask):
            raise RuntimeError("Claude mode needs an Anthropic API key.")
        cues, notes, flags = run_claude(audio, progress, api_key, model or DEFAULT_CLAUDE_MODEL, ask=ask, check=check)
    else:
        cues, notes, flags = run_offline(audio, progress)
    if not cues:
        raise RuntimeError("No speech was found in this file.")
    words = fixes.load_words()                                   # the user's remembered corrections
    if words:
        rep, n = fixes.make_replacer(words), 0
        for c in cues:
            c["en"], k = rep(c["en"]); n += sum(k.values())
        notes.append(f"Your saved word list ({len(words)} entries) was applied: {n} replacement(s).")
    srt, shown = make_srt(cues)
    notes.append("Timing was set from the audio only. If your video has a different intro length, use your player's subtitle delay.")
    progress("Done", 1.0)
    return {"srt": srt, "cues": len(shown), "notes": notes, "flags": flags, "duration": len(audio) / SR}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Japanese audio -> English .srt")
    ap.add_argument("audio")
    ap.add_argument("--mode", choices=["offline", "claude"], default="offline")
    ap.add_argument("--api-key", default=os.environ.get("ANTHROPIC_API_KEY", ""))
    ap.add_argument("--model", default=DEFAULT_CLAUDE_MODEL)
    ap.add_argument("--no-check", action="store_true", help="skip the sync check (Claude mode)")
    a = ap.parse_args()
    def show(msg, frac):
        sys.stderr.write(f"\r{int(frac * 100):3d}%  {msg:<60}"); sys.stderr.flush()
    res = run(a.audio, a.mode, a.api_key, a.model, not a.no_check, show)
    out = Path(a.audio).with_suffix(".srt")
    out.write_text(res["srt"], encoding="utf-8")
    print(f"\nWrote {out} ({res['cues']} subtitles)")
    for n in res["notes"]:
        print(" -", n)
    for f in res["flags"]:
        print(f"   check {f['time']}  {f['en']}")
