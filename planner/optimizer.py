from dataclasses import dataclass, field

EPS = 1e-9


@dataclass
class Candidate:
    mile: float
    price: float
    ref: object = None
    detour_miles: float = 0.0


@dataclass
class Stop:
    candidate: Candidate
    fuel_on_arrival: float
    gallons: float

    @property
    def cost(self):
        return self.gallons * self.candidate.price


@dataclass
class Plan:
    stops: list = field(default_factory=list)
    stop_penalty: float = 0.0

    @property
    def total_gallons(self):
        return sum(s.gallons for s in self.stops)

    @property
    def total_cost(self):
        return sum(s.cost for s in self.stops)

    @property
    def objective(self):
        return self.total_cost + self.stop_penalty * len(self.stops)


class FuelGapError(Exception):
    def __init__(self, from_mile, to_mile):
        super().__init__(f"No fuel station reachable between mile {from_mile:.0f} and mile {to_mile:.0f}")
        self.from_mile, self.to_mile = from_mile, to_mile


def thin_out(candidates, bucket_miles=20.0, per_bucket=2):
    """In every `bucket_miles` slice of the route keep the `per_bucket` cheapest stations
    plus the one closest to the route (a cheap one 40 mi away must not push out the one
    right on the highway). A corridor across the country has 500+ stations; this keeps
    the DP fast while every stretch of the road still has a station."""
    buckets = {}
    for c in candidates:
        buckets.setdefault(int(c.mile // bucket_miles), []).append(c)
    kept = []
    for group in buckets.values():
        chosen = sorted(group, key=lambda c: (c.price, -c.mile))[:per_bucket]
        nearest = min(group, key=lambda c: (c.detour_miles, c.price))
        if nearest not in chosen:
            chosen.append(nearest)
        kept += chosen
    return sorted(kept, key=lambda c: c.mile)


def plan_fuel(candidates, distance, *, tank, mpg, start_fuel=0.0, stop_penalty=0.0,
              start_window_miles=50.0) -> Plan:
    """
    candidates: Candidate list (any order); distance: total route miles.
    start_fuel == 0: the truck reaches its first station on the reserve, so the first stop
    must be within `start_window_miles` of the start.
    """
    stations = sorted((c for c in candidates if 0 <= c.mile <= distance), key=lambda c: c.mile)
    if start_fuel * mpg >= distance - EPS:
        return Plan(stop_penalty=stop_penalty)

    finish = len(stations)
    pos = [c.mile for c in stations] + [distance]
    # station `d` miles off the route costs 2*d miles: get there and back to the route.
    det = [c.detour_miles for c in stations] + [0.0]
    rng = tank * mpg

    best = [dict() for _ in range(finish + 1)]

    def relax(node, fuel, value, parent):
        key = round(fuel, 6)
        if key not in best[node] or value < best[node][key][0] - EPS:
            best[node][key] = (value, *parent)

    # from start
    reach = start_fuel * mpg if start_fuel > 0 else start_window_miles
    for j, p in enumerate(pos[:finish]):
        if p > reach + EPS:
            break
        if p + det[j] <= reach + EPS:
            relax(j, max(start_fuel - (p + det[j]) / mpg, 0.0), 0.0, (None, None, 0.0))

    # forward over stations
    for i in range(finish):
        if not best[i]:
            continue
        price = stations[i].price
        for fuel, value in _pareto(best[i]):
            for j in range(i + 1, finish + 1):
                if pos[j] - pos[i] > rng + EPS:
                    break
                need = (pos[j] - pos[i] + det[i] + det[j]) / mpg
                if need > tank + EPS:
                    continue

                buy = max(need - fuel, 0.0)
                cost = buy * price + (stop_penalty if buy > EPS else 0.0)
                relax(j, max(fuel - need, 0.0), value + cost, (i, fuel, buy))

                if j != finish and tank - fuel > EPS:
                    buy = tank - fuel
                    relax(j, tank - need, value + buy * price + stop_penalty, (i, fuel, buy))

    if not best[finish]:
        raise FuelGapError(*_largest_gap(pos, det, rng, reach))

    node, fuel = finish, min(best[finish], key=lambda f: best[finish][f][0])
    stops = []
    while True:
        _, parent, parent_fuel, bought = best[node][fuel]
        if parent is None:
            break
        if bought > EPS:
            stops.append(Stop(stations[parent], fuel_on_arrival=parent_fuel, gallons=bought))
        node, fuel = parent, round(parent_fuel, 6)
    stops.reverse()
    return Plan(stops=stops, stop_penalty=stop_penalty)


def _pareto(states):
    """Drop dominated states: less fuel in the tank and not cheaper -> never better."""
    front, cheapest = [], float("inf")
    for fuel in sorted(states, reverse=True):
        value = states[fuel][0]
        if value < cheapest - EPS:
            front.append((fuel, value))
            cheapest = value
    return front


def _largest_gap(pos, det, rng, reach):
    """Where the route breaks (for the error message): the start can't reach any station,
    or two neighbouring stations are more than one tank apart."""
    first = [p for p, d in zip(pos[:-1], det) if p + d <= reach]
    if not first:
        nearest = min((p + d for p, d in zip(pos[:-1], det)), default=pos[-1])
        return 0.0, nearest
    points = [0.0] + pos
    for a, b in zip(points, points[1:]):
        if b - a > rng:
            return a, b
    return 0.0, pos[-1]
