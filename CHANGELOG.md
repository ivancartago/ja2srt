# Changes

## 1.2.0
- Fix words: correct a mistranslated name or word everywhere in an existing .srt, with a preview. Timings are untouched.
- The page lists the names found in a subtitle file so odd spellings are easy to spot.
- Saved word list: corrections you choose to remember are applied to every new subtitle file and given to Claude in Claude mode.

## 1.1.0
- Update feature: `python update.py` (or **Update ja2srt.bat** on Windows) fetches the latest app and speech models.
- The page shows the installed version and tells you when a newer one is available.
- Speech models are now listed in `models.json`, so they can be changed without touching the code.
- Windows helpers: **Install ja2srt.bat**, **Start ja2srt.bat**, **Update ja2srt.bat**.

## 1.0.0
- First version: local web page, offline and Claude API translation modes, sync check.
