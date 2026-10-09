import argparse, os, shutil, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
MANIFEST_URL = "https://raw.githubusercontent.com/Dracolink2/DracoOS/refs/heads/main/Updates.txt"

def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "DracoOS-Updater/0.2"})
    with urllib.request.urlopen(req, timeout=25) as response:
        if response.status != 200: raise RuntimeError(f"HTTP {response.status}")
        return response.read()

def parse_manifest(data):
    lines = [x.strip() for x in data.decode("utf-8-sig").splitlines()]
    entries, i = [], 0
    while i < len(lines):
        path = lines[i]; i += 1
        if not path or path.startswith("#"): continue
        if i >= len(lines) or not lines[i] or lines[i].startswith("#"):
            raise ValueError(f"URL manquante après {path}")
        entries.append((path, lines[i])); i += 1
    return entries

def destination(relative):
    rel = Path(relative.replace("\\\\", "/"))
    if rel.is_absolute() or not rel.parts or ".." in rel.parts or ":" in rel.parts[0]:
        raise ValueError(f"Chemin interdit : {relative}")
    dest = (ROOT / rel).resolve()
    if dest != ROOT and ROOT not in dest.parents: raise ValueError(f"Chemin interdit : {relative}")
    return dest

def wait_pid(pid):
    if not pid: return
    if os.name == "nt":
        while True:
            p = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if str(pid) not in p.stdout: return
            time.sleep(.4)
    else:
        while True:
            try: os.kill(pid, 0)
            except ProcessLookupError: return
            time.sleep(.4)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--wait-pid", type=int, default=0, help=argparse.SUPPRESS)
    args = parser.parse_args()
    print("DracoOS Updater — téléchargement du manifeste…")
    try:
        entries = parse_manifest(fetch(MANIFEST_URL))
        if not entries:
            print("Aucun fichier à mettre à jour (manifeste vide).")
            input("Entrée pour fermer…"); return 0
        with tempfile.TemporaryDirectory(prefix="dracoos-update-") as td:
            temp = Path(td); staged = []
            for i, (rel, url) in enumerate(entries, 1):
                dest = destination(rel); parsed = urlparse(url)
                if parsed.scheme != "https" or parsed.netloc != "raw.githubusercontent.com":
                    raise ValueError(f"URL RAW GitHub non autorisée : {url}")
                print(f"[{i}/{len(entries)}] Téléchargement : {rel}")
                f = temp / f"new_{i}"; f.write_bytes(fetch(url)); staged.append((rel, dest, f))
            print("Téléchargements prêts. Fermeture de DracoOS…")
            wait_pid(args.wait_pid)
            backups = []
            try:
                for i, (rel, dest, staged_file) in enumerate(staged, 1):
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    backup = None
                    if dest.exists():
                        if dest.is_dir(): raise IsADirectoryError(rel)
                        backup = temp / f"old_{i}"; shutil.copy2(dest, backup)
                    backups.append((dest, backup))
                    shutil.copy2(staged_file, dest)
                    print(f"[{i}/{len(staged)}] Installé : {rel}")
            except Exception:
                for dest, backup in reversed(backups):
                    try:
                        if backup is None:
                            if dest.exists(): dest.unlink()
                        elif backup.exists(): shutil.copy2(backup, dest)
                    except OSError: pass
                raise
        print("Mise à jour terminée. Redémarrage de DracoOS…")
        subprocess.Popen([sys.executable, str(ROOT/"draco_core.py")], cwd=str(ROOT))
        return 0
    except Exception as exc:
        print(f"Échec de la mise à jour : {exc}")
        input("Entrée pour fermer…"); return 1

if __name__ == "__main__":
    raise SystemExit(main())
