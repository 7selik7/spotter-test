from dataclasses import dataclass

import polyline
from django.conf import settings

from .exceptions import NoRouteFound, UpstreamUnavailable
from .http import CallCounter, request

METERS_IN_MILE = 1609.344


@dataclass
class Route:
    distance_miles: float
    duration_hours: float
    geometry: str
    coords: list  # [(lat, lon), ...]
    legs_miles: list


def route(points, counter: CallCounter, alternatives=False, bearings=None) -> list[Route]:
    """OSRM /route for [(lat, lon), ...]. One HTTP call; returns 1..N routes (best first).
    bearings: optional [degrees or None] per point - direction of travel at that point,
    so a via point on a divided highway snaps to the right carriageway."""
    path = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in points)
    params = {
        "overview": "full",
        "geometries": "polyline6",
        "steps": "false",
        "alternatives": "3" if alternatives else "false",
    }
    if bearings:
        params["bearings"] = ";".join("" if b is None else f"{round(b) % 360},45" for b in bearings)
    response = request(
        "GET",
        f"{settings.OSRM_URL}/route/v1/driving/{path}",
        params=params,
        counter=counter,
        name="osrm.route",
    )
    data = response.json()
    if data.get("code") != "Ok":
        if data.get("code") in ("NoRoute", "NoSegment"):
            raise NoRouteFound(f"No drivable route between the points ({data.get('code')})")
        raise UpstreamUnavailable(f"osrm.route: {data.get('code')} {data.get('message', '')}".strip())

    return [
        Route(
            distance_miles=r["distance"] / METERS_IN_MILE,
            duration_hours=r["duration"] / 3600,
            geometry=r["geometry"],
            coords=polyline.decode(r["geometry"], 6),
            legs_miles=[leg["distance"] / METERS_IN_MILE for leg in r["legs"]],
        )
        for r in data["routes"]
    ]
