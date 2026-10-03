from django.conf import settings
from rest_framework import serializers


class PointSerializer(serializers.Serializer):
    """Either a full US address or coordinates."""

    street = serializers.CharField(required=False, max_length=200)
    city = serializers.CharField(required=False, max_length=100)
    state = serializers.CharField(required=False, min_length=2, max_length=2)
    zip = serializers.CharField(required=False, allow_blank=True, max_length=10)
    lat = serializers.FloatField(required=False, min_value=-90, max_value=90)
    lon = serializers.FloatField(required=False, min_value=-180, max_value=180)

    def validate(self, data):
        has_coords = "lat" in data or "lon" in data
        has_address = any(k in data for k in ("street", "city", "state"))
        if has_coords and has_address:
            raise serializers.ValidationError("Pass either an address or lat/lon, not both.")
        if has_coords:
            if not ("lat" in data and "lon" in data):
                raise serializers.ValidationError("Both lat and lon are required.")
            return {"lat": data["lat"], "lon": data["lon"]}
        missing = [k for k in ("street", "city", "state") if not data.get(k)]
        if missing:
            raise serializers.ValidationError(f"Address requires: {', '.join(missing)}.")
        data["state"] = data["state"].upper()
        return data


class RouteRequestSerializer(serializers.Serializer):
    start = PointSerializer()
    finish = PointSerializer()
    start_fuel_gallons = serializers.FloatField(
        required=False, default=0.0, min_value=0,
        max_value=settings.VEHICLE_RANGE_MILES / settings.VEHICLE_MPG,
        help_text="Fuel in the tank at the start. 0 = empty: the first stop is the cheapest station "
                  "within the first 50 miles.",
    )
    stop_penalty_usd = serializers.FloatField(
        required=False, min_value=0, max_value=1000,
        help_text=f"Cost of one fuel stop (driver time). Default {settings.FUEL_STOP_PENALTY_USD}. "
                  "0 = pure cheapest fuel, even if that means stopping for a couple of gallons.",
    )
    explain = serializers.BooleanField(
        required=False, default=False,
        help_text="Add `alternatives` (every OSRM route with its plan and cost), `corridor_stations` "
                  "(every station along the chosen route with its status) and `summary.comparison`.",
    )


class LocationSerializer(serializers.Serializer):
    lat = serializers.FloatField()
    lon = serializers.FloatField()
    query = serializers.CharField()
    matched_address = serializers.CharField(allow_null=True)
    match_type = serializers.ChoiceField(["coordinates", "exact", "non_exact", "nominatim"])


class RouteSerializer(serializers.Serializer):
    start = LocationSerializer()
    finish = LocationSerializer()
    distance_miles = serializers.FloatField()
    duration_hours = serializers.FloatField()
    geometry = serializers.CharField(help_text="Encoded polyline, precision 6 (Leaflet / Google polyline decoders)")
    geometry_format = serializers.CharField()


class FuelStopSerializer(serializers.Serializer):
    order = serializers.IntegerField()
    opis_id = serializers.IntegerField()
    name = serializers.CharField()
    address = serializers.CharField()
    city = serializers.CharField()
    state = serializers.CharField()
    lat = serializers.FloatField()
    lon = serializers.FloatField()
    geo_precision = serializers.ChoiceField(["exit", "city"])
    mile_marker = serializers.FloatField()
    miles_from_previous = serializers.FloatField()
    detour_miles = serializers.FloatField(help_text="Straight-line distance from the original route")
    fuel_on_arrival_gallons = serializers.FloatField()
    price_per_gallon = serializers.FloatField()
    gallons = serializers.FloatField(help_text="Gallons to buy here")
    cost = serializers.FloatField()


class VehicleSerializer(serializers.Serializer):
    range_miles = serializers.FloatField()
    mpg = serializers.FloatField()
    tank_gallons = serializers.FloatField()


class SummarySerializer(serializers.Serializer):
    stops_count = serializers.IntegerField()
    total_gallons = serializers.FloatField()
    total_fuel_cost = serializers.FloatField()
    average_price_paid = serializers.FloatField(allow_null=True)
    vehicle = VehicleSerializer()
    comparison = serializers.DictField(
        required=False, help_text="Only with explain=true: same gallons at the corridor's avg / max price")


class MetaSerializer(serializers.Serializer):
    stop_penalty_usd = serializers.FloatField()
    routing_provider = serializers.CharField()
    external_calls = serializers.IntegerField()
    external_calls_detail = serializers.ListField(child=serializers.CharField())
    alternatives_compared = serializers.IntegerField()
    corridor_expansion = serializers.IntegerField()
    final_route_refined = serializers.BooleanField()
    compute_ms = serializers.FloatField()
    total_ms = serializers.FloatField()


class AlternativeSerializer(serializers.Serializer):
    index = serializers.IntegerField()
    chosen = serializers.BooleanField()
    distance_miles = serializers.FloatField()
    duration_hours = serializers.FloatField()
    geometry = serializers.CharField()
    corridor_stations = serializers.IntegerField()
    stops_count = serializers.IntegerField(allow_null=True)
    total_gallons = serializers.FloatField(allow_null=True)
    total_fuel_cost = serializers.FloatField(allow_null=True)
    objective = serializers.FloatField(allow_null=True, help_text="fuel cost + stop_penalty * stops")
    final_total_fuel_cost = serializers.FloatField(allow_null=True, help_text="Chosen route, after the final OSRM call")
    error = serializers.CharField(allow_null=True)


class CorridorStationSerializer(serializers.Serializer):
    opis_id = serializers.IntegerField()
    name = serializers.CharField()
    address = serializers.CharField()
    city = serializers.CharField()
    state = serializers.CharField()
    lat = serializers.FloatField()
    lon = serializers.FloatField()
    geo_precision = serializers.CharField()
    mile_marker = serializers.FloatField()
    detour_miles = serializers.FloatField()
    price_per_gallon = serializers.FloatField()
    status = serializers.ChoiceField(
        ["stop", "considered", "thinned"],
        help_text="stop: chosen; considered: went into the optimizer; thinned: a cheaper station nearby was kept",
    )


class RouteResponseSerializer(serializers.Serializer):
    route = RouteSerializer()
    fuel_stops = FuelStopSerializer(many=True)
    summary = SummarySerializer()
    meta = MetaSerializer()
    alternatives = AlternativeSerializer(many=True, required=False, help_text="Only with explain=true")
    corridor_stations = CorridorStationSerializer(many=True, required=False, help_text="Only with explain=true")


class ErrorSerializer(serializers.Serializer):
    detail = serializers.CharField()
