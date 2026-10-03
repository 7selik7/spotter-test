import csv
from decimal import Decimal

from django.conf import settings
from django.contrib.gis.geos import Point
from django.core.management.base import BaseCommand
from django.db import transaction

from stations.models import Station


class Command(BaseCommand):
    help = "Load geocoded stations (data/stations_geocoded.csv) into the DB. Idempotent."

    def add_arguments(self, parser):
        parser.add_argument("--path", default=str(settings.STATIONS_CSV))

    @transaction.atomic
    def handle(self, *args, path, **options):
        with open(path, newline="", encoding="utf-8") as f:
            stations = [
                Station(
                    opis_id=int(row["opis_id"]),
                    name=row["name"],
                    address=row["address"],
                    city=row["city"],
                    state=row["state"],
                    rack_id=int(row["rack_id"]) if row["rack_id"] else None,
                    price=Decimal(row["price"]).quantize(Decimal("0.0001")),
                    location=Point(float(row["lon"]), float(row["lat"]), srid=4326),
                    geo_precision=row["geo_precision"],
                )
                for row in csv.DictReader(f)
            ]
        Station.objects.bulk_create(
            stations,
            update_conflicts=True,
            unique_fields=["opis_id"],
            update_fields=["name", "address", "city", "state", "rack_id", "price", "location", "geo_precision"],
            batch_size=1000,
        )
        removed, _ = Station.objects.exclude(opis_id__in=[s.opis_id for s in stations]).delete()
        self.stdout.write(self.style.SUCCESS(f"stations loaded: {len(stations)}, removed: {removed}"))
