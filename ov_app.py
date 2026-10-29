#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ordinem servare: приложение. Локальный сервер на 127.0.0.1 + окно Edge в режиме приложения.

Интерфейс — ui.html рядом с этим файлом. Все запросы к API требуют токен, который
вписывается в страницу при выдаче, — чужие сайты не могут управлять программой.
Сервер завершается сам, когда окно закрыто (нет пинга от страницы 60 секунд).
"""

import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import ov_audit  # noqa: E402
import ov_norms  # noqa: E402

APPDIR = os.path.join(os.environ.get("APPDATA", HERE), "ОВ-Проверка")
SETTINGS = os.path.join(APPDIR, "app.json")
PORTFILE = os.path.join(APPDIR, "port.txt")
UPLOADS = os.path.join(APPDIR, "Загруженные PDF")
VERSION = "0.3"

DEFAULT = {"files": [], "code": "", "name": "", "stamp": True, "sections": True, "callouts": True,
           "tech": True, "ai": False, "scale": "Авто", "tol": 5, "pages": "", "outdir": "",
           "norms_dir": ov_norms.DEFAULT_DIR, "api_key": "", "model": "claude-opus-5-5", "effort": "high"}


class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.s = dict(DEFAULT)
        try:
            with open(SETTINGS, encoding="utf-8-sig") as fo:
                self.s.update(json.load(fo))
        except (OSError, ValueError):
            pass
        self.s["files"] = [f for f in self.s["files"] if os.path.exists(f)]
        self.norms = ov_norms.Norms(self.s["norms_dir"])
        self.norms_ready = False
        self.job = {"running": False, "log": [], "result": None, "error": None}
        self.last_ping = time.time()

    def save(self):
        os.makedirs(APPDIR, exist_ok=True)
        with open(SETTINGS, "w", encoding="utf-8") as fo:
            json.dump(self.s, fo, ensure_ascii=False, indent=1)

    def public(self):
        s = dict(self.s)
        key = s.pop("api_key", "")
        s["api_key_set"] = bool(key) or bool(os.environ.get("ANTHROPIC_API_KEY"))
        s["api_key_hint"] = ("••••" + key[-4:]) if key else ("из переменной окружения" if s["api_key_set"] else "")
        s["files_info"] = [file_info(f) for f in s["files"]]
        return s

    def load_norms(self, force=False):
        self.norms = ov_norms.Norms(self.s["norms_dir"])
        self.norms.load(force=force)
        self.norms_ready = True


def file_info(path):
    info = {"path": path, "name": os.path.basename(path), "size": 0, "pages": None}
    try:
        info["size"] = os.path.getsize(path)
        import pymupdf
        info["pages"] = pymupdf.open(path).page_count
    except Exception:
        pass
    return info


STATE = None
TOKEN = secrets.token_urlsafe(24)


def pick_files(kind="files", initial=None):
    """Системный диалог выбора файлов / папки (в отдельном потоке запроса)."""
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        if kind == "dir":
            r = filedialog.askdirectory(title="Выберите папку", initialdir=initial or None, parent=root)
            return [r] if r else []
        r = filedialog.askopenfilenames(title="Выберите PDF", filetypes=[("PDF", "*.pdf")],
                                        initialdir=initial or None, parent=root)
        return list(r)
    finally:
        root.destroy()


def run_job(opts, files):
    st = STATE
    job = st.job

    def log(msg):
        job["log"].append(str(msg))

    try:
        if not st.norms_ready:
            log("Загружаю нормативы…")
            st.load_norms()
        res = ov_audit.run(files, opts, log=log, norms=st.norms)
        job["result"] = res
    except Exception as e:      # всё показываем в окне, а не в консоли
        job["error"] = str(e)
        log("Ошибка: " + str(e))
        log(traceback.format_exc())
    finally:
        job["running"] = False


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else (
            json.dumps(body, ensure_ascii=False, default=str).encode("utf-8") if not isinstance(body, str)
            else body.encode("utf-8"))
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _auth(self):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
        if self.headers.get("X-Token") != TOKEN and q.get("t") != TOKEN:     # t= — для картинок <img>
            self._send(403, {"error": "нет доступа"})
            return False
        return True

    def _finding(self, fid):
        res = STATE.job.get("result") or {}
        src, _, n = (fid or "").partition(":")
        if src == "c":
            items = (res.get("claude") or {}).get("findings", [])
        else:
            items = res.get("findings", [])
        return next((f for f in items if str(f.get("n")) == n), None)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        return raw

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path in ("/", "/index.html"):
            with open(os.path.join(HERE, "ui.html"), encoding="utf-8") as fo:
                page = fo.read().replace("{{TOKEN}}", TOKEN).replace("{{VERSION}}", VERSION)
            return self._send(200, page, "text/html; charset=utf-8")
        if u.path == "/api/alive":
            return self._send(200, {"ok": True})
        if not self._auth():
            return
        q = dict(urllib.parse.parse_qsl(u.query))
        st = STATE
        if u.path == "/api/state":
            st.last_ping = time.time()
            return self._send(200, {"settings": st.public(), "norms": st.norms.summary() if st.norms_ready else None,
                                    "job": {"running": st.job["running"], "nlog": len(st.job["log"])}})
        if u.path == "/api/job":
            st.last_ping = time.time()
            since = int(q.get("since", 0))
            j = st.job
            return self._send(200, {"running": j["running"], "log": j["log"][since:], "n": len(j["log"]),
                                    "error": j["error"], "result": j["result"] if not j["running"] else None})
        if u.path == "/api/preview":
            f = self._finding(q.get("id"))
            k = int(q.get("k", 0))
            if not f or k >= len(f.get("locs", [])):
                return self._send(404, {"error": "нет места"})
            l = f["locs"][k]
            import ov_marks
            pdf = l.get("pdf") or l["path"]
            if not os.path.exists(pdf):
                return self._send(404, {"error": "PDF не найден"})
            png = ov_marks.preview_png(pdf, l["page"], l.get("x"), l.get("y"))
            return self._send(200, png, "image/png")
        if u.path == "/api/norms/search":
            if not st.norms_ready:
                st.load_norms()
            return self._send(200, st.norms.search(q.get("q", ""), k=25, code=q.get("code") or None))
        return self._send(404, {"error": "нет такого адреса"})

    def do_POST(self):
        if not self._auth():
            return
        u = urllib.parse.urlparse(self.path)
        st = STATE
        raw = self._body()
        try:
            data = json.loads(raw or b"{}") if u.path != "/api/upload" else {}
        except ValueError:
            return self._send(400, {"error": "неверный запрос"})

        if u.path == "/api/settings":
            with st.lock:
                for k in DEFAULT:
                    if k in data and k not in ("files",):
                        st.s[k] = data[k]
                st.save()
            return self._send(200, {"settings": st.public()})

        if u.path == "/api/files/pick":
            init = os.path.dirname(st.s["files"][-1]) if st.s["files"] else None
            for f in pick_files("files", init):
                f = os.path.abspath(f)
                if f not in st.s["files"]:
                    st.s["files"].append(f)
            st.save()
            return self._send(200, {"settings": st.public()})

        if u.path == "/api/files/remove":
            st.s["files"] = [f for f in st.s["files"] if f != data.get("path")]
            st.save()
            return self._send(200, {"settings": st.public()})

        if u.path == "/api/files/clear":
            st.s["files"] = []
            st.save()
            return self._send(200, {"settings": st.public()})

        if u.path == "/api/upload":
            name = os.path.basename(urllib.parse.unquote(self.headers.get("X-Name", "file.pdf")))
            if not name.lower().endswith(".pdf"):
                return self._send(400, {"error": "только PDF"})
            os.makedirs(UPLOADS, exist_ok=True)
            path = os.path.join(UPLOADS, name)
            with open(path, "wb") as fo:
                fo.write(raw)
            if path not in st.s["files"]:
                st.s["files"].append(path)
            st.save()
            return self._send(200, {"settings": st.public()})

        if u.path == "/api/pick_dir":
            r = pick_files("dir", data.get("initial"))
            return self._send(200, {"path": r[0] if r else ""})

        if u.path == "/api/detect":
            if not st.s["files"]:
                return self._send(400, {"error": "Сначала добавьте PDF."})
            import ov_album
            g = ov_album.Album(st.s["files"][:1]).project_guess()
            return self._send(200, g)

        if u.path == "/api/norms/reload":
            if data.get("dir"):
                st.s["norms_dir"] = data["dir"]
                st.save()
            st.load_norms(force=bool(data.get("force")))
            return self._send(200, {"norms": st.norms.summary(), "settings": st.public()})

        if u.path == "/api/run":
            if st.job["running"]:
                return self._send(409, {"error": "Проверка уже идёт."})
            if not st.s["files"]:
                return self._send(400, {"error": "Добавьте PDF для проверки."})
            s = st.s
            scale = None if str(s["scale"]).startswith("Авто") else int(str(s["scale"]).split(":")[-1])
            opts = {"code": s["code"], "name": s["name"], "stamp": s["stamp"], "sections": s["sections"],
                    "callouts": s["callouts"], "tech": s["tech"], "ai": s["ai"], "scale": scale, "tol": float(s["tol"]),
                    "pages": s["pages"] or None, "outdir": s["outdir"] or None, "norms_dir": s["norms_dir"],
                    "api_key": s["api_key"], "model": s["model"], "effort": s["effort"]}
            st.job = {"running": True, "log": [], "result": None, "error": None}
            threading.Thread(target=run_job, args=(opts, list(s["files"])), daemon=True).start()
            return self._send(200, {"ok": True})

        if u.path == "/api/claude/reload":
            res = st.job.get("result")
            if not res:
                return self._send(400, {"error": "Сначала выполните проверку."})
            import ov_audit as A
            return self._send(200, A.reload_claude(res))

        if u.path == "/api/open":
            res = st.job.get("result")
            what = data.get("what")
            target = None
            if what == "page":
                f = self._finding(data.get("id"))
                k = int(data.get("k", 0))
                if not f or k >= len(f.get("locs", [])):
                    return self._send(404, {"error": "Место замечания не определено."})
                l = f["locs"][k]
                open_pdf_page(l.get("pdf") or l["path"], l["page"] + 1)
                return self._send(200, {"ok": True})
            if res:
                if what == "report":
                    target = res["report"]
                elif what == "folder":
                    target = os.path.dirname(res["report"])
                elif what == "pdf":
                    cf = list(res.get("comment_files", {}).values())
                    target = cf[0] if cf else None
                elif what == "sections":
                    sf = list(res.get("section_files", {}).values())
                    target = sf[0]["html"] if sf else None
            if what == "norms_dir":
                target = st.s["norms_dir"]
            if what in ("equip", "layers"):
                import ov_config
                ov_config.get()                     # создаст файлы, если их ещё нет
                target = ov_config.EQUIP_CSV if what == "equip" else ov_config.LAYERS_CSV
            if not target or not os.path.exists(target):
                return self._send(404, {"error": "Файл не найден — выполните проверку."})
            os.startfile(target)
            return self._send(200, {"ok": True})

        if u.path == "/api/ping":
            st.last_ping = time.time()
            return self._send(200, {"ok": True})

        return self._send(404, {"error": "нет такого адреса"})


def find_edge():
    for p in (os.path.join(os.environ.get("ProgramFiles(x86)", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
              os.path.join(os.environ.get("ProgramFiles", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
              shutil.which("msedge")):
        if p and os.path.exists(p):
            return p
    return None


def open_pdf_page(pdf, page):
    """PDF на нужной странице: Edge понимает #page=N; без Edge — просто открыть файл."""
    edge = find_edge()
    if edge:
        uri = "file:///" + urllib.parse.quote(os.path.abspath(pdf).replace("\\", "/"), safe="/:") + f"#page={page}"
        subprocess.Popen([edge, uri])
    else:
        os.startfile(pdf)


def open_window(url):
    edge = find_edge()
    if edge:
        prof = os.path.join(APPDIR, "edge-profile")
        subprocess.Popen([edge, f"--app={url}", "--window-size=1180,860", f"--user-data-dir={prof}",
                          "--no-first-run", "--disable-features=Translate"])
    else:
        webbrowser.open(url)


def running_instance():
    try:
        with open(PORTFILE) as fo:
            port = int(fo.read().strip())
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/alive", timeout=1) as r:
            if r.status == 200:
                return port
    except Exception:
        pass
    return None


def main():
    global STATE
    os.makedirs(APPDIR, exist_ok=True)
    port = running_instance()
    if port:
        open_window(f"http://127.0.0.1:{port}/")
        return
    STATE = State()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    with open(PORTFILE, "w") as fo:
        fo.write(str(port))
    threading.Thread(target=STATE.load_norms, daemon=True).start()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    if "--no-window" not in sys.argv:
        open_window(f"http://127.0.0.1:{port}/")
    else:
        print(f"http://127.0.0.1:{port}/")
    # окно закрыто — нет пингов; во время проверки не выходим
    while True:
        time.sleep(5)
        if not STATE.job["running"] and time.time() - STATE.last_ping > 60:
            break
    srv.shutdown()
    try:
        os.remove(PORTFILE)
    except OSError:
        pass


if __name__ == "__main__":
    main()
