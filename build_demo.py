"""Regenerate docs/index.html (the browser demo) from index.html. Run: python build_demo.py"""
s = open('index.html', encoding='utf-8').read()

def sub(old, new):
    global s
    assert old in s, old
    s = s.replace(old, new, 1)

sub('async function api(path,body){\n', 'async function api(path,body){\n  if(window.DEMO) return window.DEMO(path,body);\n')
sub('<script>\nconst TOKEN', '<script src="ffmpeg/ffmpeg.js"></script>\n<script src="demo.js"></script>\n<script>\nconst TOKEN')
sub('<div class="tabs"', '<div class="msg warn" style="margin:0 0 16px"><b>Browser demo.</b> Runs in this tab on your own files (Chrome/Edge on a computer): pick folders with Browse. Video is H.264 only (slow, ffmpeg.wasm); metadata and file dates are not preserved. AV1, H.265, GPU and lossless PNG optimising need the <a href="https://github.com/FrCynda/media-compressor/releases" style="color:inherit">Windows app</a>.</div>\n  <div class="tabs"')
sub('placeholder="C:\\path\\to\\media"', 'placeholder="Click Browse" readonly')
sub('placeholder="C:\\path\\to\\output"', 'placeholder="Click Browse" readonly')
sub("$('inp').value=lsGet('ic_in')||''; $('out').value=lsGet('ic_out')||'';", '')
open('docs/index.html', 'w', encoding='utf-8').write(s)
