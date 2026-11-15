"""Download the speech models listed in models.json (run once, and again after an update).

python setup_models.py [--offline-model small|medium|large-v3|none] [--prune]
"""
import argparse, json, shutil, sys, tarfile, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MODELS = ROOT / "models"
CFG = json.loads((ROOT / "models.json").read_text(encoding="utf-8"))
BASE = CFG["base_url"]


def fetch(url, dest, tries=6):
    """Download with progress; resumes where it left off if the connection drops."""
    dest = Path(dest); part = dest.with_name(dest.name + ".part")
    for attempt in range(tries):
        have = part.stat().st_size if part.exists() else 0
        req = urllib.request.Request(url, headers={"Range": f"bytes={have}-"} if have else {})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                if have and r.status != 206:      # server ignored the resume request: start again
                    have = 0
                total = have + int(r.headers.get("Content-Length") or 0)
                last = -10
                with open(part, "ab" if have else "wb") as f:
                    while True:
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk); have += len(chunk)
                        pct = have * 100 // total if total else 0
                        if pct >= last + 10:
                            last = pct; print(f"  {pct:3d}% of {total / 1e6:.0f} MB", flush=True)
            if not total or have >= total:
                part.replace(dest); return
        except Exception as e:
            print(f"  connection problem ({type(e).__name__}); retrying ({attempt + 1}/{tries})", flush=True)
    raise SystemExit(f"Could not download {url}. Check your connection and run this again.")


def get_tar(name, keep=None):
    """Download and unpack <name>.tar.bz2; keep only files whose names contain one of `keep`."""
    d = MODELS / name
    if d.exists() and any(d.iterdir()):
        print(f"- {name}: already installed"); return False
    print(f"- {name}: downloading")
    tmp = MODELS / (name + ".tar.bz2"); fetch(BASE + name + ".tar.bz2", tmp)
    print("  unpacking")
    with tarfile.open(tmp) as t:
        t.extractall(MODELS, filter="data") if sys.version_info >= (3, 12) else t.extractall(MODELS)
    tmp.unlink()
    shutil.rmtree(d / "test_wavs", ignore_errors=True)
    if keep:
        for f in d.iterdir():
            if f.is_file() and not any(k in f.name for k in keep): f.unlink()
    return True


def installed_offline():
    for n in CFG["offline_whisper_choices"]:
        if (MODELS / f"sherpa-onnx-whisper-{n}").is_dir() and n != CFG["transcribe_whisper"]:
            return n
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline-model", default=None, choices=CFG["offline_whisper_choices"] + ["none"],
                    help="Whisper model for the free offline translation (bigger = better and slower). 'none' skips it "
                         "if you will only use Claude mode. Default: keep the one already installed, otherwise "
                         + CFG["offline_whisper_default"] + ".")
    ap.add_argument("--prune", action="store_true", help="remove downloaded models that models.json no longer lists")
    a = ap.parse_args()
    offline = a.offline_model or installed_offline() or CFG["offline_whisper_default"]
    MODELS.mkdir(exist_ok=True)
    vad = MODELS / CFG["vad"]
    if not vad.exists():
        print(f"- {CFG['vad']}: downloading"); fetch(BASE + CFG["vad"], vad)
    wanted = {"sherpa-onnx-whisper-" + CFG["transcribe_whisper"], CFG["aligner"]["dir"]}
    get_tar("sherpa-onnx-whisper-" + CFG["transcribe_whisper"], keep=["int8", "tokens"])
    al = CFG["aligner"]
    get_tar(al["dir"], keep=[al["encoder"], al["decoder"], al["joiner"], al["tokens"]])
    if offline != "none":
        wanted.add("sherpa-onnx-whisper-" + offline)
        get_tar("sherpa-onnx-whisper-" + offline, keep=["int8", "tokens"])
    if a.prune:
        for d in MODELS.iterdir():
            if d.is_dir() and d.name.startswith("sherpa-onnx-") and d.name not in wanted:
                print(f"- {d.name}: no longer used, removing"); shutil.rmtree(d, ignore_errors=True)
        for f in MODELS.glob("*.onnx"):
            if f.name != CFG["vad"]:
                print(f"- {f.name}: no longer used, removing"); f.unlink()
    print("Speech models are up to date.")


if __name__ == "__main__":
    main()
