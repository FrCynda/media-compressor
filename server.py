"""Local web UI for the media compressor. Binds to 127.0.0.1 only. Shows counts/sizes only, never file names or metadata."""
import json, os, secrets, shutil, socket, subprocess, sys, tempfile, threading, time, webbrowser
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FROZEN = getattr(sys, "frozen", False)
HERE = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))  # bundled files live in _MEIPASS
LOCAL = os.environ.get("LOCALAPPDATA", "")
if FROZEN:  # tools are bundled inside the exe
    os.environ["IC_EXIFTOOL"] = os.path.join(HERE, "bin", "exiftool", "ExifTool.exe")
    os.environ["IC_OXIPNG"] = os.path.join(HERE, "bin", "oxipng.exe")
else:
    os.environ["IC_EXIFTOOL"] = os.environ.get("IC_EXIFTOOL") or shutil.which("exiftool") or os.path.join(LOCAL, r"Programs\ExifTool\ExifTool.exe")
    os.environ["IC_OXIPNG"] = os.environ.get("IC_OXIPNG") or shutil.which("oxipng") or os.path.join(LOCAL, r"Microsoft\WinGet\Links\oxipng.exe")
import worker  # noqa: E402  (needs the env vars above)
import video   # noqa: E402

TOKEN = secrets.token_urlsafe(16)
PING = {"last": 0.0, "bye_at": 0.0, "seen": False}   # window heartbeat / close signal
LOCK = threading.Lock()
CANCEL = threading.Event()
CUR = {}   # per-file progress (0..1) of videos currently encoding
BLANK = dict(status="idle", kind="images", total=0, done=0, partial=0.0, start=0.0, end=0.0, in_bytes=0, out_bytes=0,
             converted=0, verified=0, noparams=0, flattened=0, skipped=0, problems=0, overtarget=0, error="")
S = dict(BLANK)
EST = {"status": "idle"}


def list_files(in_dir, out_dir, kind):
    out_dir = os.path.abspath(out_dir) if out_dir else None
    exts = {"images": (".png",), "videos": video.VIDEO_EXT}.get(kind, (".png",) + video.VIDEO_EXT)
    found = []
    for root, dirs, files in os.walk(in_dir):
        if out_dir and out_dir != os.path.abspath(in_dir) and os.path.abspath(root).startswith(out_dir):
            dirs[:] = []
            continue
        found += [os.path.join(root, n) for n in files if n.lower().endswith(exts) and ".part." not in n.lower()]
    return found


def real_dst(src, dst):
    """Where the output actually landed (a kept original keeps its own extension)."""
    if not os.path.exists(dst):
        alt = os.path.splitext(dst)[0] + os.path.splitext(src)[1]
        return alt if os.path.exists(alt) else dst
    return dst


def copy_dates(st, dst):
    """Give dst the created / modified / accessed times in stat result st (Windows)."""
    try:
        import ctypes
        from ctypes import wintypes as w
        ft = lambda ns: ctypes.c_ulonglong(ns // 100 + 116444736000000000)
        k = ctypes.windll.kernel32
        k.CreateFileW.restype = w.HANDLE
        h = k.CreateFileW(dst, 0x100, 7, None, 3, 0x02000000, None)   # WRITE_ATTRIBUTES, share all, open existing, backup semantics
        if h in (None, w.HANDLE(-1).value):
            return
        c, a, m = ft(getattr(st, "st_birthtime_ns", st.st_ctime_ns)), ft(st.st_atime_ns), ft(st.st_mtime_ns)
        k.SetFileTime(w.HANDLE(h), ctypes.byref(c), ctypes.byref(a), ctypes.byref(m))
        k.CloseHandle(w.HANDLE(h))
    except Exception:  # noqa  (dates are best-effort)
        pass


def pick(title, start=""):
    r = subprocess.run(["powershell", "-STA", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", os.path.join(HERE, "pick.ps1"),
                        "-Title", title, "-Start", start], capture_output=True, stdin=subprocess.DEVNULL, creationflags=0x08000000)
    return r.stdout.decode("utf-8", "replace").strip()


def tally(st, src, dst):
    """Update counters for one finished file. Caller holds LOCK."""
    S["done"] += 1
    if st.startswith("skipped"):
        S["skipped"] += 1
    elif st.startswith("ok"):
        S["converted"] += 1
        S["verified"] += ("noparams" not in st)
        S["noparams"] += ("noparams" in st)
        S["flattened"] += st.endswith("_flat")
        S["overtarget"] += (st == "ok_over_target")
        try:
            S["in_bytes"] += os.path.getsize(src)
            S["out_bytes"] += os.path.getsize(dst)
        except OSError:
            pass
        return True
    elif st != "cancelled":
        S["problems"] += 1
    return False


def spread(items, k):
    """k items spread evenly across the size-sorted list, so small and large files are both represented."""
    items = sorted(items, key=lambda f: os.path.getsize(f))
    k = min(k, len(items))
    return [items[int((i + 0.5) * len(items) / k)] for i in range(k)]


def estimate(cfg):
    """Test-encode a few sample files with the chosen options on THIS machine and extrapolate."""
    tmp = tempfile.mkdtemp(prefix="mc_est_")
    try:
        in_dir, out_dir = os.path.abspath(cfg["input"]), os.path.abspath(cfg.get("output") or cfg["input"])
        kind = cfg.get("kind") if cfg.get("kind") in ("images", "videos", "both") else "images"
        files = list_files(in_dir, out_dir, kind)
        res = dict(status="done", images=None, videos=None, sec=0.0, in_bytes=0, out_bytes=0)
        cpu = os.cpu_count() or 4
        imgs = [f for f in files if f.lower().endswith(".png")] if kind != "videos" else []
        vids = [f for f in files if f.lower().endswith(video.VIDEO_EXT)] if kind != "images" else []
        if imgs:
            mode, q = cfg.get("mode", "jpg"), int(cfg.get("quality", 90))
            t = ib = ob = 0
            for i, f in enumerate(spread(imgs, 6)):
                dst = os.path.join(tmp, f"i{i}." + ("jpg" if mode == "jpg" else "png"))
                t0 = time.time()
                worker.work((f, dst, mode, q))
                t += time.time() - t0
                if os.path.exists(dst):
                    ib += os.path.getsize(f)
                    ob += os.path.getsize(dst)
            tot = sum(os.path.getsize(f) for f in imgs)
            if ib:
                par = max(1, min(int(cfg.get("workers") or 1), cpu))
                res["images"] = dict(count=len(imgs), in_bytes=tot, out_bytes=int(tot * ob / ib), sec=t / ib * tot / par)
        if vids:
            vo = video.sanitize(cfg.get("video") or {})
            target = vo["target_mb"] * 1048576
            vo = dict(vo, target_mb=0)   # time the plain encode; target mode is handled below
            t = ib = ob = 0
            for i, f in enumerate(spread(vids, 3)):
                d = video.probe(f)["duration"]
                for j, pos in enumerate((0.15, 0.5, 0.85) if d > 12 else (None,)):   # several spots: content changes over a video
                    clip, od = os.path.join(tmp, f"c{i}_{j}.mp4"), os.path.join(tmp, f"o{i}_{j}")
                    cut = ["-ss", str(max(0, d * pos - 1.5)), "-t", "3"] if pos else []
                    r = subprocess.run([video.FFMPEG, "-nostdin", "-v", "error", *cut, "-i", f, "-map", "0:v:0", "-map", "0:a?", "-c", "copy", "-y", clip],
                                       capture_output=True, stdin=subprocess.DEVNULL, creationflags=0x08000000)
                    if r.returncode or not os.path.exists(clip) or not os.path.getsize(clip):
                        continue
                    t0 = time.time()
                    video.encode(clip, os.path.join(od, "x.mp4"), vo, lambda x: None)
                    t += time.time() - t0
                    made = os.listdir(od) if os.path.isdir(od) else []
                    if made:
                        ib += video.probe(clip)["duration"] * os.path.getsize(f) / d   # what the source itself spends on that many seconds (a stream-copied clip can start early on a keyframe)
                        ob += os.path.getsize(os.path.join(od, made[0]))
            sizes = [os.path.getsize(f) for f in vids]
            tot = sum(sizes)
            if ib:
                ratio = min(1.0, ob / ib)
                out_b, mult = tot * ratio, 1.0
                if target:   # every file is squeezed to the target (never enlarged), using up to 3 encoding passes
                    out_b, mult = sum(min(z, target) for z in sizes), 1.6
                # encoders already use many cores, so extra parallel jobs help only a little
                par = 1 + 0.35 * (max(1, min(int(cfg.get("vworkers") or cfg.get("workers") or 1), 4)) - 1)
                res["videos"] = dict(count=len(vids), in_bytes=tot, out_bytes=int(out_b), sec=t / ib * tot * mult / par)
        for k in ("images", "videos"):
            if res[k]:
                res["sec"] += res[k]["sec"]
                res["in_bytes"] += res[k]["in_bytes"]
                res["out_bytes"] += res[k]["out_bytes"]
        EST.clear()
        EST.update(res)
    except Exception as e:  # noqa
        EST.clear()
        EST.update(status="error", error=type(e).__name__)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def job(cfg):
    in_dir, out_dir = os.path.abspath(cfg["input"]), os.path.abspath(cfg["output"])
    kind = cfg.get("kind") if cfg.get("kind") in ("images", "videos", "both") else "images"
    try:
        files = list_files(in_dir, out_dir, kind)
        os.makedirs(out_dir, exist_ok=True)
        vids = tuple(video.VIDEO_EXT)
        overwrite, delete_orig = bool(cfg.get("overwrite")), bool(cfg.get("delete_orig"))
        dates = {}
        if cfg.get("keep_dates"):   # read up front: an in-place overwrite replaces the source's own dates
            for f in files:
                try:
                    dates[f] = os.stat(f)
                except OSError:
                    pass
        ijobs, vjobs = [], []
        if kind != "videos":
            mode, quality, workers = cfg["mode"], int(cfg["quality"]), int(cfg["workers"])
            ext = ".jpg" if mode == "jpg" else ".png"
            ijobs = [(s, os.path.join(out_dir, os.path.splitext(os.path.relpath(s, in_dir))[0] + ext), mode, quality) for s in files if s.lower().endswith(".png")]
        if kind != "images":
            vo = video.sanitize(cfg.get("video") or {})
            ext = "." + vo["container"]
            vworkers = max(1, min(4, int(cfg.get("vworkers") or cfg.get("workers") or 1)))
            vjobs = [(s, os.path.join(out_dir, os.path.splitext(os.path.relpath(s, in_dir))[0] + ext)) for s in files if s.lower().endswith(vids)]
        jobs = ijobs + vjobs
        with LOCK:
            S.update(BLANK, kind=kind, status="running", total=len(jobs), start=time.time())
        video.STOP.clear()
        with open(os.path.join(out_dir, "compress_log.txt"), "w", encoding="utf-8") as log:
            def finish(src, dst, st):
                with LOCK:
                    if st.startswith("ok") or st.startswith("meta_mismatch"):
                        real = real_dst(src, dst)
                        if src in dates:
                            copy_dates(dates[src], real)
                        if delete_orig and st.startswith("ok") and os.path.exists(real) and os.path.abspath(real).lower() != os.path.abspath(src).lower():
                            try:
                                os.remove(src)
                            except OSError:
                                pass
                    tally(st, src, dst)
                    if st not in ("skipped_exists", "cancelled") and not st.startswith("ok"):
                        log.write(f"{st}\t{os.path.relpath(src, in_dir)}\n")
            if ijobs:
                with ProcessPoolExecutor(workers) as ex:
                    futs = {ex.submit(worker.work, j + (overwrite,)): j for j in ijobs}
                    for f in as_completed(futs):
                        if CANCEL.is_set():
                            ex.shutdown(wait=False, cancel_futures=True)
                            break
                        try:
                            src, st = f.result()
                        except Exception as e:  # noqa
                            src, st = futs[f][0], "error:" + type(e).__name__
                        finish(src, futs[f][1], st)
            if vjobs:
                def one(i, src, dst):
                    if CANCEL.is_set():
                        return
                    if os.path.exists(dst) and not overwrite:
                        return finish(src, dst, "skipped_exists")
                    def prog(x):
                        with LOCK:
                            CUR[i] = x
                    try:
                        st = video.encode(src, dst, vo, prog)
                    except Exception as e:  # noqa
                        st = "error:" + type(e).__name__
                    with LOCK:
                        CUR.pop(i, None)
                    if st != "cancelled":
                        finish(src, dst, st)
                with ThreadPoolExecutor(vworkers) as ex:
                    for i, (s, d) in enumerate(vjobs):
                        ex.submit(one, i, s, d)
        with LOCK:
            CUR.clear()
            S.update(status="cancelled" if CANCEL.is_set() else "done", end=time.time())
    except Exception as e:  # noqa
        with LOCK:
            S.update(status="error", error=type(e).__name__, end=time.time())


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json"):
        b = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def authed(self):
        tok = self.headers.get("X-Token") or (self.path.split("t=", 1)[1] if "t=" in self.path else "")  # sendBeacon can't set headers
        ok = tok == TOKEN and self.headers.get("Host", "").split(":")[0] in ("127.0.0.1", "localhost")
        if not ok:
            self.send(403, {"error": "forbidden"})
        return ok

    def do_GET(self):
        if self.path == "/":
            html = open(os.path.join(HERE, "index.html"), encoding="utf-8").read().replace("__TOKEN__", TOKEN)
            return self.send(200, html.encode("utf-8"), "text/html; charset=utf-8")
        if self.path == "/api/state" and self.authed():
            PING.update(last=time.time(), bye_at=0.0, seen=True)
            with LOCK:
                st = dict(S)
                st["partial"] = sum(CUR.values())
            end = st["end"] or time.time()
            st["elapsed"] = (end - st["start"]) if st["start"] else 0
            st["ffmpeg"] = video.available()
            st["defaults"] = video.DEFAULTS
            st["est"] = dict(EST)
            return self.send(200, st)
        if self.path != "/api/state":
            self.send(404, {"error": "not found"})

    def do_POST(self):
        if not self.authed():
            return
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) or b"{}"
        d = json.loads(raw) if raw[:1] == b"{" else {}
        if self.path == "/api/ping":
            PING.update(last=time.time(), bye_at=0.0, seen=True)
            return self.send(200, {"ok": True})
        if self.path.startswith("/api/bye"):   # window closed; a reload sends a ping right after and cancels this
            PING["bye_at"] = time.time() + 4
            return self.send(200, {"ok": True})
        if self.path == "/api/pick":
            return self.send(200, {"path": pick(d.get("title", "Select folder"), d.get("start", ""))})
        if self.path == "/api/scan":
            p = d.get("input", "")
            if not os.path.isdir(p):
                return self.send(200, {"count": 0, "bytes": 0, "valid": False})
            fs = list_files(os.path.abspath(p), d.get("output") or None, (d.get("kind") if d.get("kind") in ("videos", "both") else "images"))
            tot = 0
            for f in fs:
                try:
                    tot += os.path.getsize(f)
                except OSError:
                    pass
            return self.send(200, {"count": len(fs), "bytes": tot, "valid": True})
        if self.path == "/api/start":
            with LOCK:
                if S["status"] == "running":
                    return self.send(409, {"error": "already running"})
            i, o = d.get("input", ""), d.get("output", "")
            if not os.path.isdir(i) or not o:
                return self.send(400, {"error": "invalid folders"})
            if d.get("kind") in ("videos", "both") and not video.available():
                return self.send(400, {"error": "ffmpeg missing"})
            CANCEL.clear()
            with LOCK:
                S.update(BLANK, kind=(d.get("kind") if d.get("kind") in ("videos", "both") else "images"), status="running", start=time.time())
            threading.Thread(target=job, args=(d,), daemon=True).start()
            return self.send(200, {"ok": True})
        if self.path == "/api/estimate":
            if EST.get("status") == "running" or S["status"] == "running":
                return self.send(409, {"error": "busy"})
            if not os.path.isdir(d.get("input", "")) or (d.get("kind") in ("videos", "both") and not video.available()):
                return self.send(400, {"error": "invalid"})
            EST.clear()
            EST["status"] = "running"
            threading.Thread(target=estimate, args=(d,), daemon=True).start()
            return self.send(200, {"ok": True})
        if self.path == "/api/cancel":
            CANCEL.set()
            video.kill_all()
            return self.send(200, {"ok": True})
        self.send(404, {"error": "not found"})


def open_ui(url):
    """Prefer an Edge/Chrome 'app' window (no tabs or address bar); fall back to the default browser."""
    pf, pf86 = os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", "")
    for exe in (os.path.join(pf86, r"Microsoft\Edge\Application\msedge.exe"), os.path.join(pf, r"Microsoft\Edge\Application\msedge.exe"),
                os.path.join(pf, r"Google\Chrome\Application\chrome.exe"), os.path.join(pf86, r"Google\Chrome\Application\chrome.exe")):
        if os.path.isfile(exe):
            subprocess.Popen([exe, f"--app={url}", "--window-size=960,1000", "--no-first-run"], stdin=subprocess.DEVNULL)
            return
    webbrowser.open(url)


def watchdog():
    """Quit when the window is gone: explicit close signal, or no heartbeat for 2 minutes (or never opened)."""
    t0 = time.time()
    while True:
        time.sleep(1)
        now = time.time()
        gone = (PING["bye_at"] and now > PING["bye_at"]) or (PING["seen"] and now - PING["last"] > 120) or (not PING["seen"] and now - t0 > 120)
        if gone:
            CANCEL.set()
            video.kill_all()   # don't leave ffmpeg running in the background
            os._exit(0)


def main():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    url = f"http://127.0.0.1:{port}/"
    print(f"Media Compressor running at {url}", flush=True)
    threading.Thread(target=watchdog, daemon=True).start()
    if "--no-browser" not in sys.argv:
        open_ui(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()   # required for the packaged exe's worker processes
    main()
