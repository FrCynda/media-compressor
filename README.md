# Media Compressor

Local Windows tool that shrinks folders of images and videos. A small Python server runs on 127.0.0.1 and serves a browser UI; only counts and sizes are shown, never file names.

**[Live demo](https://frcynda.github.io/media-compressor/)** - the real UI running fully in your browser (Chrome/Edge on a computer; nothing is uploaded). Pick folders with Browse. Images use canvas, video uses ffmpeg.wasm (H.264 only, slow). The demo has no AV1/H.265/GPU, lossless PNG optimising or metadata/date preservation - those need the exe below. `build_demo.py` regenerates `docs/` from `index.html`.

## Run
- Download `MediaCompressor.exe` from [Releases](../../releases) (tools bundled), or
- `start.bat` (needs Python, plus exiftool and oxipng; ffmpeg for video).
