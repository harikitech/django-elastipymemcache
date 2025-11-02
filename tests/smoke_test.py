def test_import_and_version() -> None:
    import django_elastipymemcache

    assert hasattr(django_elastipymemcache, "__version__")


def test_backend_importable() -> None:
    from django_elastipymemcache.backend import ElastiPymemcache

    assert ElastiPymemcache is not None


def test_client_importable() -> None:
    from django_elastipymemcache.client import (
        AWSElastiCacheClient,
        _ConfigurationEndpointClient,
    )

    assert AWSElastiCacheClient is not None
    assert _ConfigurationEndpointClient is not None


def test_client_basic() -> None:
    from django_elastipymemcache.client import _ConfigurationEndpointClient

    client = _ConfigurationEndpointClient("localhost:11211")
    assert client.configuration_endpoint == "localhost:11211"
