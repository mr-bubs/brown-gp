#!/usr/bin/env python3
"""Create a self-contained offline HTML contact sheet from saved Brown GP portraits.

Reads: ~/brown-gp/portrait_audit/driver_portraits/{year}/*.png
Writes: ~/brown-gp/portrait_audit/portrait_contact_sheet.html
Does not alter source files, images, backend, or Git.
"""
import base64
import html
import json
from pathlib import Path

ROOT = Path.home() / "brown-gp" / "portrait_audit"
PORTRAITS = ROOT / "driver_portraits"
OUTPUT = ROOT / "portrait_contact_sheet.html"

def main():
    sections = []
    counts = {}
    for year in (2024, 2025, 2026):
        folder = PORTRAITS / str(year)
        images = sorted(folder.glob(f"{year}_*.png")) if folder.is_dir() else []
        counts[str(year)] = len(images)
        tiles = []
        for file in images:
            data = file.read_bytes()
            if not data.startswith(bytes.fromhex("89504e470d0a1a0a")):
                print(f"SKIPPED invalid PNG: {file}")
                continue
            code = file.stem.removeprefix(f"{year}_")
            kb = len(data) / 1024
            warning = '<span class="warning">CHECK SMALL FILE</span>' if len(data) < 6000 else ""
            uri = "data:image/png;base64," + base64.b64encode(data).decode("ascii")
            tiles.append(
                '<article class="tile"><div class="portrait"><img loading="lazy" src="' + uri +
                '" alt="' + html.escape(str(year) + " " + code, quote=True) +
                '"></div><strong>' + html.escape(code) + '</strong><small>' +
                f'{kb:.1f} KB</small>' + warning + '</article>'
            )
        sections.append(
            f'<section><h2>{year} <span>{len(tiles)} images</span></h2><div class="grid">' +
            "".join(tiles) + "</div></section>"
        )
    if not any(counts.values()):
        raise SystemExit(f"No portraits found in {PORTRAITS}")
    page = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Brown GP | Portrait audit</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#101112;color:#f4f1ed;font-family:system-ui,sans-serif}
header{padding:24px 18px;background:#222426;border-bottom:2px solid #c7a477}
h1{margin:0 0 8px;font-size:25px}p{margin:0;color:#c7c3bd;line-height:1.5}
section{padding:14px 16px 28px}h2{border-bottom:1px solid #555;padding-bottom:10px;color:#d9b991}
h2 span{font-size:14px;color:#aaa;font-weight:400}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(116px,1fr));gap:12px}
.tile{background:#292b2d;border:1px solid #414244;border-radius:12px;padding:8px;text-align:center;min-width:0}
.portrait{height:125px;display:flex;align-items:center;justify-content:center;background:#343737;border-radius:9px;overflow:hidden}
.portrait img{max-width:100%;max-height:100%;object-fit:contain}
strong{display:block;font-size:16px;margin-top:7px}small{display:block;color:#b9b9b9;margin-top:3px}
.warning{display:block;color:#ffbb73;font-size:10px;margin-top:5px;font-weight:700}
</style></head><body><header><h1>Brown GP · Driver portrait audit</h1>
<p>Offline review of saved images, grouped by year. Small files are flagged for inspection.
Same URL across seasons does not guarantee historically accurate uniforms.</p></header>
""" + "".join(sections) + "</body></html>"
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(page, encoding="utf-8")
    print("Contact sheet created:", OUTPUT)
    print("Images by year:", json.dumps(counts))
    print("Total:", sum(counts.values()))
    print("Copy to Downloads: cp ~/brown-gp/portrait_audit/portrait_contact_sheet.html ~/storage/downloads/")
    print("No website files or portraits modified.")

if __name__ == "__main__":
    main()
