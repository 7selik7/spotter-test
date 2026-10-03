from django.conf import settings
from django.db import connection

from stations.models import Station

from .optimizer import Candidate

METERS_IN_MILE = 1609.344

# 1. The OSRM polyline6 is decoded right in PostGIS and simplified (~50 m).
# 2. A cross-country line has a bbox the size of the US, so the spatial index can't help;
#    ST_Subdivide cuts it into short pieces and ST_DWithin uses the GiST index per piece.
# 3. Distance / position along the route are computed in USA Contiguous Albers

SQL = """
WITH route AS MATERIALIZED (
    SELECT ST_Simplify(ST_LineFromEncodedPolyline(%(polyline)s, 6), 0.0005) AS geom
), pieces AS (
    SELECT ST_Subdivide(geom, 32)::geography AS geog FROM route
), near AS (
    SELECT DISTINCT s.id, s.location
    FROM stations_station s JOIN pieces p ON ST_DWithin(s.location, p.geog, %(radius_m)s)
), route_m AS MATERIALIZED (
    SELECT ST_Transform(geom, 5070) AS geom FROM route
)
SELECT near.id,
       ST_Distance(route_m.geom, ST_Transform(near.location::geometry, 5070)) AS distance_m,
       ST_LineLocatePoint(route_m.geom, ST_Transform(near.location::geometry, 5070)) AS fraction
FROM near, route_m
"""


def stations_along(route, expansion=1) -> list[Candidate]:
    """route: routing.osrm.Route. expansion: corridor multiplier (1, 2, 4...)."""
    limits = {k: v * expansion * METERS_IN_MILE for k, v in settings.CORRIDOR_MILES.items()}
    with connection.cursor() as cursor:
        cursor.execute(SQL, {"polyline": route.geometry, "radius_m": max(limits.values())})
        rows = cursor.fetchall()

    stations = Station.objects.in_bulk([r[0] for r in rows])
    candidates = []
    for station_id, distance_m, fraction in rows:
        station = stations[station_id]
        if distance_m > limits[station.geo_precision]:
            continue
        candidates.append(
            Candidate(
                mile=fraction * route.distance_miles,
                price=float(station.price),
                ref=station,
                detour_miles=distance_m / METERS_IN_MILE,
            )
        )
    return candidates
