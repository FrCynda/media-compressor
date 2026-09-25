# Media Compressor

Local Windows tool that shrinks folders of images and videos. A small Python server runs on 127.0.0.1 and serves a browser UI; only counts and sizes are shown, never file names.

**[Live demo](https://frcynda.github.io/media-compressor/)** - the real UI running fully in your browser, nothing uploaded. Images: oxipng (WASM, same output as the CLI) and JPEG with EXIF/ICC/prompt metadata copied. Video: H.264 via multi-threaded ffmpeg.wasm; AV1/H.265/hardware via your browser's WebCodecs encoder where available (bitrate-based, so CRF is approximated). Chrome/Edge write into your output folder; Firefox/Safari return a ZIP. Browsers can't set file dates or delete originals outside Chrome/Edge, and exiftool/jpegli/x265 results can differ slightly, so use the exe for exact output. `build_demo.py` regenerates `docs/` from `index.html`.

## Run
- Download `MediaCompressor.exe` from [Releases](../../releases) (tools bundled), or
- `start.bat` (needs Python, plus exiftool and oxipng; ffmpeg for video).
