"""Portable JSON, exact original export and an offline static preview gallery."""

import base64
import html
import io
import json
import os
from pathlib import Path
import shutil
import tempfile

from PIL import Image, ImageOps


def preflight(path):
    path = Path(path)
    if path.exists() or path.is_symlink():
        raise ValueError(f"Output already exists: {path}")
    if not path.parent.is_dir():
        raise ValueError("Output parent directory does not exist")
    return path


def write_bytes(path, raw):
    path = preflight(path)
    fd, temporary = tempfile.mkstemp(prefix=".imagelens-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def write_json(path, value):
    write_bytes(path, (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode())


def export_gallery(library, destination, query=None):
    destination = preflight(destination)
    records = library.search(query) if query is not None else library.list()
    if len(records) > 200:
        raise ValueError("Gallery is limited to 200 images; narrow it with --query")
    # Claim a new destination exclusively. Existing directories are never replaced.
    destination.mkdir()
    try:
        assets = destination / "assets"; assets.mkdir()
        exported, cards = [], []
        for record in records:
            raw = library.read_original(record["id"])
            with Image.open(io.BytesIO(raw)) as source:
                source.load(); preview = ImageOps.exif_transpose(source)
                preview.thumbnail((512, 512), Image.Resampling.LANCZOS)
                preview = preview.convert("RGBA")
                preview.info.clear()
                filename = record["id"] + ".png"
                preview.save(assets / filename, format="PNG")
                preview_url = "data:image/png;base64," + base64.b64encode((assets / filename).read_bytes()).decode("ascii")
            exported.append({**record, "preview_path": "assets/" + filename})
            esc = lambda value: html.escape(str(value), quote=True)
            tags = "".join(f'<span class="tag">{esc(tag)}</span>' for tag in record["tags"])
            alt = record["alt_text"]
            status = "Reviewed" if record["review_status"] == "reviewed" else "Needs review"
            cards.append(f'''<article class="card">
<div class="preview"><img src="{preview_url}" alt="{esc(alt)}"></div>
<div class="details"><div class="row"><span class="status">{status}</span><span>{record['width']} × {record['height']}</span></div>
<h2>{esc(record['original_filename'])}</h2><p>{esc(alt) if alt else '<em>Alt text has not been added.</em>'}</p>
<div class="tags">{tags}</div><p class="source">Metadata: {esc(record['metadata_source'])}</p></div></article>''')
        manifest = {"schema_version": 1, "kind": "preview-gallery", "images": exported}
        write_json(destination / "metadata.json", manifest)
        page = '''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>Image Lens — library</title><style>
*{box-sizing:border-box}body{margin:0;background:#f5f2ec;color:#182522;font:16px/1.5 system-ui,sans-serif}
header{max-width:1180px;margin:auto;padding:54px 28px 28px}.eyebrow{color:#446b5f;letter-spacing:.14em;text-transform:uppercase;font-size:12px;font-weight:750}
h1{font-size:clamp(32px,5vw,52px);line-height:1.1;letter-spacing:-.04em;margin:12px 0}header p{color:#576660;max-width:660px}
main{max-width:1180px;padding:0 28px 56px;margin:auto}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:22px}
.card{background:#fff;border:1px solid #d9dfd9;border-radius:18px;overflow:hidden;min-width:0}.preview{height:240px;background:#e9eee7;display:grid;place-items:center;padding:20px}.preview img{max-width:100%;max-height:200px;object-fit:contain}
.details{padding:20px}.row{display:flex;align-items:center;justify-content:space-between;gap:8px;color:#64726a;font-size:12px}.status{border:1px solid #bccfc0;border-radius:999px;padding:3px 9px;color:#315542;background:#f0f6ef}
h2{font-size:17px;margin:15px 0 8px;overflow-wrap:anywhere}.details p{font-size:14px;color:#4b5b53;overflow-wrap:anywhere}.tags{display:flex;flex-wrap:wrap;gap:6px}.tag{background:#edf2ed;border-radius:6px;padding:3px 8px;font-size:12px}.source{font-size:11px!important;color:#748078!important}
footer{margin-top:28px;color:#69766e;font-size:13px}em{color:#7e7662}</style></head><body>
<header><div class="eyebrow">Image Lens / Local collection</div><h1>A little more findable.</h1>
<p>Preserved originals. Clear descriptions. Tags that help you find the right image again.</p></header><main>'''
        page += f'<p>{len(records)} image(s) in this export.</p><div class="grid">' + "".join(cards) + '</div>'
        page += '<footer>Offline preview export · Originals remain in your library · Review image descriptions before publishing.</footer></main></body></html>\n'
        write_bytes(destination / "index.html", page.encode())
    except Exception:
        shutil.rmtree(destination)
        raise
    return {"images": len(records), "directory": str(destination)}
