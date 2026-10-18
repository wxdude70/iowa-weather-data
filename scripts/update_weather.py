
import csv
import io
import json
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

BASE = "https://mesonet.agron.iastate.edu"
OUTPUT = Path("data")
OUTPUT.mkdir(parents=True, exist_ok=True)

# Dates based on Iowa local time
now = datetime.now(ZoneInfo("America/Chicago"))
today = now.date().isoformat()
yesterday = (now.date() - timedelta(days=1)).isoformat()


def download(url):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "IowaWeatherData/1.0"}
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        return response.read().decode("utf-8-sig")


# ---------------------------------------------
# 1. Get Iowa airport station coordinates
# ---------------------------------------------

stations_url = BASE + "/geojson/network/IA_ASOS.geojson"
station_data = json.loads(download(stations_url))
stations = {}

for feature in station_data.get("features", []):
    props = feature.get("properties") or {}
    geometry = feature.get("geometry") or {}

    sid = (
        props.get("id")
        or props.get("sid")
        or props.get("station")
        or feature.get("id")
    )

    if sid and geometry.get("type") == "Point":
        stations[str(sid).upper()] = {
            "coordinates": geometry["coordinates"],
            "name": props.get("name") or str(sid)
        }

if not stations:
    raise RuntimeError("No station coordinates were found")

print("Loaded", len(stations), "Iowa station locations")


# ---------------------------------------------
# 2. Retrieve daily weather observations
# ---------------------------------------------

def get_daily_data(date, variables):
    params = urlencode({
        "network": "IA_ASOS",
        "stations": "_ALL",
        "sts": date,
        "ets": date,
        "var": ",".join(variables),
        "format": "csv",
        "na": "blank"
    })

    url = BASE + "/cgi-bin/request/daily.py?" + params
    raw = download(url)
    rows = list(csv.DictReader(io.StringIO(raw)))

    if not rows or "station" not in rows[0]:
        raise RuntimeError("No daily data returned for " + date)

    return rows


# Yesterday's highs and gusts
yesterday_rows = get_daily_data(
    yesterday,
    ["max_temp_f", "max_wind_gust_kts"]
)

# IEM requires at least two seconds between daily requests
time.sleep(3)

# Today's lows, available progressively during the day
today_rows = get_daily_data(
    today,
    ["min_temp_f"]
)


# ---------------------------------------------
# 3. Define the three weather layers
# ---------------------------------------------

layers = [
    {
        "name": "iowa_highs",
        "title": "Yesterday's Iowa High Temperatures",
        "date": yesterday,
        "rows": yesterday_rows,
        "column": "max_temp_f",
        "units": "F",
        "multiplier": 1.0,
        "color": "255 190 70"
    },
    {
        "name": "iowa_lows",
        "title": "Today's Iowa Low Temperatures",
        "date": today,
        "rows": today_rows,
        "column": "min_temp_f",
        "units": "F",
        "multiplier": 1.0,
        "color": "80 190 255"
    },
    {
        "name": "iowa_gusts",
        "title": "Yesterday's Iowa Peak Wind Gusts",
        "date": yesterday,
        "rows": yesterday_rows,
        "column": "max_wind_gust_kts",
        "units": "mph",
        "multiplier": 1.15078,
        "color": "255 255 255"
    }
]


# ---------------------------------------------
# 4. Generate GeoJSON and GRLevelX placefiles
# ---------------------------------------------

for layer in layers:
    features = []
    placefile_lines = [
        "Title: " + layer["title"] + " - " + layer["date"],
        "Refresh: 60",
        "Threshold: 999",
        "Color: " + layer["color"],
        'Font: 1, 24, 1, "Arial"'
    ]

    for row in layer["rows"]:
        sid = row.get("station", "").strip().upper()
        raw_value = (row.get(layer["column"]) or "").strip()

        if sid not in stations or not raw_value:
            continue

        try:
            value = round(float(raw_value) * layer["multiplier"])
        except (ValueError, TypeError):
            continue

        location = stations[sid]
        lon, lat = location["coordinates"][:2]
        station_name = str(location["name"])
        label = str(value)

        # GeoJSON point
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [lon, lat]
            },
            "properties": {
                "name": label,
                "station": sid,
                "station_name": station_name,
                "value": value,
                "units": layer["units"],
                "date": layer["date"]
            }
        })

        # GRLevelX placefile text
        hover = (
            station_name + " (" + sid + ") | "
            + layer["date"] + " | "
            + label + " " + layer["units"]
        )

        hover = hover.replace('"', "'")

        placefile_lines.append(
            f'Text: {lat:.5f}, {lon:.5f}, 1, '
            f'"{label}", "{hover}"'
        )

    if not features:
        raise RuntimeError(
            "No valid observations for " + layer["name"]
        )

    # Save GeoJSON
    geojson = {
        "type": "FeatureCollection",
        "features": features
    }

    geojson_path = OUTPUT / (layer["name"] + ".geojson")
    geojson_path.write_text(
        json.dumps(geojson, indent=2),
        encoding="utf-8"
    )

    # Save GRLevelX placefile
    placefile_path = OUTPUT / (layer["name"] + ".txt")
    placefile_path.write_text(
        "\n".join(placefile_lines) + "\n",
        encoding="utf-8"
    )

    print(
        layer["name"],
        layer["date"],
        len(features),
        "stations generated"
    )

print("SUCCESS: All six weather files generated.")
