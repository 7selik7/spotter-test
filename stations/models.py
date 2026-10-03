from django.contrib.gis.db import models


class Station(models.Model):
    class GeoPrecision(models.TextChoices):
        EXIT = "exit", "Highway exit (OSM motorway_junction)"
        CITY = "city", "City centroid"

    opis_id = models.PositiveIntegerField(unique=True)
    name = models.CharField(max_length=255)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=2, db_index=True)
    rack_id = models.PositiveIntegerField(null=True, blank=True)
    price = models.DecimalField(max_digits=7, decimal_places=4, help_text="USD per gallon")
    location = models.PointField(geography=True, srid=4326)
    geo_precision = models.CharField(max_length=8, choices=GeoPrecision.choices)

    class Meta:
        ordering = ["opis_id"]

    def __str__(self):
        return f"{self.name} ({self.city}, {self.state}) ${self.price}"
