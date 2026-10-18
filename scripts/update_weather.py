
import csv
import io
import json
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

BASE = "https://mesonet.agron.iastate.edu"
OUTPUT = Path("data")
OUTPUT.mkdir(exist_ok=True)

# Yesterday in Iowa local time
day = (datetime.now(ZoneInfo("America/Chicago")).date()
       - timedelta(days=1)).isoformat()

def download(url):
    request = urllib.request.Request(
        url, headers={"User-Agent": "IowaWeatherData/1.0"}
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        return response.read().decode("utf-8-sig")

# Get Iowa airport locations
stations_url = BASE + "/geojson/network/IA_ASOS.geojson"
station_data = json.loads(download(stations_url))
stations = {}

for feature in station_data["features"]:
    p = feature.get("properties", {})
    station_id = (
        p.get("id") or p.get("sid") or
        p.get("station") or feature.get("id")
    )
    geometry = feature.get("geometry") or {}
    if station_id and geometry.get("type") == "Point":
        stations[str(station_id).upper()] = {
            "coordinates": geometry["coordinates"],
            "name": p.get("name", str(station_id))
        }

if not stations:
    raise RuntimeError("No Iowa station coordinates found")

# Download yesterday's daily summaries
params = urlencode({
    "network": "IA_ASOS",
    "stations": "_ALL",
    "sts": day,
    "ets": day,
    "var": "max_temp_f,min_temp_f,max_wind_gust_kts",
    "format": "csv",
    "na": "blank"
})

url = BASE + "/cgi-bin/request/daily.py?" + params
raw = download(url)
rows = list(csv.DictReader(io.StringIO(raw)))

if not rows or "station" not in rows[0]:
    raise RuntimeError("Daily observations unavailable")

variables = {
    "iowa_highs.geojson": ("max_temp_f", "F", 1.0),
    "iowa_lows.geojson": ("min_temp_f", "F", 1.0),
    "iowa_gusts.geojson": ("max_wind_gust_kts", "mph", 1.15078)
}

for filename, (column, units, multiplier) in variables.items():
    features = []

    for row in rows:
        sid = row["station"].strip().upper()
        value = row.get(column, "").strip()

        if sid not in stations or not value:
            continue

        try:
            number = round(float(value) * multiplier)
        except ValueError:
            continue

        location = stations[sid]

        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": location["coordinates"]
            },
            "properties": {
                "name": str(number),
                "station": sid,
                "station_name": location["name"],
                "value": number,
                "units": units,
                "date": day
            }
        })

    if not features:
        raise RuntimeError("No valid observations for " + filename)

    output = {
        "type": "FeatureCollection",
        "features": features
    }

    (OUTPUT / filename).write_text(
        json.dumps(output, indent=2),
        encoding="utf-8"
    )

    print(filename, len(features), "stations", day)

print("All three GeoJSON files created.")

# Generate GRLevelX placefiles from GeoJSON data
placefiles = {
    "iowa_highs.geojson": (
        "iowa_highs.txt",
        "Yesterday's Iowa High Temperatures",
        "255 190 70"
    ),
    "iowa_lows.geojson": (
        "iowa_lows.txt",
        "Yesterday's Iowa Low Temperatures",
        "80 190 255"
    ),
    "iowa_gusts.geojson": (
        "iowa_gusts.txt",
        "Yesterday's Iowa Peak Wind Gusts",
        "255 255 255"
    )
}

for source, (destination, title, color) in placefiles.items():
    geojson = json.loads((OUTPUT / source).read_text())

    lines = [
        f"Title: {title} - {day}",
        "Refresh: 60",
        "Threshold: 999",
        f"Color: {color}",
        'Font: 1, 24, 1, "Arial"'
    ]

    for feature in geojson["features"]:
        props = feature["properties"]
        lon, lat = feature["geometry"]["coordinates"]
        value = props["name"]
        station = props["station"]
        hover = f"{station} - {day}"

        lines.append(
            f'Text: {lat:.5f}, {lon:.5f}, 1, '
            f'"{value}", "{hover}"'
        )

    (OUTPUT / destination).write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8"
    )

    print("Created", destination)
