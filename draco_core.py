import os
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

class DracoOS:
    def __init__(self):
        self.cwd = "/"
        self.running = True
        self.commands = {}
        self.fs = {"/": {}}
        self._builtins()

    def register(self, name, callback, description, chapter=1):
        self.commands[name] = (callback, description, chapter)

    def execute(self, line):
        try:
            parts = shlex.split(line)
        except ValueError as exc:
            return f"Erreur de syntaxe : {exc}"
        if not parts:
            return ""
        entry = self.commands.get(parts[0].lower())
        if not entry:
            return f"Commande inconnue : {parts[0]}. Tape « help »."
        try:
            result = entry[0](parts[1:])
            return "" if result is None else str(result)
        except Exception as exc:
            return f"Erreur : {exc}"

    def _builtins(self):
        cmds = [
            ("help", self.help, "Affiche l'aide par chapitres", 1),
            ("about", lambda a: "DracoOS — système expérimental en Python.", "À propos", 1),
            ("whoami", lambda a: "user", "Affiche l'utilisateur courant", 1),
            ("clear", lambda a: "\\033[2J\\033[H", "Efface le terminal", 1),
            ("exit", self.exit, "Ferme DracoOS", 1),
            ("pwd", lambda a: self.cwd, "Affiche le dossier virtuel courant", 2),
            ("ls", self.ls, "Liste un dossier virtuel", 2),
            ("cd", self.cd, "Change de dossier virtuel", 2),
            ("mkdir", self.mkdir, "Crée un dossier virtuel", 2),
            ("touch", self.touch, "Crée un fichier virtuel", 2),
            ("cat", self.cat, "Affiche un fichier virtuel", 2),
            ("write", self.write, "Écrit dans un fichier virtuel", 2),
            ("append", self.append, "Ajoute du texte à un fichier virtuel", 2),
            ("rm", self.rm, "Supprime un fichier ou dossier vide", 2),
            ("echo", lambda a: " ".join(a), "Affiche du texte", 3),
            ("apps", self.apps, "Liste les applications Python dans apps/", 4),
            ("update", self.update, "Lance l'updater GitHub", 5),
        ]
        for name, func, desc, chapter in cmds:
            self.register(name, func, desc, chapter)

    def help(self, args):
        chapters = {
            1: ("Système et session", ["help","about","whoami","clear","exit"]),
            2: ("Fichiers et dossiers virtuels", ["pwd","ls","cd","mkdir","touch","cat","write","append","rm"]),
            3: ("Texte et affichage", ["echo"]),
            4: ("Applications", ["apps"]),
            5: ("Réseau et mises à jour", ["update"]),
        }
        if not args:
            return "Aide DracoOS — chapitres disponibles :\\n" + "\\n".join(
                f"  help {n} — {title}" for n,(title,_) in chapters.items()
            ) + "\\n\\nTape « help <numéro> » pour afficher un chapitre."
        if len(args) != 1 or not args[0].isdigit() or int(args[0]) not in chapters:
            return "Chapitre inconnu. Tape « help »."
        n = int(args[0]); title, names = chapters[n]
        return f"Chapitre {n} — {title}\\n" + "\\n".join(
            f"  {name:<10} {self.commands[name][1]}" for name in names if name in self.commands
        )

    def _resolve(self, path):
        parts = [] if path.startswith("/") else [x for x in self.cwd.split("/") if x]
        for item in path.split("/"):
            if item in ("", "."): continue
            if item == "..":
                if parts: parts.pop()
            else: parts.append(item)
        return "/" + "/".join(parts)

    def _parent(self, path):
        target = self._resolve(path)
        if target == "/": raise ValueError("Opération impossible sur la racine.")
        parent_path, _, name = target.rpartition("/")
        parent_path = parent_path or "/"
        parent = self.fs.get(parent_path)
        if not isinstance(parent, dict): raise FileNotFoundError(parent_path)
        return target, parent, name

    def ls(self, args):
        target = self._resolve(args[0]) if args else self.cwd
        node = self.fs.get(target)
        if not isinstance(node, dict): raise FileNotFoundError(target)
        return "\\n".join(sorted(node)) if node else "(vide)"

    def cd(self, args):
        target = self._resolve(args[0]) if args else "/"
        if not isinstance(self.fs.get(target), dict): raise FileNotFoundError(target)
        self.cwd = target

    def mkdir(self, args):
        if not args: return "Usage : mkdir <nom>"
        for path in args:
            target, parent, name = self._parent(path)
            if name in parent: raise FileExistsError(target)
            parent[name] = {}; self.fs[target] = parent[name]

    def touch(self, args):
        if not args: return "Usage : touch <nom>"
        for path in args:
            target, parent, name = self._parent(path)
            if name not in parent: parent[name] = ""; self.fs[target] = ""

    def cat(self, args):
        if len(args) != 1: return "Usage : cat <fichier>"
        value = self.fs.get(self._resolve(args[0]))
        if not isinstance(value, str): raise FileNotFoundError(args[0])
        return value

    def write(self, args):
        if len(args) < 2: return 'Usage : write <fichier> "texte"'
        target, parent, name = self._parent(args[0])
        if isinstance(parent.get(name), dict): raise IsADirectoryError(target)
        parent[name] = " ".join(args[1:]); self.fs[target] = parent[name]

    def append(self, args):
        if len(args) < 2: return 'Usage : append <fichier> "texte"'
        target = self._resolve(args[0]); old = self.fs.get(target)
        if not isinstance(old, str): raise FileNotFoundError(target)
        value = old + " ".join(args[1:])
        self.fs[target] = value
        _, parent, name = self._parent(target); parent[name] = value

    def rm(self, args):
        if len(args) != 1: return "Usage : rm <fichier-ou-dossier-vide>"
        target, parent, name = self._parent(args[0])
        if name not in parent: raise FileNotFoundError(target)
        if isinstance(parent[name], dict) and parent[name]: raise OSError("Dossier non vide.")
        del parent[name]; self.fs.pop(target, None)

    def apps(self, args):
        files = sorted(p.name for p in (ROOT/"apps").glob("*.py") if p.name != "__init__.py")
        return "\\n".join(files) if files else "Aucune application Python dans apps/."

    def update(self, args):
        updater = ROOT / "updates.py"
        if not updater.exists(): return "updates.py est introuvable."
        subprocess.Popen([sys.executable, str(updater), "--wait-pid", str(os.getpid())], cwd=str(ROOT))
        self.running = False
        return "Updater lancé. DracoOS va se fermer."

    def exit(self, args):
        self.running = False
        return "Fermeture de DracoOS."

def main():
    osys = DracoOS()
    print("DracoOS v0.2 — tape « help » pour commencer.")
    while osys.running:
        try: line = input(f"DracoOS:{osys.cwd}$ ")
        except (EOFError, KeyboardInterrupt):
            print(); break
        output = osys.execute(line)
        if output: print(output)

if __name__ == "__main__":
    main()
