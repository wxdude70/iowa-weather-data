#!/usr/bin/env python3
"""Numbers-only Iowa RWIS pavement temperature live GRLevelX placefile."""
import csv
import io
import math
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

OUTPUT = Path('docs/ia_rwis_pavement_placefile.txt')
MAX_AGE = timedelta(minutes=90)
LAT = ('lat', 'latitude', 'geo_lat')
LON = ('lon', 'longitude', 'long', 'geo_lon')
STATION = ('station', 'station_id', 'sid', 'id')
TIMESTAMP = ('obtime', 'valid', 'ob_valid', 'timestamp', 'datetime', 'ob_time', 'time', 'date')
SENSORS = ('tsf0', 'tsf1', 'tsf2', 'tsf3')
AVERAGE = ('pave_avg', 'avg_surface_temp', 'ave_surface_temp')


def value(row, keys):
    for key in keys:
        raw = row.get(key)
        if raw is not None and str(raw).strip():
            return str(raw).strip()
    return None


def number(raw):
    if raw is None:
        return None
    try:
        n = float(str(raw).split('(')[0].strip())
        return n if math.isfinite(n) else None
    except (ValueError, TypeError):
        return None


def obs_datetime(raw):
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace('Z', '+00:00'))
    except ValueError:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def pavement_temp(row):
    vals = [number(row.get(k)) for k in SENSORS]
    vals = [v for v in vals if v is not None and -50 <= v <= 160]
    if vals:
        return sum(vals) / len(vals)
    avg = number(value(row, AVERAGE))
    return avg if avg is not None and avg != 0 and -50 <= avg <= 160 else None


def fetch_rows(now):
    # Limit the download to one hour rather than 3 hours of statewide records.
    # The freshness filter below separately excludes observations >90 minutes old.
    params = {
        'network': 'IA_RWIS', 'stations': '_ALL', 'tz': 'UTC',
        'what': 'download', 'src': 'atmos', 'gis': 'true',
        'sts': (now - timedelta(minutes=60)).strftime('%Y-%m-%dT%H:%M:%S'),
        'ets': now.strftime('%Y-%m-%dT%H:%M:%S'),
    }
    url = 'https://mesonet.agron.iastate.edu/cgi-bin/request/rwis.py?' + urlencode(params)
    request = Request(url, headers={'User-Agent': 'IowaRWISPavementPlacefile/1.1'})
    last_error = None
    for attempt in range(3):
        try:
            with urlopen(request, timeout=120) as response:
                body = response.read().decode('utf-8-sig')
            break
        except (TimeoutError, OSError) as exc:
            last_error = exc
            print(f'IEM download attempt {attempt+1}/3 failed: {exc}', flush=True)
            if attempt < 2:
                time.sleep(5)
    else:
        raise RuntimeError(f'IEM data download failed after 3 attempts: {last_error}')

    raw_lines = [line for line in body.splitlines() if line.strip()]
    if not raw_lines:
        raise RuntimeError('IEM returned no records.')
    header_index = None
    delimiter = None
    for i, line in enumerate(raw_lines):
        candidate = line.lstrip('\ufeff').strip().lstrip('#').strip()
        for delim in (',', '\t', ';', '|'):
            parts = next(csv.reader([candidate], delimiter=delim, skipinitialspace=True))
            fields = [p.strip().lower() for p in parts]
            if (set(SENSORS + AVERAGE) & set(fields) and set(LAT) & set(fields)
                    and set(LON) & set(fields)):
                header_index, delimiter = i, delim
                break
        if header_index is not None:
            break
    if header_index is None:
        print('IEM first lines:', repr(raw_lines[:5]))
        raise RuntimeError('Could not locate pavement sensor and coordinate headers.')
    header = raw_lines[header_index].lstrip('\ufeff').strip().lstrip('#').strip()
    data_lines = [header] + [line for line in raw_lines[header_index+1:]
                             if not line.lstrip().startswith('#')]
    reader = csv.DictReader(io.StringIO('\n'.join(data_lines)), delimiter=delimiter,
                            skipinitialspace=True)
    rows = [{(k or '').strip().lower(): v for k, v in row.items() if k is not None}
            for row in reader]
    print(f'IEM returned {len(rows)} records, headers: {reader.fieldnames}', flush=True)
    return rows


def build(now, rows):
    newest = {}
    for row in rows:
        station = value(row, STATION)
        obs = obs_datetime(value(row, TIMESTAMP))
        if not station or obs is None or not timedelta(0) <= now - obs <= MAX_AGE:
            continue
        lat, lon = number(value(row, LAT)), number(value(row, LON))
        temp = pavement_temp(row)
        if lat is None or lon is None or temp is None or not (40 <= lat <= 44 and -97 <= lon <= -89):
            continue
        if station not in newest or obs > newest[station][0]:
            newest[station] = (obs, lat, lon, temp)
    if not newest:
        raise RuntimeError('No current Iowa pavement sensor readings found; old file retained.')
    lines = ['Title: Iowa RWIS Pavement Temperatures (F)', 'RefreshSeconds: 600',
             'Threshold: 999', 'Font: 1, 14, 1, "Arial"', 'Color: 255 255 255', '']
    for station, (_, lat, lon, temp) in sorted(newest.items()):
        lines += [f'Object: {lat:.5f}, {lon:.5f}',
                  f'  Text: 4, -4, 1, "{round(temp)}"', 'End:', '']
    lines.append(f'; Stations displayed: {len(newest)}')
    print(f'Pavement temperature stations: {len(newest)}', flush=True)
    return '\n'.join(lines) + '\n'


def main():
    now = datetime.now(timezone.utc)
    content = build(now, fetch_rows(now))
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(content, encoding='utf-8')
    print(f'Wrote {OUTPUT}')


if __name__ == '__main__':
    main()
