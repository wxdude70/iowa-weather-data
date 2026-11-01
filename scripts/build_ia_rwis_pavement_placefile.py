#!/usr/bin/env python3
"""Build an Iowa RWIS pavement-temperature numbers-only GRLevelX placefile."""
import csv
import io
import math
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

OUTPUT = Path('docs/ia_rwis_pavement_placefile.txt')
SENSOR_KEYS = ('tsf0', 'tsf1', 'tsf2', 'tsf3')
MAX_AGE = timedelta(minutes=100)


def fetch(now):
    params = {
        'network': 'IA_RWIS', 'stations': '_ALL', 'tz': 'UTC',
        'what': 'download', 'src': 'atmos', 'gis': 'true',
        'sts': (now - timedelta(minutes=90)).strftime('%Y-%m-%dT%H:%M:%S'),
        'ets': now.strftime('%Y-%m-%dT%H:%M:%S'),
    }
    url = 'https://mesonet.agron.iastate.edu/cgi-bin/request/rwis.py?' + urlencode(params)
    error = None
    for attempt in range(3):
        try:
            with urlopen(Request(url, headers={'User-Agent': 'IowaRWISPlacefile/2.0'}), timeout=120) as r:
                return r.read().decode('utf-8-sig')
        except (TimeoutError, OSError, HTTPError, URLError) as exc:
            error = exc
            print(f'RWIS request attempt {attempt+1} failed: {exc}', flush=True)
            if attempt != 2:
                time.sleep(5)
    raise RuntimeError(f'Iowa Mesonet request failed: {error}')


def parse_csv(body):
    lines = body.splitlines()
    # IEM returns a conventional header, sometimes preceded by '#'.
    # Explicitly locate it rather than guessing CSV dialect from arbitrary comments.
    header_index = None
    for i, line in enumerate(lines):
        cleaned = line.strip().removeprefix('#').strip().strip('"').lower()
        if cleaned.startswith('station,') and 'latitude' in cleaned and 'longitude' in cleaned and 'tsf0' in cleaned:
            header_index = i
            break
    if header_index is None:
        print('First response lines:', repr(lines[:6]), flush=True)
        raise RuntimeError('IEM CSV header not found. Expected station,obtime,longitude,latitude,...,tsf0.')
    text = '\n'.join([lines[header_index].lstrip().removeprefix('#').strip()] + [
        l for l in lines[header_index+1:] if l.strip() and not l.lstrip().startswith('#')
    ])
    reader = csv.DictReader(io.StringIO(text), delimiter=',', skipinitialspace=True)
    rows = [{str(k).strip().lower(): (v or '').strip() for k, v in row.items() if k is not None}
            for row in reader]
    print(f'IEM CSV parsed: {len(rows)} rows; fields: {reader.fieldnames}', flush=True)
    return rows


def number(text):
    try:
        n = float(str(text).split('(')[0].strip())
        return n if math.isfinite(n) else None
    except (ValueError, TypeError):
        return None


def write_placefile(now, rows):
    stations = {}
    for row in rows:
        sid = row.get('station', '').strip()
        try:
            observed = datetime.fromisoformat(row.get('obtime', '').replace('Z', '+00:00'))
            if observed.tzinfo is None:
                observed = observed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if not sid or not timedelta(0) <= now - observed <= MAX_AGE:
            continue
        lat, lon = number(row.get('latitude')), number(row.get('longitude'))
        if lat is None or lon is None or not (40 <= lat <= 44 and -97 <= lon <= -89):
            continue
        values = [number(row.get(k)) for k in SENSOR_KEYS]
        values = [v for v in values if v is not None and -50 <= v <= 160]
        if not values:
            continue
        temp = sum(values) / len(values)
        if sid not in stations or observed > stations[sid][0]:
            stations[sid] = (observed, lat, lon, temp)
    if not stations:
        raise RuntimeError('No fresh pavement sensor measurements; leaving any previous file unchanged.')
    out = [
        'Title: Iowa RWIS Pavement Temperatures (F)',
        'RefreshSeconds: 600',
        'Threshold: 999',
        'Font: 1, 14, 1, "Arial"',
        'Color: 255 255 255',
        '',
    ]
    for _, lat, lon, temp in sorted(stations.values(), key=lambda x: (x[1], x[2])):
        out += [f'Object: {lat:.5f}, {lon:.5f}', f'  Text: 4, -4, 1, "{round(temp)}"', 'End:', '']
    out.append(f'; Stations displayed: {len(stations)}')
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text('\n'.join(out) + '\n', encoding='utf-8')
    print(f'Success: {len(stations)} Iowa RWIS pavement temperatures written to {OUTPUT}', flush=True)


def main():
    now = datetime.now(timezone.utc)
    write_placefile(now, parse_csv(fetch(now)))


if __name__ == '__main__':
    main()
