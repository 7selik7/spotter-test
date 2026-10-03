import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def env(name, default=None):
    return os.environ.get(name, default)


SECRET_KEY = env("DJANGO_SECRET_KEY", "dev-only-insecure-key")
DEBUG = env("DJANGO_DEBUG", "0") == "1"
ALLOWED_HOSTS = env("DJANGO_ALLOWED_HOSTS", "*").split(",")

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "django.contrib.gis",
    "rest_framework",
    "drf_spectacular",
    "stations",
    "api",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]

# default "same-origin" strips the Referer that OSM tile servers require (map page)
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": ["django.template.context_processors.request"]},
    }
]

DATABASES = {
    "default": {
        "ENGINE": "django.contrib.gis.db.backends.postgis",
        "NAME": env("DB_NAME", "fuelroute"),
        "USER": env("DB_USER", "fuelroute"),
        "PASSWORD": env("DB_PASSWORD", "fuelroute"),
        "HOST": env("DB_HOST", "localhost"),
        "PORT": env("DB_PORT", "5432"),
        "CONN_MAX_AGE": 60,
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "UTC"
STATIC_URL = "static/"

REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": [],
    "UNAUTHENTICATED_USER": None,
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Fuel Route API",
    "DESCRIPTION": "Route between two US points with the cheapest fuel stops (500 mi range, 10 mpg).",
    "VERSION": "1.0.0",
}

# --- domain settings ---
VEHICLE_RANGE_MILES = 500
VEHICLE_MPG = 10
# Max distance from the route line to consider a station, by geocoding precision
CORRIDOR_MILES = {"exit": 3, "city": 10}
CORRIDOR_MAX_EXPANSION = 3  # corridor x2 per step if the route has a fuel gap
# Fixed cost of one fuel stop (~15-20 min of driver + truck time). Stops the optimizer
# from pulling over to save a few cents; overridable per request.
FUEL_STOP_PENALTY_USD = 25.0
# Corridor plan uses straight-line positions; the real road through the stops is a bit
# longer (detours). Plan legs with this share of the range, then re-check on real legs.
PLANNING_RANGE_MARGIN = 0.98
# With an empty tank the first station must be within this distance from the start
START_RESERVE_MILES = 50

OSRM_URL = env("OSRM_URL", "https://router.project-osrm.org")
CENSUS_GEOCODER_URL = "https://geocoding.geo.census.gov/geocoder/locations/addressbatch"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
HTTP_TIMEOUT_SECONDS = 15
HTTP_USER_AGENT = "spotter-fuel-route-assessment/1.0"
STATIONS_CSV = BASE_DIR / "data" / "stations_geocoded.csv"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
