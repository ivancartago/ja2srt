"""Local web page: drop in Japanese audio, get an English .srt.   Start with:  python app.py"""
import os, tempfile, threading, time, urllib.request, uuid, webbrowser
from pathlib import Path

from flask import Flask, Response, jsonify, request

import pipeline

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 ** 3          # 2 GB upload limit
JOBS = {}
ONE_AT_A_TIME = threading.Lock()

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Japanese audio to English subtitles</title>
<style>
  :root { --ink:#1d2433; --soft:#5b6478; --line:#d9dde6; --bg:#f6f4ef; --card:#fffdf9; --accent:#b4442c; --ok:#2e6b4f; }
  * { box-sizing:border-box }
  body { margin:0; background:var(--bg); color:var(--ink); font:16px/1.5 Georgia, "Times New Roman", serif; }
  main { max-width:680px; margin:0 auto; padding:48px 20px 80px; }
  h1 { font-size:30px; line-height:1.2; margin:0 0 6px; letter-spacing:-.01em; }
  .sub { color:var(--soft); margin:0 0 28px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:6px; padding:22px; margin-bottom:18px; }
  #drop { border:2px dashed var(--line); border-radius:6px; padding:34px 16px; text-align:center; cursor:pointer; color:var(--soft); }
  #drop.over, #drop:focus-visible { border-color:var(--accent); color:var(--ink); outline:none; }
  #drop strong { color:var(--ink); display:block; font-size:18px; }
  fieldset { border:0; padding:0; margin:18px 0 0; }
  legend { font-weight:bold; padding:0; margin-bottom:8px; }
  label.opt { display:block; padding:10px 12px; border:1px solid var(--line); border-radius:6px; margin-bottom:8px; cursor:pointer; }
  label.opt:has(input:checked) { border-color:var(--accent); background:#fbf1ed; }
  label.opt small { display:block; color:var(--soft); margin-left:24px; }
  .field { margin-top:10px; } .field label { display:block; font-size:14px; color:var(--soft); }
  input[type=password], input[type=text] { width:100%; padding:9px 10px; border:1px solid var(--line); border-radius:4px; font:15px ui-monospace, Menlo, Consolas, monospace; background:#fff; }
  input:focus-visible, button:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
  button { font:inherit; font-weight:bold; background:var(--accent); color:#fff; border:0; border-radius:4px; padding:12px 22px; cursor:pointer; margin-top:16px; }
  button[disabled] { background:#c9c3bb; cursor:not-allowed; }
  a.btn { display:inline-block; background:var(--ok); color:#fff; font-weight:bold; text-decoration:none; border-radius:4px; padding:12px 22px; }
  progress { width:100%; height:14px; accent-color:var(--accent); }
  #msg { color:var(--soft); margin:8px 0 0; min-height:1.5em; }
  .err { color:var(--accent); font-weight:bold; }
  ul { padding-left:20px; margin:10px 0 0; } li { margin-bottom:6px; }
  table { border-collapse:collapse; width:100%; margin-top:10px; font-size:14px; } td { border-top:1px solid var(--line); padding:6px 8px 6px 0; vertical-align:top; }
  td:first-child { font-family:ui-monospace, Menlo, Consolas, monospace; white-space:nowrap; color:var(--soft); }
  [hidden] { display:none !important; }
</style></head><body><main>
<h1>Japanese audio to English subtitles</h1>
<p class="sub">Drop in a Japanese audio or video file and get an English .srt timed to the speech. Everything except the optional Claude translation runs on this computer.</p>

<form id="form" class="card">
  <div id="drop" tabindex="0" role="button" aria-label="Choose an audio file"><strong id="fname">Drop an audio file here</strong>or click to choose (MP3, M4A, WAV, MP4…)</div>
  <input id="file" type="file" accept="audio/*,video/*" hidden>
  <fieldset><legend>Translation</legend>
    <label class="opt"><input type="radio" name="mode" value="offline" checked> Offline (free)
      <small id="offnote">Runs entirely on this computer. English is rougher and names are less reliable.</small></label>
    <label class="opt"><input type="radio" name="mode" value="claude"> Claude API (better)
      <small>Japanese is transcribed here, then Claude writes the English. Needs your own Anthropic API key; usage is billed to that key.</small></label>
    <div id="claudeopts" hidden>
      <div class="field"><label for="key">Anthropic API key (used for this run only, never saved)</label><input id="key" type="password" autocomplete="off" placeholder="sk-ant-…"></div>
      <div class="field"><label for="model">Model</label><input id="model" type="text" value="__MODEL__"></div>
      <div class="field"><label><input id="check" type="checkbox" checked> Run the sync check afterwards (adds a few minutes)</label></div>
    </div>
  </fieldset>
  <button id="go" type="submit" disabled>Make subtitles</button>
</form>

<div id="work" class="card" hidden><progress id="bar" max="100" value="0"></progress><p id="msg" role="status" aria-live="polite"></p></div>

<div id="done" class="card" hidden>
  <p><a id="dl" class="btn" href="#">Download .srt</a></p>
  <p id="summary"></p><ul id="notes"></ul>
  <div id="flagbox" hidden><p><strong>Subtitles worth a look</strong> (the check could not confirm these in their time slot):</p><table id="flags"></table></div>
  <button id="again" type="button">Do another file</button>
</div>
<p id="ver" class="sub" style="margin-top:24px;font-size:14px"></p>
</main>
<script>
const $ = id => document.getElementById(id);
let file = null;
const pick = f => { if (!f) return; file = f; $('fname').textContent = f.name; $('go').disabled = false; };
$('drop').onclick = () => $('file').click();
$('drop').onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); $('file').click(); } };
$('file').onchange = e => pick(e.target.files[0]);
['dragover','dragenter'].forEach(t => $('drop').addEventListener(t, e => { e.preventDefault(); $('drop').classList.add('over'); }));
['dragleave','drop'].forEach(t => $('drop').addEventListener(t, e => { e.preventDefault(); $('drop').classList.remove('over'); }));
$('drop').addEventListener('drop', e => pick(e.dataTransfer.files[0]));
document.querySelectorAll('input[name=mode]').forEach(r => r.onchange = () => $('claudeopts').hidden = r.value !== 'claude' || !r.checked);
fetch('/info').then(r => r.json()).then(i => { $('ver').textContent = 'Version ' + i.version + (i.update ? '. A newer version (' + i.latest + ') is available: close the app and run "Update ja2srt.bat" (or: python update.py).' : ''); if (!i.offline_model) $('offnote').textContent = 'Not installed yet. Run: python setup_models.py --offline-model medium'; else $('offnote').textContent += ' Model: Whisper ' + i.offline_model + '.'; });

const fail = t => { $('msg').innerHTML = ''; const s = document.createElement('span'); s.className = 'err'; s.textContent = t; $('msg').append(s); $('go').disabled = false; };
$('form').onsubmit = async e => {
  e.preventDefault(); if (!file) return;
  const mode = document.querySelector('input[name=mode]:checked').value;
  if (mode === 'claude' && !$('key').value.trim()) { $('work').hidden = false; return fail('Enter your Anthropic API key, or choose Offline.'); }
  const fd = new FormData(); fd.append('file', file); fd.append('mode', mode); fd.append('key', $('key').value.trim());
  fd.append('model', $('model').value.trim()); fd.append('check', $('check').checked ? '1' : '0');
  $('go').disabled = true; $('done').hidden = true; $('work').hidden = false; $('bar').value = 0; $('msg').textContent = 'Uploading…';
  let r; try { r = await (await fetch('/start', { method: 'POST', body: fd })).json(); } catch (x) { return fail('Could not reach the app. Is it still running?'); }
  if (r.error) return fail(r.error);
  const poll = async () => {
    let s; try { s = await (await fetch('/status/' + r.id)).json(); } catch (x) { return setTimeout(poll, 2000); }
    if (s.error) return fail(s.error);
    $('bar').value = Math.round(s.frac * 100); $('msg').textContent = s.msg;
    if (!s.done) return setTimeout(poll, 1000);
    $('work').hidden = true; $('done').hidden = false; $('dl').href = '/download/' + r.id; $('dl').download = s.name;
    $('summary').textContent = s.name + ': ' + s.cues + ' subtitles.';
    $('notes').replaceChildren(...s.notes.map(n => Object.assign(document.createElement('li'), { textContent: n })));
    $('flagbox').hidden = !s.flags.length;
    $('flags').replaceChildren(...s.flags.map(f => { const tr = document.createElement('tr'); tr.append(Object.assign(document.createElement('td'), { textContent: f.time }), Object.assign(document.createElement('td'), { textContent: f.en })); return tr; }));
  };
  poll();
};
$('again').onclick = () => { $('done').hidden = true; file = null; $('file').value = ''; $('fname').textContent = 'Drop an audio file here'; $('go').disabled = true; };
</script></body></html>"""


@app.get("/")
def index():
    return PAGE.replace("__MODEL__", pipeline.DEFAULT_CLAUDE_MODEL)


def _read(name):
    p = pipeline.ROOT / name
    return p.read_text(encoding="utf-8").strip() if p.exists() else ""


def _vt(v):
    return tuple(int(x) for x in v.split(".") if x.isdigit())


_latest = {"at": 0.0, "version": ""}

def latest_version():
    """Version number in the GitHub repository (checked at most once an hour; empty if it cannot be reached)."""
    repo = _read("repo.txt")
    if repo and time.time() - _latest["at"] > 3600:
        _latest["at"] = time.time()
        try:
            with urllib.request.urlopen(f"https://raw.githubusercontent.com/{repo}/main/VERSION", timeout=4) as r:
                _latest["version"] = r.read().decode().strip()[:20]
        except Exception:
            _latest["version"] = ""
    return _latest["version"]


@app.get("/info")
def info():
    mine, latest = _read("VERSION"), latest_version()
    return jsonify(offline_model=pipeline.offline_model_name(), version=mine, latest=latest,
                   update=bool(latest and _vt(latest) > _vt(mine)))


@app.post("/start")
def start():
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify(error="No file was received.")
    mode = request.form.get("mode", "offline")
    key = request.form.get("key", "") or os.environ.get("ANTHROPIC_API_KEY", "")
    if mode == "claude" and not key:
        return jsonify(error="Claude mode needs an Anthropic API key.")
    if not ONE_AT_A_TIME.acquire(blocking=False):
        return jsonify(error="Another file is still being processed. Wait for it to finish.")
    jid = uuid.uuid4().hex
    tmp = Path(tempfile.mkdtemp(prefix="ja2srt_")) / ("in" + Path(f.filename).suffix.lower())
    f.save(tmp)
    job = JOBS[jid] = {"msg": "Starting", "frac": 0.0, "done": False, "error": None,
                       "name": Path(f.filename).stem + ".srt"}
    model, check = request.form.get("model", ""), request.form.get("check", "1") == "1"

    def work():
        try:
            def progress(msg, frac):
                job["msg"], job["frac"] = msg, frac
            res = pipeline.run(tmp, mode, key, model, check, progress)
            job.update(srt=res["srt"], cues=res["cues"], notes=res["notes"], flags=res["flags"], done=True)
        except Exception as e:                                   # shown to the user on the page
            job["error"] = str(e) or type(e).__name__
        finally:
            try:
                tmp.unlink(); tmp.parent.rmdir()
            except OSError:
                pass
            ONE_AT_A_TIME.release()

    threading.Thread(target=work, daemon=True).start()
    return jsonify(id=jid)


@app.get("/status/<jid>")
def status(jid):
    j = JOBS.get(jid)
    if not j:
        return jsonify(error="Unknown job (the app may have been restarted).")
    return jsonify({k: j.get(k) for k in ("msg", "frac", "done", "error", "name", "cues", "notes", "flags")})


@app.get("/download/<jid>")
def download(jid):
    j = JOBS.get(jid)
    if not j or not j.get("done"):
        return "Not ready", 404
    name = j["name"].encode("ascii", "ignore").decode() or "subtitles.srt"
    return Response(j["srt"], mimetype="application/x-subrip",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8765"))
    url = f"http://127.0.0.1:{port}"
    print(f"Open {url} in your browser (Ctrl+C to stop)")
    if not os.environ.get("NO_BROWSER"):
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
