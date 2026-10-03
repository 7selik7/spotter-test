import csv
import io
from dataclasses import dataclass

from django.conf import settings

from .exceptions import GeocodingFailed
from .http import CallCounter, request


US_BOUNDS = {"lat": (24.3, 49.5), "lon": (-125.0, -66.8)}


@dataclass
class Location:
    lat: float
    lon: float
    query: str
    matched_address: str | None = None
    match_type: str = "coordinates"  # coordinates | exact | non_exact | nominatim

    def as_point(self):
        return self.lat, self.lon


def _address_line(point):
    return ", ".join(p for p in (point["street"], point["city"], point["state"], point.get("zip") or "") if p)


def ensure_in_us(location: Location, label: str):
    (lat_min, lat_max), (lon_min, lon_max) = US_BOUNDS["lat"], US_BOUNDS["lon"]
    if not (lat_min <= location.lat <= lat_max and lon_min <= location.lon <= lon_max):
        raise GeocodingFailed(f"{label}: point {location.lat:.4f},{location.lon:.4f} is outside the contiguous US")


def resolve(points: dict, counter: CallCounter) -> dict[str, Location]:
    """points: {"start": {...}, "finish": {...}} -> {"start": Location, "finish": Location}"""
    result = {}
    addresses = {}
    for label, point in points.items():
        if "lat" in point:
            result[label] = Location(point["lat"], point["lon"], query=f"{point['lat']},{point['lon']}")
        else:
            addresses[label] = point

    if addresses:
        result.update(_census_batch(addresses, counter))
        for label in addresses.keys() - result.keys():
            result[label] = _nominatim(addresses[label], counter)

    for label, location in result.items():
        ensure_in_us(location, label)
    return result


def _census_batch(addresses: dict, counter: CallCounter) -> dict[str, Location]:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    for label, a in addresses.items():
        writer.writerow([label, a["street"], a["city"], a["state"], a.get("zip") or ""])

    response = request(
        "POST",
        settings.CENSUS_GEOCODER_URL,
        data={"benchmark": "Public_AR_Current"},
        files={"addressFile": ("addresses.csv", buffer.getvalue(), "text/csv")},
        counter=counter,
        name="census.geocode",
    )
    found = {}
    for row in csv.reader(io.StringIO(response.text)):
        if len(row) < 6 or row[2] != "Match":
            continue
        lon, lat = (float(v) for v in row[5].split(","))
        found[row[0]] = Location(
            lat=lat,
            lon=lon,
            query=_address_line(addresses[row[0]]),
            matched_address=row[4],
            match_type=row[3].lower(),
        )
    return found


def _nominatim(address: dict, counter: CallCounter) -> Location:
    response = request(
        "GET",
        settings.NOMINATIM_URL,
        params={
            "street": address["street"],
            "city": address["city"],
            "state": address["state"],
            "postalcode": address.get("zip") or "",
            "countrycodes": "us",
            "format": "jsonv2",
            "limit": 1,
        },
        counter=counter,
        name="nominatim.geocode",
    )
    data = response.json() if response.status_code == 200 else []
    if not data:
        raise GeocodingFailed(f"Address not found: {_address_line(address)}")
    return Location(
        lat=float(data[0]["lat"]),
        lon=float(data[0]["lon"]),
        query=_address_line(address),
        matched_address=data[0].get("display_name"),
        match_type="nominatim",
    )
