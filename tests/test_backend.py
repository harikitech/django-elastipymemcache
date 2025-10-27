from typing import Callable
from unittest.mock import Mock, patch

import pytest
from django.core.cache import InvalidCacheBackendError
from pytest import MonkeyPatch

from django_elastipymemcache.backend import ElastiPymemcache


@pytest.fixture
def mock_discovery(monkeypatch: MonkeyPatch) -> Callable[[list[tuple[str, int]]], None]:
    def _set(nodes: list[tuple[str, int]]) -> None:
        monkeypatch.setattr(
            "django_elastipymemcache.client._ConfigurationEndpointClient.config_get_cluster",
            lambda self: list(nodes),
        )

    return _set


def test_multiple_servers() -> None:
    with pytest.raises(InvalidCacheBackendError):
        ElastiPymemcache(
            "test.0001.use1.cache.amazonaws.com:11211,test.0002.use1.cache.amazonaws.com:11211",
            {},
        )


def test_wrong_server_format() -> None:
    with pytest.raises(InvalidCacheBackendError):
        ElastiPymemcache(
            "test.0000.use1.cache.amazonaws.com",
            {},
        )


def test_split_servers(
    mock_discovery: Callable[[list[tuple[str, int]]], None],
) -> None:
    servers = [("10.0.0.1", 11211), ("10.0.0.2", 11211)]
    mock_discovery(servers)

    with patch("django_elastipymemcache.backend.AWSElastiCacheClient") as MockClient:
        backend = ElastiPymemcache("test.0000.use1.cache.amazonaws.com:11211", {})

        assert backend._cache
        MockClient.assert_called_once()
        _, kwargs = MockClient.call_args

    assert kwargs["configuration_endpoint"] == "test.0000.use1.cache.amazonaws.com:11211"


def test_node_info_cache(
    mock_discovery: Callable[[list[tuple[str, int]]], None],
) -> None:
    servers = [("10.0.0.1", 11211), ("10.0.0.2", 11211)]
    mock_discovery(servers)

    with patch("django_elastipymemcache.backend.AWSElastiCacheClient") as MockClient:
        mock_client = Mock()
        MockClient.return_value = mock_client

        backend = ElastiPymemcache("test.0000.use1.cache.amazonaws.com:11211", {})

        backend.set("key1", "val")
        backend.get("key1")
        backend.set("key2", "val")
        backend.get("key2")

        assert mock_client.set.call_count == 2
        assert mock_client.get.call_count == 2
        MockClient.assert_called_once()


def test_failed_to_connect_servers(monkeypatch: MonkeyPatch) -> None:
    mock_config_get = Mock(
        side_effect=[
            OSError("boom"),  # 1st call raises
            [("10.0.0.9", 11211)],  # 2nd call returns
        ]
    )

    monkeypatch.setattr(
        "django_elastipymemcache.client._ConfigurationEndpointClient.config_get_cluster",
        mock_config_get,
    )

    backend = ElastiPymemcache("test.0000.use1.cache.amazonaws.com:11211", {})

    client = backend._cache._get_client("test")
    assert client is not None
