from drf_spectacular.utils import OpenApiExample, extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from planner.service import plan_trip
from routing.exceptions import RoutingError

from .serializers import ErrorSerializer, RouteRequestSerializer, RouteResponseSerializer


class RouteView(APIView):
    @extend_schema(
        summary="Plan a route with the cheapest fuel stops",
        description=(
            "Each point is a full US address `{street, city, state, zip?}` or coordinates `{lat, lon}`.\n\n"
            "- `start_fuel_gallons` (default 0): fuel in the tank at the start, 0..50\n"
            "- `stop_penalty_usd` (default 25): cost of one stop (driver time); 0 = cheapest fuel only\n"
            "- `explain` (default false): also return `alternatives` (every OSRM route with its plan), "
            "`corridor_stations` (every station along the chosen route with its status) and "
            "`summary.comparison` (savings vs the corridor's average price)\n\n"
            "2-3 external calls: Census geocoder (only for addresses) + OSRM route + OSRM route through the stops."
        ),
        request=RouteRequestSerializer,
        responses={200: RouteResponseSerializer, 400: ErrorSerializer, 422: ErrorSerializer,
                   502: ErrorSerializer, 504: ErrorSerializer},
        examples=[
            OpenApiExample(
                "Chicago -> Los Angeles (addresses)",
                value={
                    "start": {"street": "233 S Wacker Dr", "city": "Chicago", "state": "IL", "zip": "60606"},
                    "finish": {"street": "200 N Spring St", "city": "Los Angeles", "state": "CA", "zip": "90012"},
                    "start_fuel_gallons": 0,
                    "stop_penalty_usd": 25,
                    "explain": False,
                },
                request_only=True,
            ),
            OpenApiExample(
                "New York -> Chicago (coordinates)",
                value={"start": {"lat": 40.7128, "lon": -74.006}, "finish": {"lat": 41.8781, "lon": -87.6298},
                       "start_fuel_gallons": 25, "stop_penalty_usd": 25, "explain": False},
                request_only=True,
            ),
            OpenApiExample(
                "Los Angeles -> Washington with explain (alternatives + all stations)",
                value={"start": {"lat": 33.9427, "lon": -118.56445}, "finish": {"lat": 38.85652, "lon": -76.99219},
                       "start_fuel_gallons": 25, "stop_penalty_usd": 25, "explain": True},
                request_only=True,
            ),
        ],
    )
    def post(self, request):
        serializer = RouteRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            result = plan_trip(data["start"], data["finish"], data["start_fuel_gallons"],
                               data.get("stop_penalty_usd"), explain=data["explain"])
        except RoutingError as exc:
            return Response({"detail": exc.message}, status=exc.status)
        return Response(result)
