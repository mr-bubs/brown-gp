#!/usr/bin/env python3
"""Read-only Brown GP historical portrait coverage audit (2024 and 2025).

Run on the Brown GP phone while its local backend is running:
    python /path/to/brown_gp_portrait_audit.py

Writes only into ~/brown-gp/portrait_audit/ (not production source).
"""
import json
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE = 'https://api.openf1.org/v1'
LOCAL = 'http://127.0.0.1:10000'
OUT = Path.home() / 'brown-gp' / 'portrait_audit'
YEARS = (2024, 2025)


def get_json(url, attempts=3):
    for attempt in range(attempts):
        try:
            req = Request(url, headers={'User-Agent': 'BrownGP-Portrait-Audit/1.0', 'Accept': 'application/json'})
            with urlopen(req, timeout=25) as response:
                return json.load(response)
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            if attempt == attempts - 1:
                raise RuntimeError(f'{url}: {exc}') from exc
            time.sleep(2 * (attempt + 1))


def api(endpoint, **params):
    url = f'{BASE}/{endpoint}?{urlencode(params)}'
    return get_json(url)


def standings(year):
    payload = get_json(f'{LOCAL}/api/season-overview/{year}')
    rows = (payload.get('drivers', {}).get('MRData', {}).get('StandingsTable', {})
            .get('StandingsLists', [{}])[0].get('DriverStandings', []))
    if not rows:
        raise RuntimeError(f'No championship standings returned for {year}')
    result = {}
    for row in rows:
        driver = row['Driver']
        code = driver.get('code')
        if not code:
            raise RuntimeError(f'Missing driver code for {driver.get("driverId")} in {year}')
        result[code] = f'{driver.get("givenName", "")} {driver.get("familyName", "")}'.strip()
    return result


def image_check(url):
    """Check HTTP response and image signature; cannot prove portrait identity."""
    if not url or not url.startswith('https://'):
        return 'missing_url'
    try:
        req = Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Range': 'bytes=0-31'})
        with urlopen(req, timeout=20) as response:
            kind = response.headers.get('Content-Type', '').lower()
            signature = response.read(32)
            image_bytes = (signature.startswith(b'\x89PNG\r\n\x1a\n') or
                           signature.startswith(b'\xff\xd8\xff') or
                           signature.startswith(b'RIFF') or
                           signature.startswith(b'GIF8'))
            return 'image_response' if kind.startswith('image/') and image_bytes else f'unexpected_content:{kind}'
    except (HTTPError, URLError, TimeoutError) as exc:
        return f'http_error:{exc}'


def audit(year):
    expected = standings(year)
    print(f'\n{year}: {len(expected)} drivers in championship standings', flush=True)
    sessions = api('sessions', year=year, session_name='Race')
    sessions = sorted((s for s in sessions if s.get('session_key') is not None),
                      key=lambda s: s.get('date_start', ''))
    if not sessions:
        raise RuntimeError(f'OpenF1 returned no {year} Race sessions')
    # Scan the season backwards so late-season team changes are considered first.
    # Fill gaps with earlier race sessions, preserving the first matching URL.
    portraits = {}
    checked = []
    failures = []
    for session in reversed(sessions):
        if set(expected).issubset(portraits):
            break
        key = session['session_key']
        try:
            drivers = api('drivers', session_key=key)
            checked.append(key)
        except RuntimeError as exc:
            failures.append(str(exc))
            print(f'  session {key}: unavailable; continuing', flush=True)
            continue
        for driver in drivers:
            code = driver.get('name_acronym')
            url = driver.get('headshot_url')
            if code in expected and code not in portraits and isinstance(url, str) and url.startswith('https://'):
                portraits[code] = {
                    'name': expected[code], 'headshot_url': url,
                    'openf1_name': driver.get('full_name'),
                    'openf1_team': driver.get('team_name'),
                    'source_session_key': key,
                }
        print(f'  session {key}: {len(portraits)}/{len(expected)} portrait URLs collected', flush=True)
        time.sleep(0.35)

    for code, record in sorted(portraits.items()):
        record['url_check'] = image_check(record['headshot_url'])
        print(f'  {code}: {record["url_check"]}', flush=True)
        time.sleep(0.2)

    missing = {code: name for code, name in expected.items() if code not in portraits}
    result = {
        'season': year, 'expected_drivers': expected,
        'portraits': portraits, 'missing_drivers': missing,
        'checked_session_keys': checked, 'session_errors': failures,
        'note': 'image_response verifies image bytes, NOT driver identity or season uniform. Manual visual review remains required.'
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f'portraits_{year}_audit.json').write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n')
    good = sum(x['url_check'] == 'image_response' for x in portraits.values())
    print(f'{year} SUMMARY: {len(portraits)}/{len(expected)} URLs; {good} image responses; {len(missing)} missing', flush=True)
    if missing:
        print('MISSING:', ', '.join(f'{c} ({n})' for c, n in sorted(missing.items())), flush=True)
    return {'year': year, 'expected': len(expected), 'urls': len(portraits),
            'image_responses': good, 'missing': sorted(missing), 'session_errors': len(failures)}


def main():
    summary = []
    for year in YEARS:
        try:
            summary.append(audit(year))
        except Exception as exc:
            print(f'{year} FAILED: {exc}', flush=True)
            summary.append({'year': year, 'error': str(exc)})
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(f'\nReports saved in: {OUT}', flush=True)
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == '__main__':
    main()
