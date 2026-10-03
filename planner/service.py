import bisect
import logging
import math
import time
from dataclasses import dataclass

from django.conf import settings

from routing import geocode, osrm
from routing.exceptions import NoRouteFound, RoutingError
from routing.http import CallCounter

from .corridor import stations_along
from .optimizer import Candidate, FuelGapError, plan_fuel, thin_out

log = logging.getLogger(__name__)


@dataclass
class Option:
    route: osrm.Route
    plan: object
    corridor_expansion: int
    corridor: list
    considered: set


def plan_trip(start: dict, finish: dict, start_fuel: float = 0.0, stop_penalty: float | None = None,
              explain: bool = False) -> dict:
    started = time.perf_counter()
    counter = CallCounter()
    tank = settings.VEHICLE_RANGE_MILES / settings.VEHICLE_MPG
    if stop_penalty is None:
        stop_penalty = settings.FUEL_STOP_PENALTY_USD

    locations = geocode.resolve({"start": start, "finish": finish}, counter)
    alternatives = osrm.route(
        [locations["start"].as_point(), locations["finish"].as_point()], counter, alternatives=True
    )

    compute_started = time.perf_counter()
    options, failed, last_gap = [], [], None
    for route in alternatives:
        try:
            options.append(_plan_for_route(route, tank, start_fuel, stop_penalty))
        except FuelGapError as gap:
            last_gap = gap
            failed.append((route, str(gap)))
    if not options:
        raise NoRouteFound(f"Route is not drivable with a {settings.VEHICLE_RANGE_MILES} mi range: {last_gap}")
    best = min(options, key=lambda o: o.plan.objective)
    compute_ms = (time.perf_counter() - compute_started) * 1000

    route, plan, refined = best.route, best.plan, False
    if plan.stops:
        try:
            route, plan = _refine_through_stops(locations, best, tank, start_fuel, stop_penalty, counter)
            refined = True
        except (RoutingError, FuelGapError) as exc:
            log.warning("final route through stops failed, keeping the corridor plan: %s", exc)

    detours = {c.ref.opis_id: c.detour_miles for c in best.corridor}
    response = _response(
        locations, route, plan, detours,
        meta={
            "stop_penalty_usd": stop_penalty,
            "routing_provider": "osrm",
            "external_calls": counter.total,
            "external_calls_detail": counter.calls,
            "alternatives_compared": len(alternatives),
            "corridor_expansion": best.corridor_expansion,
            "final_route_refined": refined,
            "compute_ms": round(compute_ms, 1),
            "total_ms": round((time.perf_counter() - started) * 1000, 1),
        },
    )
    if explain:
        response["alternatives"] = _explain_alternatives(alternatives, options, failed, best, plan)
        response["corridor_stations"] = _explain_corridor(best, plan)
        response["summary"]["comparison"] = _comparison(best, plan)
    return response


def _plan_for_route(route, tank, start_fuel, stop_penalty) -> Option:
    """Find stations in the corridor; widen it if the route has a fuel gap (no extra API calls)."""
    expansion, gap = 1, None
    for _ in range(settings.CORRIDOR_MAX_EXPANSION + 1):
        corridor = stations_along(route, expansion)
        candidates = thin_out(corridor)
        try:
            plan = plan_fuel(candidates, route.distance_miles, tank=tank * settings.PLANNING_RANGE_MARGIN,
                             mpg=settings.VEHICLE_MPG,
                             start_fuel=start_fuel, stop_penalty=stop_penalty,
                             start_window_miles=settings.START_RESERVE_MILES)
            return Option(route, plan, expansion, corridor, {c.ref.opis_id for c in candidates})
        except FuelGapError as exc:
            gap = exc
            expansion *= 2
    raise gap


def _refine_through_stops(locations, option, tank, start_fuel, stop_penalty, counter):
    """Second OSRM call through the chosen stations: real geometry incl. detours,
    exact leg distances -> recompute gallons on the same stations.

    Between two waypoints OSRM takes its own fastest path, which is not necessarily the
    alternative we chose (LA -> Jean, NV: 268 mi instead of 244). So the request also gets
    via points sampled from the chosen route, with the direction of travel."""
    stops = option.plan.stops
    miles = _cumulative_miles(option.route.coords)
    waypoints = [(0.0, locations["start"].as_point(), None, None)]  # (mile, point, bearing, station)
    for s in stops:
        st = s.candidate.ref
        bearing = _bearing_at(option.route.coords, miles, s.candidate.mile) if st.geo_precision == "exit" else None
        waypoints.append((s.candidate.mile, (st.location.y, st.location.x), bearing, st))
    for mile, point, bearing in _via_points(option.route, miles, avoid=[s.candidate.mile for s in stops]):
        waypoints.append((mile, point, bearing, None))
    waypoints.append((option.route.distance_miles, locations["finish"].as_point(), None, None))
    waypoints.sort(key=lambda w: w[0])

    final = osrm.route([w[1] for w in waypoints], counter, bearings=[w[2] for w in waypoints])[0]

    # real legs already include the detours, so detour_miles stays 0 here
    candidates, mile = [], 0.0
    for (_, _, _, station), leg in zip(waypoints[1:], final.legs_miles):
        mile += leg
        if station is not None:
            candidates.append(Candidate(mile=mile, price=float(station.price), ref=station))

    plan = plan_fuel(candidates, final.distance_miles, tank=tank, mpg=settings.VEHICLE_MPG,
                     start_fuel=start_fuel, stop_penalty=stop_penalty,
                     start_window_miles=candidates[0].mile + 1 if candidates else 0)
    return final, plan


def _via_points(route, miles, every=100.0, avoid=(), clearance=15.0):
    """Points on the route every `every` miles (not within `clearance` of a stop or the ends),
    with the bearing of the road there."""
    result, next_mark = [], every
    for k, cum in enumerate(miles):
        if cum < next_mark:
            continue
        next_mark += every
        if cum > route.distance_miles - clearance or any(abs(cum - a) < clearance for a in avoid):
            continue
        result.append((cum, route.coords[k], _bearing_at(route.coords, miles, cum)))
    return result


def _cumulative_miles(coords):
    miles = [0.0]
    for a, b in zip(coords, coords[1:]):
        miles.append(miles[-1] + _haversine(a, b))
    return miles


def _bearing_at(coords, miles, mile):
    k = min(bisect.bisect_left(miles, mile), len(coords) - 2)
    return _bearing(coords[k], coords[min(k + 5, len(coords) - 1)])


def _haversine(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 3958.8 * math.asin(math.sqrt(h))


def _bearing(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    x = math.sin(lon2 - lon1) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(lon2 - lon1)
    return math.degrees(math.atan2(x, y)) % 360


def _explain_alternatives(alternatives, options, failed, best, final_plan):
    by_route = {id(o.route): o for o in options}
    errors = {id(r): e for r, e in failed}
    result = []
    for i, route in enumerate(alternatives):
        option = by_route.get(id(route))
        chosen = option is best
        plan = option.plan if option else None
        result.append({
            "index": i,
            "chosen": chosen,
            "distance_miles": round(route.distance_miles, 1),
            "duration_hours": round(route.duration_hours, 2),
            "geometry": route.geometry,
            "corridor_stations": len(option.corridor) if option else 0,
            "stops_count": len(plan.stops) if plan else None,
            "total_gallons": round(plan.total_gallons, 2) if plan else None,
            "total_fuel_cost": round(plan.total_cost, 2) if plan else None,
            "objective": round(plan.objective, 2) if plan else None,
            "final_total_fuel_cost": round(final_plan.total_cost, 2) if chosen else None,
            "error": errors.get(id(route)),
        })
    return result


def _explain_corridor(option, final_plan):
    stop_ids = {s.candidate.ref.opis_id for s in final_plan.stops}
    rows = []
    for c in sorted(option.corridor, key=lambda c: c.mile):
        st = c.ref
        if st.opis_id in stop_ids:
            status = "stop"
        elif st.opis_id in option.considered:
            status = "considered"
        else:
            status = "thinned"  # a cheaper station nearby was kept instead
        rows.append({
            "opis_id": st.opis_id,
            "name": st.name,
            "address": st.address,
            "city": st.city,
            "state": st.state,
            "lat": round(st.location.y, 6),
            "lon": round(st.location.x, 6),
            "geo_precision": st.geo_precision,
            "mile_marker": round(c.mile, 1),
            "detour_miles": round(c.detour_miles, 1),
            "price_per_gallon": round(c.price, 4),
            "status": status,
        })
    return rows


def _comparison(option, plan):
    """The same gallons bought at the corridor's average / max price."""
    prices = [c.price for c in option.corridor]
    gallons = plan.total_gallons
    if not prices or not gallons:
        return None
    avg, worst = sum(prices) / len(prices), max(prices)
    return {
        "corridor_min_price": round(min(prices), 4),
        "corridor_avg_price": round(avg, 4),
        "corridor_max_price": round(worst, 4),
        "cost_at_avg_price": round(gallons * avg, 2),
        "cost_at_max_price": round(gallons * worst, 2),
        "savings_vs_avg": round(gallons * avg - plan.total_cost, 2),
    }


def _location(loc):
    return {"lat": round(loc.lat, 6), "lon": round(loc.lon, 6), "query": loc.query,
            "matched_address": loc.matched_address, "match_type": loc.match_type}


def _response(locations, route, plan, detours, meta):
    stops, prev_mile = [], 0.0
    for i, stop in enumerate(plan.stops, 1):
        st, c = stop.candidate.ref, stop.candidate
        stops.append({
            "order": i,
            "opis_id": st.opis_id,
            "name": st.name,
            "address": st.address,
            "city": st.city,
            "state": st.state,
            "lat": round(st.location.y, 6),
            "lon": round(st.location.x, 6),
            "geo_precision": st.geo_precision,
            "mile_marker": round(c.mile, 1),
            "miles_from_previous": round(c.mile - prev_mile, 1),
            "detour_miles": round(detours.get(st.opis_id, c.detour_miles), 1),
            "fuel_on_arrival_gallons": round(stop.fuel_on_arrival, 2),
            "price_per_gallon": round(c.price, 4),
            "gallons": round(stop.gallons, 2),
            "cost": round(stop.cost, 2),
        })
        prev_mile = c.mile

    total_gallons = plan.total_gallons
    return {
        "route": {
            "start": _location(locations["start"]),
            "finish": _location(locations["finish"]),
            "distance_miles": round(route.distance_miles, 1),
            "duration_hours": round(route.duration_hours, 2),
            "geometry": route.geometry,
            "geometry_format": "polyline6",
        },
        "fuel_stops": stops,
        "summary": {
            "stops_count": len(stops),
            "total_gallons": round(total_gallons, 2),
            "total_fuel_cost": round(plan.total_cost, 2),
            "average_price_paid": round(plan.total_cost / total_gallons, 4) if total_gallons else None,
            "vehicle": {"range_miles": settings.VEHICLE_RANGE_MILES, "mpg": settings.VEHICLE_MPG,
                        "tank_gallons": settings.VEHICLE_RANGE_MILES / settings.VEHICLE_MPG},
        },
        "meta": meta,
    }
