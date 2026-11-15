# ja2srt: Japanese audio to English subtitles

Drop a Japanese audio (or video) file into a local web page and get back an English `.srt` whose lines appear when they are spoken.

It runs on your own computer. Nothing is uploaded anywhere unless you choose the Claude translation mode, in which case only the Japanese transcript text (not the audio) is sent to the Anthropic API.

## Two translation modes

| | Offline | Claude API |
|---|---|---|
| Cost | Free | Billed to your own Anthropic API key |
| English quality | Rough: literal phrasing, unreliable names, occasional nonsense on noisy audio | Much better: three transcripts are compared, names kept consistent, natural subtitle wording |
| Long sentences | One subtitle per stretch of speech | Split into subtitle-sized cues, each timed separately |
| Sync check | Not needed (one cue per detected stretch of speech) | Re-listens to every cue and lists the ones it could not confirm |
| Needs internet | Only for the one-time model download | Yes |

## Install

Needs Python 3.10 or newer and about 2.5 GB of disk space for the speech models. No GPU needed.

```bash
git clone <this repository>
cd ja2srt
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python setup_models.py           # one-time download, resumes if interrupted
```

On Windows you can skip the commands: install Python from python.org (tick "Add python.exe to PATH"), download this repository as a zip, unzip it, and double-click **Install ja2srt.bat**. After that, **Start ja2srt.bat** runs the app and **Update ja2srt.bat** updates it.

`setup_models.py` options:

- `--offline-model small` (smaller download, rougher English), `medium` (default), or `large-v3` (best offline quality, several times slower)
- `--offline-model none` if you will only use Claude mode (saves about 1 GB)

## Use

```bash
python app.py
```

A page opens at `http://127.0.0.1:8765`. Drop in a file, pick a mode, press **Make subtitles**, then download the `.srt`. It is named after your audio file, so most video players load it automatically when it sits next to the video.

For Claude mode, paste an Anthropic API key (create one at https://console.anthropic.com). The key is used for that run only and is never written to disk. You can also set it as the `ANTHROPIC_API_KEY` environment variable. The model field is pre-filled; if the API rejects the model name, replace it with a current one from https://docs.claude.com.

Command line, without the web page:

```bash
python pipeline.py episode.mp3                      # offline
python pipeline.py episode.mp3 --mode claude        # uses ANTHROPIC_API_KEY
```

## Updating

```bash
python update.py          # Windows: double-click "Update ja2srt.bat"
```

This fetches the latest app from the repository named in `repo.txt`, reinstalls the Python packages if needed, downloads any speech models that have changed and removes ones no longer used. It prints what changed. The page also shows your version and tells you when a newer one is available.

What can improve, and how:

- **Claude translation:** type a newer model name into the Model box. No update needed.
- **The app:** released by raising the number in `VERSION` and adding a note to `CHANGELOG.md`.
- **Speech models:** listed in `models.json`. A better model is a new, differently named one that someone has to choose and test; once `models.json` in the repository points to it, Update switches everyone over.

If you fork this repository, put your own `owner/name` in `repo.txt` so your copy updates from your fork.

## How long it takes

On a 2-core machine with no GPU, a 2-minute clip took about 2 minutes in either mode (offline with the medium model; Claude mode excluding the API calls). More cores are faster. Offline mode with `large-v3` is several times slower.

## How it works

1. **Find speech.** A voice-activity detector marks where speech starts and stops. These times are the backbone of the subtitle timing.
2. **Transcribe.** Whisper (turbo) transcribes each stretch of speech in Japanese.
3. **Offline mode:** a second Whisper model translates each stretch straight to English. One stretch of speech becomes one subtitle.
4. **Claude mode:**
   - A second recogniser (ReazonSpeech zipformer) transcribes the same audio with a timestamp for every character, and Whisper listens again with longer context. That gives three readings of every line.
   - Claude compares the readings, decides what was said, splits long stretches into subtitle-sized cues and writes the English.
   - Each cue's Japanese is matched against the character timestamps to find where it starts and ends.
   - **Sync check:** the app re-transcribes each cue's own time window and compares it with the line that should be there. Cues it cannot confirm are listed for you to look at.
5. **Timing rules.** A subtitle appears 0.1 s before its line, stays until 0.4 s after it (longer if needed for reading, up to 2 s), and never overlaps the next one.

## Limits

- Timing comes from the audio alone. If your video has a different intro length, shift everything with your player's subtitle-delay setting.
- Music, cheering and people talking over each other reduce accuracy in both modes.
- Names are spelled by ear and may not match official spellings.
- Songs are not handled well.
- One file is processed at a time.
- The sync check flags lines it could not confirm; it does not fix them. Open the `.srt` in a subtitle editor to nudge those.

## Credits

Speech models are downloaded at setup from the [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) project's releases: Silero VAD, OpenAI Whisper (ONNX exports) and the ReazonSpeech Japanese zipformer. They are not included in this repository; see each project for its licence.
