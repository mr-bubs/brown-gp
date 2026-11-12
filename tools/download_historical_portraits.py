#!/usr/bin/env python3
"""Download season-labelled portraits from Brown GP's existing OpenF1 audits.

Does not modify Brown GP source, Git, or running backend.
Input: 2024/2025 audit JSON plus localhost:10000/api/season-data for 2026
Output: ~/brown-gp/portrait_audit/driver_portraits/{year}/{year}_{CODE}.png
"""
import json
import re
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

ROOT = Path.home() / "brown-gp" / "portrait_audit"
OUTPUT = ROOT / "driver_portraits"
MAX_BYTES = 8 * 1024 * 1024

LOCAL_SEASON_DATA = "http://127.0.0.1:10000/api/season-data"

def current_season_portraits():
    """Read the same current-season headshot records used by landing002.html."""
    req = Request(LOCAL_SEASON_DATA, headers={"Accept": "application/json"})
    with urlopen(req, timeout=30) as response:
        bundle = json.load(response)
    records = bundle.get("headshots", [])
    if not isinstance(records, list):
        raise ValueError("2026 headshots were not a list")
    portraits = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        code = record.get("name_acronym")
        url = record.get("headshot_url")
        if isinstance(code, str) and re.fullmatch(r"[A-Z]{3}", code) and isinstance(url, str):
            portraits[code] = {"headshot_url": url}
    if not portraits:
        raise ValueError("No 2026 portrait URLs returned by local backend")
    return portraits


def is_image(data):
    return (data.startswith(bytes.fromhex("89504e470d0a1a0a")) or
            data.startswith(bytes.fromhex("ffd8ff")) or
            data.startswith(b"RIFF") and data[8:12] == b"WEBP")

def main():
    report = {"saved": [], "already_present": [], "failed": [], "missing": {}}
    for year in (2024, 2025, 2026):
        if year == 2026:
            try:
                portraits = current_season_portraits()
            except (HTTPError, URLError, TimeoutError, ValueError, OSError) as exc:
                print(f"2026: could not read local season headshots: {exc}", flush=True)
                report["failed"].append({"year": year, "reason": str(exc)})
                continue
            report["missing"][str(year)] = {}
            print(f"2026: {len(portraits)} headshot URLs from local backend", flush=True)
        else:
            audit_path = ROOT / f"portraits_{year}_audit.json"
            if not audit_path.is_file():
                print(f"Missing audit: {audit_path}")
                report["failed"].append({"year": year, "reason": "audit file missing"})
                continue
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
            if audit.get("season") != year:
                raise ValueError(f"Unexpected season in {audit_path}")
            portraits = audit.get("portraits", {})
            report["missing"][str(year)] = audit.get("missing_drivers", {})
        dest_dir = OUTPUT / str(year)
        dest_dir.mkdir(parents=True, exist_ok=True)
        for code, record in sorted(portraits.items()):
            if not re.fullmatch(r"[A-Z]{3}", code):
                raise ValueError(f"Invalid driver code: {code!r}")
            url = record.get("headshot_url", "")
            dest = dest_dir / f"{year}_{code}.png"
            if dest.exists() and is_image(dest.read_bytes()[:16]):
                print(f"EXISTS {dest.name}", flush=True)
                report["already_present"].append(str(dest.relative_to(OUTPUT)))
                continue
            if not isinstance(url, str) or not url.startswith("https://media.formula1.com/"):
                report["failed"].append({"year": year, "code": code, "reason": "unexpected URL host"})
                continue
            try:
                req = Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "image/*"})
                with urlopen(req, timeout=30) as response:
                    data = response.read(MAX_BYTES + 1)
                if len(data) > MAX_BYTES or not is_image(data):
                    raise ValueError("not a supported PNG/JPEG/WEBP image or too large")
                # Files are named .png for the audited URLs, which currently return PNG.
                if not data.startswith(bytes.fromhex("89504e470d0a1a0a")):
                    raise ValueError("image was not PNG; refusing mismatched .png extension")
                temp = dest.with_suffix(".png.part")
                temp.write_bytes(data)
                temp.replace(dest)
                report["saved"].append(str(dest.relative_to(OUTPUT)))
                print(f"SAVED  {dest.name} ({len(data)} bytes)", flush=True)
            except (HTTPError, URLError, TimeoutError, ValueError, OSError) as exc:
                print(f"FAILED {year}_{code}: {exc}", flush=True)
                report["failed"].append({"year": year, "code": code, "reason": str(exc)})
            time.sleep(0.25)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "download_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nSaved: {len(report['saved'])}, already present: {len(report['already_present'])}, failed: {len(report['failed'])}")
    for year, missing in report["missing"].items():
        print(f"{year} unavailable in audit: {', '.join(sorted(missing)) or 'none'}")
    print(f"Files: {OUTPUT}")
    print("No website files were modified.")

if __name__ == "__main__":
    main()
