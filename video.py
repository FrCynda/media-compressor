"""Video engine (FFmpeg). Never prints or returns file names/metadata; errors go to the caller as short codes."""
import os, shutil, subprocess, tempfile, threading

NOWIN = 0x08000000
FFMPEG = os.environ.get("IC_FFMPEG") or shutil.which("ffmpeg")
FFPROBE = os.environ.get("IC_FFPROBE") or shutil.which("ffprobe")
VIDEO_EXT = (".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".wmv", ".flv", ".mpg", ".mpeg", ".ts")
PROCS, PLOCK, STOP = set(), threading.Lock(), threading.Event()

DEFAULTS = dict(codec="av1", engine="cpu", crf=42, speed="balanced", scale="orig", fps="orig",
                audio="copy", container="mp4", target_mb=0, skip="yes")
CHOICES = dict(codec=("av1", "h265", "h264"), engine=("cpu", "gpu"), speed=("fast", "balanced", "slow"),
               scale=("orig", "2160", "1440", "1080", "720", "480", "360"), fps=("orig", "60", "30"),
               audio=("copy", "aac96", "aac128", "aac192", "opus64", "opus96", "opus128", "none"), container=("mp4", "mkv"), skip=("yes", "no"))


def available():
    return bool(FFMPEG and FFPROBE)


def kill_all():
    STOP.set()
    with PLOCK:
        for p in list(PROCS):
            try:
                p.kill()
            except OSError:
                pass


def sanitize(o):
    """Whitelist the options coming from the browser."""
    r = dict(DEFAULTS)
    for k, ok in CHOICES.items():
        if o.get(k) in ok:
            r[k] = o[k]
    try:
        r["crf"] = max(0, min(63, int(o.get("crf", r["crf"]))))
        r["target_mb"] = max(0.0, min(100000.0, float(o.get("target_mb") or 0)))
    except (TypeError, ValueError):
        pass
    return r


def _run(cmd, capture=True):
    return subprocess.run(cmd, capture_output=capture, stdin=subprocess.DEVNULL, creationflags=NOWIN, text=True, encoding="utf-8", errors="replace")


def _frac(s):
    try:
        a, b = s.split("/")
        return float(a) / float(b) if float(b) else 0.0
    except (ValueError, AttributeError):
        try:
            return float(s)
        except (TypeError, ValueError):
            return 0.0


def probe(src):
    """Technical values only: duration, fps, height, audio bitrate."""
    info = dict(duration=0.0, fps=0.0, height=0, width=0, abr=0, audio=False, codec="", vbr=0)
    r = _run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", src]).stdout.strip()
    try:
        info["duration"] = float(r)
    except ValueError:
        pass
    r = _run([FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=height,avg_frame_rate,codec_name,width,bit_rate", "-of", "csv=p=0", src]).stdout.strip().split(",")
    if len(r) >= 5:   # order: codec_name,width,height,avg_frame_rate,bit_rate
        info["codec"] = r[0]
        r = [r[2], r[3], r[1], r[4]]
        try:
            info["width"], info["vbr"] = int(r[2]), int(r[3])
        except ValueError:
            pass
    if len(r) >= 2:
        try:
            info["height"] = int(r[0])
        except ValueError:
            pass
        info["fps"] = _frac(r[1])
    r = _run([FFPROBE, "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=bit_rate", "-of", "csv=p=0", src]).stdout.strip()
    info["audio"] = r != "" or bool(_run([FFPROBE, "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", src]).stdout.strip())
    try:
        info["abr"] = int(int(r) / 1000)
    except ValueError:
        pass
    return info


def _audio_args(o, info):
    a = o["audio"]
    if a == "none" or not info["audio"]:
        return ["-an"], 0
    if a == "copy":
        return ["-c:a", "copy"], info["abr"] or 128
    if a.startswith("aac"):
        return ["-c:a", "aac", "-b:a", f"{int(a[3:])}k"], int(a[3:])
    return ["-c:a", "libopus", "-b:a", f"{int(a[4:])}k"], int(a[4:])


def _video_args(o, kbps):
    c, sp = o["codec"], o["speed"]
    if o["engine"] == "gpu":
        enc = {"av1": "av1_nvenc", "h265": "hevc_nvenc", "h264": "h264_nvenc"}[c]
        a = ["-c:v", enc, "-preset", {"fast": "p3", "balanced": "p5", "slow": "p7"}[sp]]
        a += ["-rc", "vbr", "-b:v", f"{kbps}k", "-maxrate", f"{int(kbps * 1.5)}k"] if kbps else ["-rc", "vbr", "-cq", str(o["crf"]), "-b:v", "0"]
    else:
        if c == "av1":
            a = ["-c:v", "libsvtav1", "-preset", {"fast": "8", "balanced": "6", "slow": "4"}[sp]]
        elif c == "h265":
            a = ["-c:v", "libx265", "-preset", {"fast": "fast", "balanced": "medium", "slow": "slow"}[sp], "-x265-params", "log-level=error"]
            a += ["-tag:v", "hvc1"] if o["container"] == "mp4" else []
        else:
            a = ["-c:v", "libx264", "-preset", {"fast": "fast", "balanced": "medium", "slow": "slow"}[sp]]
        a += ["-b:v", f"{kbps}k"] if kbps else ["-crf", str(o["crf"])]
    return a + ["-pix_fmt", "yuv420p"]


def _filters(o, info):
    f = []
    if o["scale"] != "orig" and info["height"] > int(o["scale"]):
        f.append(f"scale=-2:{o['scale']}")
    if o["fps"] != "orig" and info["fps"] > int(o["fps"]) + 1:
        f.append(f"fps={o['fps']}")
    return ["-vf", ",".join(f)] if f else []


def _encode_once(src, tmp, o, info, kbps, audio_args, progress, base, span):
    cmd = [FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error", "-progress", "pipe:1", "-y", "-i", src,
           "-map", "0:v:0", "-map", "0:a?", "-fps_mode", "passthrough", *_filters(o, info), *_video_args(o, kbps), *audio_args,
           "-map_metadata", "0"] + (["-movflags", "+faststart"] if o["container"] == "mp4" else []) + [tmp]
    with tempfile.TemporaryFile() as err:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err, stdin=subprocess.DEVNULL, creationflags=NOWIN, text=True)
        with PLOCK:
            PROCS.add(p)
        try:
            for line in p.stdout:
                if line.startswith("out_time_us=") and info["duration"]:
                    try:
                        progress(base + span * min(1.0, int(line.split("=")[1]) / 1e6 / info["duration"]))
                    except ValueError:
                        pass
            p.wait()
        finally:
            with PLOCK:
                PROCS.discard(p)
    return p.returncode


def _keep(src, dst):
    kd = os.path.splitext(dst)[0] + os.path.splitext(src)[1]
    if os.path.abspath(kd).lower() != os.path.abspath(src).lower():   # already in place: nothing to copy
        shutil.copy2(src, kd)


BPP = {"av1": 0.04, "hevc": 0.05, "vp9": 0.05, "h264": 0.07}   # bits per pixel per frame below which a file is already tightly compressed


def _already_small(info, size, o):
    if o["skip"] != "yes" or not (info["vbr"] and info["width"] and info["height"] and info["fps"]):
        return False
    if o["target_mb"] and size > o["target_mb"] * 1048576:
        return False
    rank = {"h264": 0, "hevc": 1, "vp9": 1, "av1": 2}   # only skip when the source codec is already as efficient as the chosen one
    if rank.get(info["codec"], -1) < {"h264": 0, "h265": 1, "av1": 2}[o["codec"]]:
        return False
    return info["vbr"] / (info["width"] * info["height"] * info["fps"]) < BPP.get(info["codec"], 0)


def encode(src, dst, o, progress):
    """Returns a short status string: ok, ok_over_target, cancelled, fail."""
    o = sanitize(o)
    if STOP.is_set():
        return "cancelled"
    info = probe(src)
    audio_args, akbps = _audio_args(o, info)
    ext = "." + o["container"]
    tmp = os.path.splitext(dst)[0] + ".part" + ext
    dst = os.path.splitext(dst)[0] + ext
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if _already_small(info, os.path.getsize(src), o) and o["scale"] == "orig" and o["fps"] == "orig":
        _keep(src, dst)   # keep as is, container unchanged
        progress(1.0)
        return "ok_kept"
    target = int(o["target_mb"] * 1048576) if o["target_mb"] and info["duration"] else 0
    kbps = max(40, int(o["target_mb"] * 8388.608 * 0.93 / info["duration"] - akbps)) if target else None
    tries = 3 if target else 1
    status = "fail"
    try:
        for i in range(tries):
            rc = _encode_once(src, tmp, o, info, kbps, audio_args, progress, i / tries, 1 / tries)
            if rc != 0 and not STOP.is_set() and o["audio"] == "copy" and audio_args != ["-an"]:   # audio can't be copied into this container
                audio_args, akbps = ["-c:a", "aac", "-b:a", "128k"], 128
                rc = _encode_once(src, tmp, o, info, kbps, audio_args, progress, i / tries, 1 / tries)
            if STOP.is_set():
                return "cancelled"
            if rc != 0 or not os.path.exists(tmp):
                return "fail"
            size = os.path.getsize(tmp)
            status = "ok"
            if target and size > target:
                status = "ok_over_target"
                if i < tries - 1:
                    kbps = max(30, int(kbps * target / size * 0.95))
                    continue
            break
        if not target and os.path.getsize(tmp) > 0.9 * os.path.getsize(src) and o["skip"] == "yes" and o["scale"] == "orig" and o["fps"] == "orig":
            _keep(src, dst)   # re-encode saved under 10%: keep the original
            status = "ok_kept"
        else:
            os.replace(tmp, dst)
        progress(1.0)
        return status
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
