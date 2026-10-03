import csv
import json
import math
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC_CSV = ROOT / "fuel-prices-for-be-assessment.csv"
OUT_CSV = ROOT / "data" / "stations_geocoded.csv"
CACHE = ROOT / "data" / "cache"
GAZETTEER_URL = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2026_Gazetteer/2026_Gaz_{}_national.zip"
GAZETTEER_FILES = ("place", "cousubs")
OVERPASS_URLS = (
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass-api.de/api/interpreter",
)
OVERPASS_WORKERS = 3
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "spotter-fuel-route-assessment/0.1 (one-off geocoding)"

US_STATES = set(
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV "
    "NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split()
)
EXIT_MAX_MILES_FROM_CITY = 15
EXIT_ONLY_MAX_MILES_FROM_CITY = 8


def haversine_miles(lat1, lon1, lat2, lon2):
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def http_get(url, data=None, timeout=300):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def norm_city(name):
    s = name.upper().replace(".", "").replace("'", "").replace("-", " ")
    s = re.sub(r"\bST\b", "SAINT", s)
    s = re.sub(r"\bFT\b", "FORT", s)
    s = re.sub(r"\bMT\b", "MOUNT", s)
    return re.sub(r"\s+", " ", s).strip()


def load_stations():
    """US-only stations, deduplicated by OPIS id (row with the lowest price wins)."""
    best = {}
    with open(SRC_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            row = {k.strip(): (v or "").strip() for k, v in row.items()}
            if row["State"] not in US_STATES:
                continue
            row["Retail Price"] = float(row["Retail Price"])
            opis_id = int(row["OPIS Truckstop ID"])
            if opis_id not in best or row["Retail Price"] < best[opis_id]["Retail Price"]:
                best[opis_id] = row
    return best


GAZ_SUFFIX = re.compile(
    r"\s+(city and borough|consolidated government|metropolitan government|unified government|"
    r"urban county|charter township|city|town|village|CDP|borough|municipality|township|"
    r"plantation|CCD|UT|gore|grant|location|purchase|precinct|district|reservation)$",
    re.I,
)


def load_gazetteer():
    centroids = {}
    for name in GAZETTEER_FILES:
        path = CACHE / f"2026_Gaz_{name}_national.txt"
        if not path.exists():
            import io
            import zipfile

            print(f"downloading gazetteer {name} ...")
            zipfile.ZipFile(io.BytesIO(http_get(GAZETTEER_URL.format(name)))).extractall(CACHE)
        with open(path, encoding="utf-8", errors="replace") as f:
            for row in csv.DictReader(f, delimiter="|"):
                row = {k.strip(): v.strip() for k, v in row.items()}
                base = re.sub(r"\s*\(.*\)", "", row["NAME"])
                base = GAZ_SUFFIX.sub("", base)
                point = (float(row["INTPTLAT"]), float(row["INTPTLONG"]))
                key = (norm_city(base), row["USPS"])
                names = [key[0]]
                if key[0].endswith(" CITY"):
                    names.append(key[0][: -len(" CITY")])
                for n in names:
                    centroids.setdefault((n, row["USPS"]), point)
                    centroids.setdefault((n.replace(" ", ""), row["USPS"]), point)
    return centroids


def gazetteer_lookup(gazetteer, city, state):
    name = norm_city(city)
    for candidate in (name, name.replace(" ", ""), re.sub(r" NATIONAL PARK$", "", name)):
        if (candidate, state) in gazetteer:
            return gazetteer[(candidate, state)]
    return None


def nominatim_city(city, state, cache):
    key = f"{city}|{state}"
    if key not in cache:
        query = urllib.parse.urlencode(
            {"city": city, "state": state, "country": "us", "format": "jsonv2", "limit": 1}
        )
        try:
            result = json.loads(http_get(f"{NOMINATIM_URL}?{query}", timeout=30))
        except Exception as exc:  # noqa: BLE001
            print(f"  nominatim failed for {key}: {exc}")
            return None
        cache[key] = [float(result[0]["lat"]), float(result[0]["lon"])] if result else None
        time.sleep(1.1)
    return cache[key]


def resolve_cities(stations):
    gazetteer = load_gazetteer()
    cache_path = CACHE / "nominatim_cities.json"
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}

    result = {}
    pairs = sorted({(s["City"], s["State"]) for s in stations.values()})
    missing = [(c, st) for c, st in pairs if gazetteer_lookup(gazetteer, c, st) is None]
    print(f"cities: {len(pairs)}, not in gazetteer: {len(missing)} -> nominatim")
    for city, state in pairs:
        point = gazetteer_lookup(gazetteer, city, state)
        if point is None:
            point = nominatim_city(city, state, cache)
            cache_path.write_text(json.dumps(cache, indent=0))
        if point:
            result[(city, state)] = tuple(point)
    return result


OVERPASS_QUERY = """
[out:json][timeout:600];
area["ISO3166-2"="US-{state}"]["admin_level"="4"]->.a;
node["highway"="motorway_junction"]["ref"](area.a)->.n;
.n out;
way(bn.n)["highway"~"^(motorway|trunk)$"]["ref"];
out body;
"""


def fetch_overpass(state, mirror_offset=0):
    path = CACHE / "overpass" / f"{state}.json"
    if path.exists():
        return json.loads(path.read_text())
    path.parent.mkdir(parents=True, exist_ok=True)
    data = urllib.parse.urlencode({"data": OVERPASS_QUERY.format(state=state)}).encode()
    for attempt in range(8):
        url = OVERPASS_URLS[(attempt + mirror_offset) % len(OVERPASS_URLS)]
        try:
            raw = http_get(url, data=data, timeout=700)
            payload = json.loads(raw)
            path.write_text(json.dumps(payload))
            print(f"  overpass {state}: ok via {url}")
            return payload
        except Exception as exc:  # noqa: BLE001
            wait = 15 * (attempt + 1)
            print(f"  overpass {state} via {url} failed ({exc}); retry in {wait}s")
            time.sleep(wait)
    raise RuntimeError(f"overpass failed for {state}")


ROAD_RE = re.compile(r"\b(I|IH|US|SR|SH|HWY|ST|[A-Z]{2})\s*-?\s*(\d{1,4})", re.I)
OSM_REF_RE = re.compile(r"^\s*([A-Z]+)\s*-?\s*(\d{1,4})", re.I)
CSV_EXIT_RE = re.compile(r"\bEXIT\s*#?\s*(\d{1,4})\s*-?\s*([A-Z])?\b", re.I)
OSM_EXIT_RE = re.compile(r"^\s*(\d{1,4})\s*-?\s*([A-Z](?:\s*[-/]\s*[A-Z])*)?", re.I)


def road_key(family, number):
    family = family.upper()
    if family in ("I", "IH"):
        family = "I"
    elif family != "US":
        family = "STATE"
    return family, int(number)


def parse_csv_address(address):
    """'I-57 & I-64, EXIT 95' -> ({('I',57), ('I',64)}, (95, None))"""
    m = CSV_EXIT_RE.search(address)
    if not m:
        return None, None
    roads = {road_key(f, n) for f, n in ROAD_RE.findall(address[: m.start()])}
    return roads, (int(m.group(1)), (m.group(2) or "").upper() or None)


def parse_osm_exit(ref):
    m = OSM_EXIT_RE.match(ref)
    if not m:
        return None
    letters = set(re.findall(r"[A-Z]", (m.group(2) or "").upper()))
    return int(m.group(1)), letters


def build_exit_index(state):
    """(exit_number) -> list of {lat, lon, letters, roads, node_id}"""
    payload = fetch_overpass(state)
    nodes, node_roads = {}, defaultdict(set)
    for el in payload["elements"]:
        if el["type"] == "node":
            nodes[el["id"]] = el
    for el in payload["elements"]:
        if el["type"] == "way":
            roads = set()
            for part in re.split(r"[;/]", el.get("tags", {}).get("ref", "")):
                m = OSM_REF_RE.match(part)
                if m:
                    roads.add(road_key(*m.groups()))
            for node_id in el.get("nodes", ()):
                if node_id in nodes:
                    node_roads[node_id] |= roads
    index = defaultdict(list)
    for node_id, el in nodes.items():
        for tag in ("ref", "old_ref"):
            parsed = parse_osm_exit(el["tags"].get(tag, ""))
            if parsed:
                number, letters = parsed
                index[number].append(
                    {"lat": el["lat"], "lon": el["lon"], "letters": letters,
                     "roads": node_roads[node_id], "node_id": node_id}
                )
    return index


def match_exit(station, exit_index, city_point):
    roads, exit_ = parse_csv_address(station["Address"])
    if not exit_:
        return None
    number, letter = exit_
    candidates = exit_index.get(number, [])
    if letter:
        exact = [c for c in candidates if letter in c["letters"]]
        candidates = exact or candidates

    def nearest(cands, max_miles):
        if not cands:
            return None
        if city_point is None:
            first = cands[0]
            spread = max(haversine_miles(first["lat"], first["lon"], c["lat"], c["lon"]) for c in cands)
            return first if spread <= 3 else None
        best = min(cands, key=lambda c: haversine_miles(*city_point, c["lat"], c["lon"]))
        return best if haversine_miles(*city_point, best["lat"], best["lon"]) <= max_miles else None

    on_road = [c for c in candidates if roads & c["roads"]] if roads else []
    return nearest(on_road, EXIT_MAX_MILES_FROM_CITY) or nearest(
        candidates, EXIT_ONLY_MAX_MILES_FROM_CITY
    )


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    stations = load_stations()
    print(f"stations (US, deduplicated): {len(stations)}")

    cities = resolve_cities(stations)

    states = sorted({s["State"] for s in stations.values() if CSV_EXIT_RE.search(s["Address"])})
    todo = [st for st in states if not (CACHE / "overpass" / f"{st}.json").exists()]
    print(f"overpass: {len(states)} states, {len(todo)} to download")
    with ThreadPoolExecutor(max_workers=OVERPASS_WORKERS) as pool:
        list(pool.map(lambda args: fetch_overpass(args[1], mirror_offset=args[0] % OVERPASS_WORKERS),
                      enumerate(todo)))
    exit_indexes = {state: build_exit_index(state) for state in states}

    stats, out_rows = Counter(), []
    for opis_id, s in sorted(stations.items()):
        city_point = cities.get((s["City"], s["State"]))
        exit_node = match_exit(s, exit_indexes.get(s["State"], {}), city_point)
        if exit_node:
            lat, lon, precision, node = exit_node["lat"], exit_node["lon"], "exit", exit_node["node_id"]
        elif city_point:
            (lat, lon), precision, node = city_point, "city", ""
        else:
            stats["unresolved"] += 1
            print(f"  unresolved: {opis_id} {s['Address']}, {s['City']}, {s['State']}")
            continue
        stats[precision] += 1
        out_rows.append({
            "opis_id": opis_id, "name": s["Truckstop Name"], "address": s["Address"],
            "city": s["City"], "state": s["State"], "rack_id": s["Rack ID"],
            "price": s["Retail Price"], "lat": round(lat, 6), "lon": round(lon, 6),
            "geo_precision": precision, "osm_node_id": node,
        })

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(out_rows[0]))
        writer.writeheader()
        writer.writerows(out_rows)
    print(f"written {len(out_rows)} rows -> {OUT_CSV.relative_to(ROOT)}; {dict(stats)}")


if __name__ == "__main__":
    sys.exit(main())