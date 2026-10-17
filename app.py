import os, sys, json, zlib, struct, threading, time, shutil, posixpath, html, atexit, hashlib, secrets
from flask import Flask, request, jsonify, render_template, Response, g, abort, send_file
try:
    import requests
except ImportError:
    requests = None

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "draco.db")
FMT, MAGIC, HDR, PORT = "<8sQQBI", b"DRACOOS1", 4096, 5173
L = threading.RLock()
S = dict(fs=None, total=0, blob=0, raw=0, dirty=False, err=None)
app = Flask(__name__)


HOMEDIRS = ("Bureau", "Documents", "Images", "Musique", "Jeux")
SESS = {}


def vp(p, u):
    h = "/Users/" + u["name"]
    if p in ("/System/settings.json", "/System/commands.json"):
        return h + p[7:]
    if p == "/System/Apps" or p.startswith("/System/Apps/"):
        return h + "/Apps" + p[len("/System/Apps"):]
    if p.split("/")[1] in HOMEDIRS:
        return h + p
    return p if u["admin"] else None


def norm(p):
    p = posixpath.normpath("/" + (p or "").lstrip("/"))
    if p == "/" or not getattr(g, "user", None):
        return p
    r = vp(p, g.user)
    if r is None:
        abort(403)
    return r


def default_fs():
    t = time.time()
    fs = {"/": {"t": "d", "m": t}}
    for p in ["/System", "/Users"]:
        fs[p] = {"t": "d", "m": t}
    cfg = {"theme": "dark", "accent": "#4f8cff", "compress": True,
           "wallpaper": "linear-gradient(135deg,#1e3c72,#2a5298 50%,#7b4397)"}
    fs["/System/settings.json"] = {"t": "f", "c": json.dumps(cfg), "m": t}
    fs["/Documents/Bienvenue.txt"] = {"t": "f", "c": "Bienvenue dans DracoOS !", "m": t}
    return fs


def save(compress=False):
    with L:
        raw = json.dumps(S["fs"], separators=(",", ":")).encode()
        blob = zlib.compress(raw, 6) if compress else raw
        if len(blob) > S["total"] - HDR:
            raise IOError("Disque plein")
        with open(DB, "r+b") as f:
            f.seek(HDR)
            f.write(blob)
            f.seek(0)
            f.write(struct.pack(FMT, MAGIC, S["total"], len(blob), int(compress), zlib.crc32(blob)))
            f.flush()
            os.fsync(f.fileno())
        S.update(raw=len(raw), blob=len(blob), dirty=False)


def load():
    try:
        with open(DB, "rb") as f:
            magic, total, n, comp, crc = struct.unpack(FMT, f.read(struct.calcsize(FMT)))
            if magic != MAGIC or os.path.getsize(DB) < total:
                raise ValueError("Fichier disque invalide ou modifié")
            f.seek(HDR)
            blob = f.read(n)
        if zlib.crc32(blob) != crc:
            raise ValueError("Disque corrompu (CRC)")
        raw = zlib.decompress(blob) if comp else blob
        S.update(fs=json.loads(raw), total=total, blob=n, raw=len(raw), dirty=False, err=None)
    except Exception as e:
        S["err"] = str(e)


def create(total):
    if shutil.disk_usage(BASE).free < total + (64 << 20):
        raise IOError("Pas assez d'espace libre sur le disque réel")
    chunk = b"\0" * (8 << 20)
    with open(DB, "wb") as f:
        n = total
        while n > 0:
            f.write(chunk[:min(n, len(chunk))])
            n -= len(chunk)
        f.flush()
        os.fsync(f.fileno())
    S.update(fs=default_fs(), total=total, err=None)
    save(False)


def settings():
    try:
        a = next(n for n, v in users().items() if v.get("admin"))
        return json.loads(S["fs"]["/Users/%s/settings.json" % a]["c"])
    except Exception:
        return {}


def shutdown_save():
    with L:
        if S["fs"] is not None:
            save(settings().get("compress", True))


def autosave():
    while True:
        time.sleep(1.5)
        try:
            with L:
                if S["fs"] is not None and S["dirty"]:
                    save(False)
        except Exception:
            pass


def mkparents(p):
    d = posixpath.dirname(p)
    while d != "/" and d not in S["fs"]:
        S["fs"][d] = {"t": "d", "m": time.time()}
        d = posixpath.dirname(d)


def err(msg, code=400):
    return jsonify(error=msg), code


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def status():
    if S["err"]:
        return jsonify(error=S["err"])
    return jsonify(init=S["fs"] is not None, total=S["total"], free=max(0, S["total"] - HDR - S["blob"]),
                   admin_needed=S["fs"] is not None and not users(), users=list(users()))


@app.route("/api/init", methods=["POST"])
def init():
    if S["fs"] is not None or os.path.exists(DB):
        return err("Disque déjà créé")
    mb = int(request.json.get("mb", 0))
    if mb < 64 or mb > 1024 * 1024:
        return err("Taille invalide (min 64 Mo)")
    try:
        create(mb << 20)
    except Exception as e:
        return err(str(e), 500)
    return jsonify(ok=True)


@app.route("/api/ls")
def ls():
    p = norm(request.args.get("p"))
    if p == "/":
        return jsonify(items=[{"n": d, "t": "d", "s": 0} for d in HOMEDIRS + (("System", "Users") if g.user["admin"] else ())])
    with L:
        if p not in S["fs"] or S["fs"][p]["t"] != "d":
            return err("Dossier introuvable", 404)
        items = [{"n": posixpath.basename(k), "t": v["t"], "s": len(v.get("c", "")), "m": v.get("m", 0)}
                 for k, v in S["fs"].items() if k != p and posixpath.dirname(k) == p]
    return jsonify(items=items)


@app.route("/api/read")
def read():
    p = norm(request.args.get("p"))
    with L:
        v = S["fs"].get(p)
        if not v or v["t"] != "f":
            return err("Fichier introuvable", 404)
        return jsonify(c=v["c"])


@app.route("/api/write", methods=["POST"])
def write():
    d = request.json
    p, c = norm(d.get("p")), str(d.get("c", ""))
    with L:
        if p == "/" or S["fs"].get(p, {}).get("t") == "d":
            return err("Chemin invalide")
        old = len(S["fs"].get(p, {}).get("c", ""))
        if S["raw"] + len(c) - old > S["total"] - HDR - 4096:
            return err("Disque plein", 507)
        mkparents(p)
        S["fs"][p] = {"t": "f", "c": c, "m": time.time()}
        S["raw"] += len(c) - old
        S["dirty"] = True
    return jsonify(ok=True)


@app.route("/api/mkdir", methods=["POST"])
def mkdir():
    p = norm(request.json.get("p"))
    with L:
        if p in S["fs"]:
            return err("Existe déjà")
        mkparents(p)
        S["fs"][p] = {"t": "d", "m": time.time()}
        S["dirty"] = True
    return jsonify(ok=True)


@app.route("/api/rm", methods=["POST"])
def rm():
    p = norm(request.json.get("p"))
    if p in ("/", "/System"):
        return err("Protégé")
    with L:
        for k in [k for k in S["fs"] if k == p or k.startswith(p + "/")]:
            del S["fs"][k]
        S["dirty"] = True
    return jsonify(ok=True)


@app.route("/api/mv", methods=["POST"])
def mv():
    a, b = norm(request.json.get("a")), norm(request.json.get("b"))
    with L:
        if a in ("/", "/System") or a not in S["fs"] or b in S["fs"] or b.startswith(a + "/"):
            return err("Déplacement impossible")
        mkparents(b)
        for k in sorted(k for k in S["fs"] if k == a or k.startswith(a + "/")):
            S["fs"][b + k[len(a):]] = S["fs"].pop(k)
        S["dirty"] = True
    return jsonify(ok=True)


@app.route("/api/backup")
def backup():
    """Télécharge une copie du disque virtuel DracoOS (administrateur uniquement)."""
    if not g.user.get("admin"):
        return err("Sauvegarde complète réservée à l’administrateur", 403)
    if not os.path.isfile(DB):
        return err("Aucun disque DracoOS à sauvegarder", 404)
    try:
        shutdown_save()
        stamp = time.strftime("%Y%m%d-%H%M%S")
        return send_file(DB, as_attachment=True, download_name="DracoOS-sauvegarde-%s.db" % stamp,
                         mimetype="application/octet-stream", max_age=0)
    except Exception as e:
        return err("Sauvegarde impossible : " + str(e), 500)


@app.route("/api/shutdown", methods=["POST"])
def shutdown():
    try:
        shutdown_save()
    except Exception as e:
        return err(str(e), 500)
    if not (request.json or {}).get("restart"):
        threading.Timer(0.4, lambda: os._exit(0)).start()
    return jsonify(ok=True)


@app.route("/proxy", methods=["GET", "POST"])
def proxy():
    u = request.args.get("u", "")
    if not requests:
        return "Navigateur indisponible : pip install requests", 500
    if not u.startswith(("http://", "https://")):
        return "URL invalide", 400
    try:
        r = requests.request(
            request.method, u, timeout=15,
            data=request.form.to_dict(flat=False) if request.method == "POST" else None,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
                     "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8"})
    except Exception as e:
        return "Erreur : " + html.escape(str(e)), 502
    ct, body = r.headers.get("Content-Type", "text/html"), r.content
    if "text/html" in ct:
        P = request.host_url + "proxy?u="
        inj = ('<base href="%s"><script>const P=%s;document.addEventListener("click",e=>{const a=e.target.closest("a[href]");'
               'if(a&&/^https?:/.test(a.href)){e.preventDefault();location=P+encodeURIComponent(a.href)}},true);'
               'document.addEventListener("submit",e=>{const f=e.target,u=new URL(f.getAttribute("action")||"",document.baseURI);if((f.method||"get").toLowerCase()!="get"){f.action=P+encodeURIComponent(u.href);return}'
               'e.preventDefault();new FormData(f).forEach((v,k)=>u.searchParams.set(k,v));'
               'location=P+encodeURIComponent(u.href)},true)</script>') % (html.escape(r.url), json.dumps(P))
        body = inj.encode() + body
    return Response(body, content_type=ct)


@app.route("/api/resize", methods=["POST"])
def resize():
    mb = int((request.json or {}).get("mb", 0))
    if mb < 64 or mb > 1024 * 1024:
        return err("Taille invalide (min 64 Mo)")
    new = mb << 20
    with L:
        old = S["total"]
        if new == old:
            return jsonify(ok=True)
        raw = len(json.dumps(S["fs"], separators=(",", ":")).encode())
        if raw > new - HDR - 4096:
            return err("Les données (%d Mo) ne tiennent pas dans cette taille" % max(1, raw >> 20))
        cur = os.path.getsize(DB)
        if new > cur and shutil.disk_usage(BASE).free < new - cur + (64 << 20):
            return err("Pas assez d'espace libre sur le disque réel")
        try:
            if new > cur:
                with open(DB, "r+b") as f:
                    f.seek(cur)
                    chunk, n = b"\0" * (8 << 20), new - cur
                    while n > 0:
                        f.write(chunk[:min(n, len(chunk))])
                        n -= len(chunk)
                    f.flush()
                    os.fsync(f.fileno())
            S["total"] = new
            save(False)
            with open(DB, "r+b") as f:
                f.truncate(new)
        except Exception as e:
            S["total"] = old
            return err(str(e), 500)
    return jsonify(ok=True)


@app.route("/api/reset", methods=["POST"])
def reset():
    with L:
        S["fs"] = default_fs()
        save(False)
    return jsonify(ok=True)


PYHELP = (
    "import json,urllib.request as _r,urllib.parse as _p\n"
    "_B='http://127.0.0.1:%d/api/'\n"
    "def read(path): return json.load(_r.urlopen(_B+'read?p='+_p.quote(path)))['c']\n"
    "def ls(path='/'): return json.load(_r.urlopen(_B+'ls?p='+_p.quote(path)))['items']\n"
    "def write(path,content):\n"
    "    q=_r.Request(_B+'write',json.dumps({'p':path,'c':content}).encode(),{'Content-Type':'application/json'})\n"
    "    return json.load(_r.urlopen(q))\n"
) % PORT


@app.before_request
def guard():
    o = request.headers.get("Origin", "")
    if request.path.startswith("/api/") and request.method == "POST" and o \
            and not o.startswith(("http://127.0.0.1", "http://localhost")):
        return err("Origine refusée", 403)


def users():
    try:
        return json.loads(S["fs"]["/System/users.json"]["c"])
    except Exception:
        return {}


def put_users(us):
    S["fs"]["/System/users.json"] = {"t": "f", "c": json.dumps(us), "m": time.time()}
    S["dirty"] = True


def hpw(pw, salt):
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 120000).hex()


def migrate(n):
    h, fs = "/Users/" + n, S["fs"]
    mv_ = {}
    for k in list(fs):
        a = k.split("/")[1]
        if a in HOMEDIRS:
            mv_[k] = h + k
        elif k in ("/System/settings.json", "/System/commands.json"):
            mv_[k] = h + k[7:]
        elif k == "/System/Apps" or k.startswith("/System/Apps/"):
            mv_[k] = h + "/Apps" + k[len("/System/Apps"):]
    for a, b in mv_.items():
        fs[b] = fs.pop(a)


def mkhome(n):
    t, h = time.time(), "/Users/" + n
    for d in ["/Users", h] + [h + "/" + x for x in HOMEDIRS + ("Apps",)]:
        S["fs"].setdefault(d, {"t": "d", "m": t})
    cfg = {"theme": "dark", "accent": "#4f8cff", "compress": True,
           "wallpaper": "linear-gradient(135deg,#1e3c72,#2a5298 50%,#7b4397)"}
    S["fs"].setdefault(h + "/settings.json", {"t": "f", "c": json.dumps(cfg), "m": t})
    S["fs"].setdefault(h + "/Documents/Bienvenue.txt", {"t": "f", "c": "Bienvenue dans DracoOS, %s !" % n, "m": t})


OPEN = {"/api/status", "/api/init", "/api/login", "/api/register"}


@app.errorhandler(403)
def e403(e):
    return err("Accès refusé", 403)


@app.before_request
def auth():
    if not request.path.startswith("/api/") or request.path in OPEN:
        return
    n = SESS.get(request.headers.get("X-Token", ""))
    u = users().get(n) if n else None
    if not u:
        return err("Non connecté", 401)
    g.user = {"name": n, "admin": bool(u.get("admin"))}
    if request.path in ("/api/resize", "/api/reset", "/api/py") and not g.user["admin"]:
        return err("Réservé à l'administrateur", 403)


@app.route("/api/register", methods=["POST"])
def register():
    d = request.json or {}
    n, pw = str(d.get("name", "")).strip(), str(d.get("password", ""))
    if S["fs"] is None:
        return err("Disque non créé")
    first = not users()
    if not first:
        me = users().get(SESS.get(request.headers.get("X-Token", "")), {})
        if not me.get("admin"):
            return err("Réservé à l'administrateur", 403)
    if not (2 <= len(n) <= 20 and n.replace("-", "").replace("_", "").isalnum()):
        return err("Nom invalide (2 à 20 lettres/chiffres)")
    if len(pw) < 4:
        return err("Mot de passe trop court (4 minimum)")
    with L:
        us = users()
        if n.lower() in [x.lower() for x in us]:
            return err("Ce nom existe déjà")
        salt = secrets.token_hex(8)
        us[n] = {"salt": salt, "hash": hpw(pw, salt), "admin": first}
        put_users(us)
        if first:
            migrate(n)
        mkhome(n)
        save(False)
    return jsonify(ok=True)


@app.route("/api/login", methods=["POST"])
def login():
    d = request.json or {}
    n, u = str(d.get("name", "")), users().get(str(d.get("name", "")))
    if not u or not secrets.compare_digest(hpw(str(d.get("password", "")), u["salt"]), u["hash"]):
        time.sleep(0.6)
        return err("Nom ou mot de passe incorrect", 401)
    tok = secrets.token_hex(16)
    SESS[tok] = n
    return jsonify(token=tok, name=n, admin=bool(u.get("admin")))


@app.route("/api/logout", methods=["POST"])
def logout():
    SESS.pop(request.headers.get("X-Token", ""), None)
    return jsonify(ok=True)


@app.route("/api/whoami")
def whoami():
    return jsonify(name=g.user["name"], admin=g.user["admin"])


@app.route("/api/userlist")
def userlist():
    if not g.user["admin"]:
        return err("Réservé à l'administrateur", 403)
    return jsonify(items=[{"name": n, "admin": bool(v.get("admin"))} for n, v in users().items()])


@app.route("/api/deluser", methods=["POST"])
def deluser():
    if not g.user["admin"]:
        return err("Réservé à l'administrateur", 403)
    n = str((request.json or {}).get("name", ""))
    us = users()
    if n not in us or us[n].get("admin"):
        return err("Suppression impossible")
    with L:
        del us[n]
        put_users(us)
        for k in [k for k in S["fs"] if k == "/Users/" + n or k.startswith("/Users/" + n + "/")]:
            del S["fs"][k]
        for t in [t for t, v in SESS.items() if v == n]:
            del SESS[t]
        save(False)
    return jsonify(ok=True)


@app.route("/api/passwd", methods=["POST"])
def passwd():
    d = request.json or {}
    us = users()
    u = us[g.user["name"]]
    if not secrets.compare_digest(hpw(str(d.get("old", "")), u["salt"]), u["hash"]):
        return err("Ancien mot de passe incorrect")
    new = str(d.get("new", ""))
    if len(new) < 4:
        return err("Mot de passe trop court (4 minimum)")
    with L:
        u["salt"] = secrets.token_hex(8)
        u["hash"] = hpw(new, u["salt"])
        put_users(us)
        save(False)
    return jsonify(ok=True)


PYHELP = (
    "import json,os,urllib.request as _r,urllib.parse as _p\n"
    "_B='http://127.0.0.1:%d/api/'\n"
    "_H={'X-Token':os.environ.get('DRACO_TOKEN',''),'Content-Type':'application/json'}\n"
    "def _g(u,d=None): return json.load(_r.urlopen(_r.Request(_B+u,None if d is None else json.dumps(d).encode(),_H)))\n"
    "def read(path): return _g('read?p='+_p.quote(path))['c']\n"
    "def ls(path='/'): return _g('ls?p='+_p.quote(path))['items']\n"
    "def write(path,content): return _g('write',{'p':path,'c':content})\n"
) % PORT


@app.route("/api/py", methods=["POST"])
def run_py():
    import subprocess, tempfile
    d = request.json or {}
    t0 = time.time()
    with tempfile.TemporaryDirectory() as t:
        with open(os.path.join(t, "draco.py"), "w", encoding="utf-8") as f:
            f.write(PYHELP)
        with open(os.path.join(t, "main.py"), "w", encoding="utf-8") as f:
            f.write(str(d.get("code", "")))
        try:
            r = subprocess.run([sys.executable, "-u", "main.py"], cwd=t, input=str(d.get("stdin", "")),
                               capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=30, env={**os.environ, "PYTHONIOENCODING": "utf-8",
                                    "DRACO_TOKEN": request.headers.get("X-Token", "")})
            out, e = r.stdout, r.stderr
        except subprocess.TimeoutExpired:
            out, e = "", "Délai dépassé (30 s) : programme arrêté."
    return jsonify(out=out[:200000], err=e[:50000], ms=int((time.time() - t0) * 1000))


def main():
    if os.path.exists(DB):
        load()
    atexit.register(lambda: S["fs"] is not None and shutdown_save())
    threading.Thread(target=autosave, daemon=True).start()
    threading.Thread(target=lambda: app.run(port=PORT, threaded=True, use_reloader=False), daemon=True).start()
    time.sleep(0.8)
    url = "http://127.0.0.1:%d" % PORT
    try:
        import webview
        webview.create_window("DracoOS", url, width=1280, height=800, min_size=(800, 500))
        webview.start()
    except ImportError:
        import webbrowser
        webbrowser.open(url)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    try:
        shutdown_save()
    except Exception:
        pass


if __name__ == "__main__":
    main()