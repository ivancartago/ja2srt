"""Update the app and its speech models from the GitHub repository named in repo.txt.   python update.py"""
import io, shutil, subprocess, sys, tempfile, urllib.request, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
KEEP = {"models", ".venv", "venv", ".git", "__pycache__"}      # never overwritten or removed


def read(p):
    return p.read_text(encoding="utf-8").strip() if p.exists() else ""


def vtuple(v):
    return tuple(int(x) for x in v.split(".") if x.isdigit())


def changes_since(old, text):
    """Sections of CHANGELOG.md that are newer than version `old`."""
    out, show = [], False
    for line in text.splitlines():
        if line.startswith("## "):
            show = vtuple(line[3:].strip()) > vtuple(old)
        if show:
            out.append(line)
    return "\n".join(out).strip()


def main():
    repo = read(ROOT / "repo.txt")
    if not repo or "/" not in repo:
        raise SystemExit("repo.txt should contain the GitHub repository, for example  yourname/ja2srt")
    old = read(ROOT / "VERSION") or "0"
    print(f"Installed version: {old}\nChecking https://github.com/{repo} ...")
    if (ROOT / ".git").exists() and shutil.which("git"):
        r = subprocess.run(["git", "-C", str(ROOT), "pull", "--ff-only"], capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit("git pull failed:\n" + (r.stderr or r.stdout).strip())
    else:
        url = f"https://github.com/{repo}/archive/refs/heads/main.zip"
        try:
            data = urllib.request.urlopen(url, timeout=120).read()
        except Exception as e:
            raise SystemExit(f"Could not download the update ({e}). Check your connection and try again.")
        with tempfile.TemporaryDirectory() as tmp:
            zipfile.ZipFile(io.BytesIO(data)).extractall(tmp)
            src = next(p for p in Path(tmp).iterdir() if p.is_dir())
            for item in src.iterdir():
                if item.name in KEEP:
                    continue
                dest = ROOT / item.name
                if item.is_dir():
                    shutil.copytree(item, dest, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, dest)
    new = read(ROOT / "VERSION") or "0"
    if vtuple(new) > vtuple(old):
        print(f"Updated the app: {old} -> {new}")
        notes = changes_since(old, read(ROOT / "CHANGELOG.md"))
        if notes:
            print("\nWhat changed:\n" + notes + "\n")
    else:
        print("The app is already the latest version.")
    print("Checking Python packages ...")
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--disable-pip-version-check", "-r", str(ROOT / "requirements.txt")], check=False)
    print("Checking speech models ...")
    subprocess.run([sys.executable, str(ROOT / "setup_models.py"), "--prune"], check=False)
    print("\nUpdate finished. Start the app again to use the new version.")


if __name__ == "__main__":
    main()
