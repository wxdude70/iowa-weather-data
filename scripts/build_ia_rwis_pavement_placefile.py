#!/usr/bin/env python3
"""Create a numbers-only Iowa RWIS pavement-temperature GRLevelX placefile.

Requires Python 3.10+. Designed for GitHub Actions.
Output: docs/ia_rwis_pavement_placefile.txt

The IEM API may change its CSV column names. If the returned data doesn't have
recognized pavement or coordinate fields, the script FAILS rather than silently
publishing an empty/misleading map.
"""
import csv
import io
import math
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
# IEM RWIS pavement sensor temperatures commonly appear as tsf0-tsf3.
# Avoid treating 0 in pave_avg as a valid mean when individual sensors are present.
SENSORS = ('tsf0', 'tsf1', 'tsf2', 'tsf3', 'pavement_temp', 'pavement_temperature')
AVERAGE = ('pave_avg', 'avg_surface_temp', 'ave_surface_temp')


def value(row, keys):
    for key in keys:
        raw = row.get(key)
        if raw is not None and str(raw).strip():
            return str(raw).strip()
    return None


def number(v):
    if v is None:
        return None
    try:
        n = float(v.split('(')[0].strip())
        return n if math.isfinite(n) else None
    except (ValueError, TypeError):
        return None


def obs_datetime(raw):
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace('Z', '+00:00'))
    except ValueError:
        dt = None
        for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M'):
            try:
                dt = datetime.strptime(raw, fmt)
                break
            except ValueError:
                continue
    if dt is None:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def pavement_temp(row):
    sensor_values = [number(row.get(k)) for k in SENSORS]
    sensor_values = [n for n in sensor_values if n is not None and -50 <= n <= 160]
    if sensor_values:
        return sum(sensor_values) / len(sensor_values)
    avg = number(value(row, AVERAGE))
    # IEM's surface summary sometimes uses zero when average is unavailable.
    return avg if avg is not None and avg != 0 and -50 <= avg <= 160 else None


def fetch_rows(now):
    params = {
        'network': 'IA_RWIS', 'stations': '_ALL', 'tz': 'UTC',
        'what': 'download', 'src': 'atmos', 'gis': 'true',
        'sts': (now - timedelta(hours=3)).strftime('%Y-%m-%dT%H:%M:%S'),
        'ets': now.strftime('%Y-%m-%dT%H:%M:%S'),
    }
    url = 'https://mesonet.agron.iastate.edu/cgi-bin/request/rwis.py?' + urlencode(params)
    request = Request(url, headers={'User-Agent': 'IowaRWISPavementPlacefile/1.0'})
    with urlopen(request, timeout=60) as response:
        body = response.read().decode('utf-8-sig')
    raw_lines = [line for line in body.splitlines() if line.strip()]
    if not raw_lines:
        raise RuntimeError('IEM returned no records.')

    # IEM CSV exports can prefix the header with '#'. Do not discard
    # that header along with ordinary comment lines.
    reader = None
    fields = []
    for idx, raw_line in enumerate(raw_lines):
        candidate_header = raw_line.lstrip('\ufeff').strip()
        if candidate_header.startswith('#'):
            candidate_header = candidate_header[1:].strip()
        for delim in (',', '\t', ';', '|'):
            parts = next(csv.reader([candidate_header], delimiter=delim, skipinitialspace=True))
            headers = [(part or '').strip().lower() for part in parts]
            if not (set(SENSORS + AVERAGE) & set(headers)):
                continue
            if not (set(LAT) & set(headers)) or not (set(LON) & set(headers)):
                continue
            data_lines = [candidate_header]
            for line in raw_lines[idx + 1:]:
                if not line.lstrip().startswith('#'):
                    data_lines.append(line)
            candidate = csv.DictReader(io.StringIO('\n'.join(data_lines)), delimiter=delim,
                                       skipinitialspace=True)
            reader, fields = candidate, headers
            print('IEM delimiter:', repr(delim))
            break
        if reader is not None:
            break
    if reader is None:
        print('IEM first lines:', repr(raw_lines[:5]))
        raise RuntimeError('Could not find a CSV header with coordinates and pavement fields.')
    print('IEM fields:', ', '.join(fields))
    rows = [{(k or '').strip().lower().lstrip('\ufeff'): v for k, v in row.items()
             if k is not None} for row in reader]
    return rows


def build(now, rows):
    newest = {}
    for row in rows:
        station = value(row, STATION)
        ts = obs_datetime(value(row, TIMESTAMP))
        if not station or ts is None or not timedelta(0) <= now - ts <= MAX_AGE:
            continue
        lat, lon = number(value(row, LAT)), number(value(row, LON))
        temp = pavement_temp(row)
        if lat is None or lon is None or temp is None or not (40 <= lat <= 44) or not (-97 <= lon <= -89):
            continue
        if station not in newest or ts > newest[station][0]:
            newest[station] = (ts, lat, lon, temp)

    if not newest:
        raise RuntimeError('No fresh Iowa pavement observations with coordinates; original file retained.')

    lines = [
        'Title: Iowa RWIS Pavement Temperatures (F)',
        'RefreshSeconds: 600',
        'Threshold: 999',
        'Font: 1, 14, 1, "Arial"',
        'Color: 255 255 255',
        '',
    ]
    for station, (_, lat, lon, temp) in sorted(newest.items()):
        label = str(round(temp))
        lines.extend([
            f'Object: {lat:.5f}, {lon:.5f}',
            f'  Text: 4, -4, 1, "{label}"',
            'End:',
            '',
        ])
    lines.append(f'; Stations displayed: {len(newest)}')
    print(f'Current valid pavement-temperature stations: {len(newest)}')
    return '\n'.join(lines) + '\n'


def main():
    now = datetime.now(timezone.utc)
    content = build(now, fetch_rows(now))
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(content, encoding='utf-8')
    print('Wrote:', OUTPUT)


if __name__ == '__main__':
    main()
