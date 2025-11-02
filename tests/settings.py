SECRET_KEY = "test"
INSTALLED_APPS: list[str] = []
CACHES = {
    "default": {
        "BACKEND": "django_elastipymemcache.backend.ElastiPymemcache",
        "LOCATION": "localhost:11211",
    }
}
