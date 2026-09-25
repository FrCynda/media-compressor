"""Batch compressor. Prints ONLY counts/sizes. Per-file problems go to a local log file (never printed)."""
import os, subprocess, sys, time
from concurrent.futures import ProcessPoolExecutor, as_completed
from PIL import Image
import numpy as np

EXIFTOOL, OXIPNG = os.environ["IC_EXIFTOOL"], os.environ["IC_OXIPNG"]
NOWIN = 0x08000000  # CREATE_NO_WINDOW


def run(cmd):
    return subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace", creationflags=NOWIN)


def do_png(src, dst):
    r = run([OXIPNG, "-o", "4", "--strip", "none", "-q", "--out", dst, src])
    return "ok" if r.returncode == 0 and os.path.exists(dst) else "fail"


def do_jpg(src, dst, quality):
    import pyjpegli
    im = Image.open(src)
    params = im.info.get("parameters")  # PNG text chunk (e.g. SD generation info)
    flat = im.mode in ("RGBA", "LA", "P") and "transparency" in im.info or im.mode in ("RGBA", "LA")
    if flat:
        im = im.convert("RGBA")
        im = Image.alpha_composite(Image.new("RGBA", im.size, (255, 255, 255, 255)), im)
    im = im.convert("RGB")
    w, h = im.size
    with open(dst, "wb") as f:
        f.write(pyjpegli.encode(np.ascontiguousarray(np.asarray(im)).tobytes(), w, h, quality))
    # copy metadata: all mappable tags + ICC, and PNG 'Parameters' text into EXIF UserComment
    run([EXIFTOOL, "-m", "-q", "-overwrite_original", "-TagsFromFile", src, "-all:all", "-icc_profile",
         "-EXIF:UserComment<PNG:Parameters", dst])
    if params is None:
        return "ok_noparams" + ("_flat" if flat else "")
    raw = subprocess.run([EXIFTOOL, "-b", "-EXIF:UserComment", dst], capture_output=True, stdin=subprocess.DEVNULL, creationflags=NOWIN).stdout
    same = raw.decode("utf-8", "replace").strip() == params.strip()  # -b keeps newlines exact
    return ("ok" if same else "meta_mismatch") + ("_flat" if flat else "")


def work(args):
    src, dst, mode, quality = args[:4]
    overwrite = len(args) > 4 and args[4]
    base, ext = os.path.splitext(dst)
    tmp = base + ".part" + ext   # written aside, swapped in only on success (so overwriting never risks the original)
    try:
        if os.path.exists(dst) and not overwrite:
            return src, "skipped_exists"
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        st = do_png(src, tmp) if mode == "png" else do_jpg(src, tmp, quality)
        if st != "fail":
            os.replace(tmp, dst)
        return src, st
    except Exception as e:  # noqa
        return src, "error:" + type(e).__name__
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def main():
    in_dir, out_dir, mode, quality, workers = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5])
    in_dir, out_dir = os.path.abspath(in_dir), os.path.abspath(out_dir)
    jobs, other = [], 0
    for root, dirs, files in os.walk(in_dir):
        if os.path.abspath(root).startswith(out_dir):
            dirs[:] = []
            continue
        for n in files:
            if n.lower().endswith(".png"):
                src = os.path.join(root, n)
                rel = os.path.relpath(src, in_dir)
                dst = os.path.join(out_dir, os.path.splitext(rel)[0] + (".jpg" if mode == "jpg" else ".png"))
                jobs.append((src, dst, mode, quality))
            else:
                other += 1
    log = os.path.join(out_dir, "compress_log.txt")
    os.makedirs(out_dir, exist_ok=True)
    print(f"{len(jobs)} PNG files found, {other} other files ignored. Mode={mode}. Workers={workers}", flush=True)
    counts, t0, done, in_b, out_b = {}, time.time(), 0, 0, 0
    with ProcessPoolExecutor(workers) as ex, open(log, "w", encoding="utf-8") as lf:
        futs = [ex.submit(work, j) for j in jobs]
        for f in as_completed(futs):
            src, status = f.result()
            counts[status] = counts.get(status, 0) + 1
            done += 1
            if not status.startswith(("ok", "skipped")):
                lf.write(f"{status}\t{os.path.relpath(src, in_dir)}\n")
            if status.startswith("ok"):
                pass
            if done % 100 == 0 or done == len(jobs):
                el = time.time() - t0
                print(f"  {done}/{len(jobs)}  {el/60:.1f} min elapsed, ~{el/done*(len(jobs)-done)/60:.1f} min left", flush=True)
    for j in jobs:
        if os.path.exists(j[1]):
            in_b += os.path.getsize(j[0]); out_b += os.path.getsize(j[1])
    print("\nRESULT (counts only)")
    for k in sorted(counts):
        print(f"  {k}: {counts[k]}")
    if in_b:
        print(f"  size: {in_b/1e6:,.0f} MB -> {out_b/1e6:,.0f} MB  ({100*(1-out_b/in_b):.0f}% smaller)")
    print("Problem files (if any) are listed in compress_log.txt inside the output folder.")


if __name__ == "__main__":
    main()
