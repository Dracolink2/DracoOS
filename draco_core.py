"""DracoOS Core — moteur de commandes indépendant de l'interface."""
from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Callable

@dataclass
class Node:
    kind: str  # "dir" ou "file"
    content: str = ""
    children: dict[str, "Node"] = field(default_factory=dict)

@dataclass
class Session:
    username: str = "user"
    cwd: str = "/home/user"

@dataclass
class Command:
    name: str
    usage: str
    description: str
    handler: Callable[["DracoOS", Session, list[str]], str]

class VirtualFileSystem:
    """Petit système de fichiers en mémoire : ne touche pas aux fichiers Windows."""
    def __init__(self):
        self.root = Node("dir")
        self.mkdir("/system")
        self.mkdir("/apps")
        self.mkdir("/home")
        self.mkdir("/home/user")
        self.write("/system/welcome.txt", "Bienvenue dans DracoOS v0.1 !")
        self.write("/home/user/notes.txt", "Mes premières notes dans DracoOS.")

    def normalize(self, path: str, cwd: str = "/") -> str:
        if not path:
            path = cwd
        p = PurePosixPath(path if path.startswith("/") else f"{cwd}/{path}")
        parts = []
        for part in p.parts:
            if part in ("/", "", "."):
                continue
            if part == "..":
                if parts:
                    parts.pop()
            else:
                parts.append(part)
        return "/" + "/".join(parts)

    def _node(self, path: str, cwd: str = "/") -> Node:
        normalized = self.normalize(path, cwd)
        node = self.root
        if normalized == "/":
            return node
        for part in normalized.strip("/").split("/"):
            if node.kind != "dir" or part not in node.children:
                raise FileNotFoundError(f"Chemin introuvable : {normalized}")
            node = node.children[part]
        return node

    def mkdir(self, path: str, cwd: str = "/", parents: bool = False) -> str:
        normalized = self.normalize(path, cwd)
        if normalized == "/":
            raise FileExistsError("La racine existe déjà.")
        parts = normalized.strip("/").split("/")
        node = self.root
        for i, part in enumerate(parts):
            if part in node.children:
                child = node.children[part]
                if child.kind != "dir":
                    raise NotADirectoryError(f"Un fichier bloque le chemin : {part}")
                if i == len(parts) - 1:
                    raise FileExistsError(f"Le dossier existe déjà : {normalized}")
                node = child
            else:
                if i != len(parts) - 1 and not parents:
                    raise FileNotFoundError("Le dossier parent n'existe pas (utilise mkdir -p).")
                child = Node("dir")
                node.children[part] = child
                node = child
        return normalized

    def write(self, path: str, content: str, cwd: str = "/", append: bool = False) -> str:
        normalized = self.normalize(path, cwd)
        parent_path, _, name = normalized.rpartition("/")
        parent = self._node(parent_path or "/", "/")
        if parent.kind != "dir":
            raise NotADirectoryError(parent_path)
        if not name:
            raise IsADirectoryError("Il faut indiquer un nom de fichier.")
        existing = parent.children.get(name)
        if existing and existing.kind == "dir":
            raise IsADirectoryError(f"Un dossier porte déjà ce nom : {normalized}")
        if existing and append:
            existing.content += content
        else:
            parent.children[name] = Node("file", content=content)
        return normalized

    def read(self, path: str, cwd: str = "/") -> str:
        node = self._node(path, cwd)
        if node.kind != "file":
            raise IsADirectoryError(f"Ce chemin est un dossier : {self.normalize(path, cwd)}")
        return node.content

    def ls(self, path: str = ".", cwd: str = "/") -> list[str]:
        node = self._node(path, cwd)
        if node.kind != "dir":
            return [PurePosixPath(self.normalize(path, cwd)).name]
        return [name + ("/" if child.kind == "dir" else "")
                for name, child in sorted(node.children.items())]

    def remove(self, path: str, cwd: str = "/", recursive: bool = False) -> str:
        normalized = self.normalize(path, cwd)
        if normalized == "/":
            raise PermissionError("La racine ne peut pas être supprimée.")
        parent_path, _, name = normalized.rpartition("/")
        parent = self._node(parent_path or "/", "/")
        if name not in parent.children:
            raise FileNotFoundError(f"Chemin introuvable : {normalized}")
        node = parent.children[name]
        if node.kind == "dir" and node.children and not recursive:
            raise OSError("Le dossier n'est pas vide (utilise rm -r).")
        del parent.children[name]
        return normalized

class DracoOS:
    def __init__(self):
        self.fs = VirtualFileSystem()
        self.session = Session()
        self.commands: dict[str, Command] = {}
        self.running = True
        self._register_builtins()

    def register(self, name: str, usage: str, description: str,
                 handler: Callable[["DracoOS", Session, list[str]], str]) -> None:
        """Enregistre une commande. Les extensions peuvent appeler cette méthode."""
        if not name or any(ch.isspace() for ch in name):
            raise ValueError("Le nom d'une commande doit être un seul mot.")
        self.commands[name] = Command(name, usage, description, handler)

    def prompt(self) -> str:
        return f"{self.session.username}@draco:{self.session.cwd}$ "

    def execute(self, line: str) -> str:
        """API publique : exécute une ligne et renvoie du texte, sans dépendre d'un terminal."""
        try:
            parts = shlex.split(line)
        except ValueError as exc:
            return f"Erreur de syntaxe : {exc}"
        if not parts:
            return ""
        name, args = parts[0].lower(), parts[1:]
        command = self.commands.get(name)
        if command is None:
            return f"Commande inconnue : {name}. Tape help pour voir les commandes."
        try:
            return command.handler(self, self.session, args)
        except (FileNotFoundError, FileExistsError, NotADirectoryError,
                IsADirectoryError, PermissionError, OSError, ValueError) as exc:
            return f"Erreur : {exc}"

    def _register_builtins(self):
        self.register("help", "help [commande]", "Affiche l'aide.", self._cmd_help)
        self.register("about", "about", "Présente DracoOS.", lambda os_, s, a: "DracoOS v0.1 — un OS simulé, extensible et minimaliste.")
        self.register("clear", "clear", "Efface l'écran du terminal.", lambda os_, s, a: "__CLEAR__")
        self.register("whoami", "whoami", "Affiche l'utilisateur actuel.", lambda os_, s, a: s.username)
        self.register("pwd", "pwd", "Affiche le dossier actuel.", lambda os_, s, a: s.cwd)
        self.register("ls", "ls [chemin]", "Liste les fichiers et dossiers.", self._cmd_ls)
        self.register("cd", "cd [chemin]", "Change de dossier.", self._cmd_cd)
        self.register("mkdir", "mkdir [-p] chemin", "Crée un dossier virtuel.", self._cmd_mkdir)
        self.register("touch", "touch fichier", "Crée un fichier vide.", self._cmd_touch)
        self.register("cat", "cat fichier", "Affiche le contenu d'un fichier.", self._cmd_cat)
        self.register("write", "write fichier texte...", "Remplace le contenu d'un fichier.", self._cmd_write)
        self.register("append", "append fichier texte...", "Ajoute du texte à un fichier.", self._cmd_append)
        self.register("rm", "rm [-r] chemin", "Supprime un fichier ou dossier virtuel.", self._cmd_rm)
        self.register("echo", "echo texte...", "Affiche du texte.", lambda os_, s, a: " ".join(a))
        self.register("apps", "apps", "Liste les applications (à venir).", lambda os_, s, a: "Aucune application installée pour le moment.")
        self.register("exit", "exit", "Ferme la session du terminal.", self._cmd_exit)

    def _need(self, args: list[str], usage: str):
        if not args:
            raise ValueError(f"Usage : {usage}")

    def _cmd_help(self, os_, session, args):
        if args:
            cmd = self.commands.get(args[0])
            return f"{cmd.usage}\n  {cmd.description}" if cmd else f"Commande inconnue : {args[0]}"
        lines = ["Commandes disponibles :"]
        lines.extend(f"  {c.name:<8} {c.description}  ({c.usage})"
                     for c in sorted(self.commands.values(), key=lambda c: c.name))
        return "\n".join(lines)

    def _cmd_ls(self, os_, s, a):
        return "\n".join(self.fs.ls(a[0] if a else ".", s.cwd)) or "(dossier vide)"

    def _cmd_cd(self, os_, s, a):
        target = a[0] if a else f"/home/{s.username}"
        node = self.fs._node(target, s.cwd)
        if node.kind != "dir":
            raise NotADirectoryError(f"Ce n'est pas un dossier : {target}")
        s.cwd = self.fs.normalize(target, s.cwd)
        return ""

    def _cmd_mkdir(self, os_, s, a):
        parents = bool(a and a[0] == "-p")
        if parents:
            a = a[1:]
        self._need(a, "mkdir [-p] chemin")
        return f"Dossier créé : {self.fs.mkdir(a[0], s.cwd, parents)}"

    def _cmd_touch(self, os_, s, a):
        self._need(a, "touch fichier")
        path = self.fs.normalize(a[0], s.cwd)
        try:
            self.fs.read(path)
        except FileNotFoundError:
            self.fs.write(path, "", "/")
        return f"Fichier prêt : {path}"

    def _cmd_cat(self, os_, s, a):
        self._need(a, "cat fichier")
        return self.fs.read(a[0], s.cwd)

    def _cmd_write(self, os_, s, a):
        if len(a) < 2:
            raise ValueError("Usage : write fichier texte...")
        path = self.fs.write(a[0], " ".join(a[1:]), s.cwd)
        return f"Fichier écrit : {path}"

    def _cmd_append(self, os_, s, a):
        if len(a) < 2:
            raise ValueError("Usage : append fichier texte...")
        path = self.fs.write(a[0], " ".join(a[1:]), s.cwd, append=True)
        return f"Texte ajouté : {path}"

    def _cmd_rm(self, os_, s, a):
        recursive = bool(a and a[0] == "-r")
        if recursive:
            a = a[1:]
        self._need(a, "rm [-r] chemin")
        return f"Supprimé : {self.fs.remove(a[0], s.cwd, recursive)}"

    def _cmd_exit(self, os_, s, a):
        self.running = False
        return "Fermeture de la session DracoOS. À bientôt !"

if __name__ == "__main__":
    import sys
    os_ = DracoOS()
    print("DracoOS v0.1 — tape help pour commencer.\n")
    while os_.running:
        try:
            line = input(os_.prompt())
        except (EOFError, KeyboardInterrupt):
            print("\nFermeture de la session DracoOS.")
            break
        output = os_.execute(line)
        if output and output != "__CLEAR__":
            print(output)
        elif output == "__CLEAR__":
            print("\033[2J\033[H", end="")
