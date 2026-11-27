import time
from typing import Any, Callable
from unittest.mock import Mock

import pytest
from pymemcache.client import Client, PooledClient
from pytest import MonkeyPatch

from django_elastipymemcache.client import AWSElastiCacheClient

from .conftest import FakeSocketModule

CONFIG_GET_CLUSTER_RESPONSE = (
    b"CONFIG cluster 0 52\r\n"
    b"1\n"
    b"test.0001.use1.cache.amazonaws.com|10.0.0.1|11211\n\r\n"
    b"END\r\n"
)


@pytest.fixture
def mock_discovery(monkeypatch: MonkeyPatch) -> Callable[[list[tuple[str, int]]], None]:
    def _set(nodes: list[tuple[str, int]]) -> None:
        monkeypatch.setattr(
            "django_elastipymemcache.client._ConfigurationEndpointClient.config_get_cluster",
            lambda self: list(nodes),
        )

    return _set


def make_client(**options: Any) -> AWSElastiCacheClient:
    return AWSElastiCacheClient(
        "test.0000.use1.cache.amazonaws.com:11211",
        **options,
    )


def test_initial_refresh_builds_clients(
    mock_discovery: Callable[[list[tuple[str, int]]], None],
) -> None:
    mock_discovery([("10.0.0.1", 11211), ("10.0.0.2", 11211)])
    client = make_client(discovery_interval=0.0)
    assert len(client.clients) == 2
    assert set(client.clients.keys()) == {"10.0.0.1:11211", "10.0.0.2:11211"}


def test_add_and_remove_nodes(
    mock_discovery: Callable[[list[tuple[str, int]]], None],
) -> None:
    mock_discovery([("10.0.0.1", 11211), ("10.0.0.2", 11211)])
    client = make_client(discovery_interval=0.0)
    client._refresh_clients(force=True)
    assert set(client.clients.keys()) == {"10.0.0.1:11211", "10.0.0.2:11211"}

    mock_discovery([("10.0.0.2", 11211), ("10.0.0.3", 11211)])
    client._refresh_clients(force=True)

    assert set(client.clients.keys()) == {"10.0.0.2:11211", "10.0.0.3:11211"}


def test_periodic_refresh_respects_interval(
    monkeypatch: MonkeyPatch,
    mock_discovery: Callable[[list[tuple[str, int]]], None],
) -> None:
    mock_discovery([("10.0.0.1", 11211)])
    now = time.monotonic()
    mock_monotonic = Mock(return_value=now)
    monkeypatch.setattr(time, "monotonic", mock_monotonic)

    client = make_client(discovery_interval=10.0)
    client._refresh_clients(force=True)
    assert set(client.clients.keys()) == {"10.0.0.1:11211"}
    mock_monotonic.return_value = now + 5.0
    mock_discovery([("10.0.0.2", 11211)])
    client._refresh_clients()
    assert set(client.clients.keys()) == {"10.0.0.1:11211"}

    mock_monotonic.return_value = now + 20.0
    client._refresh_clients()
    assert set(client.clients.keys()) == {"10.0.0.2:11211"}


def test_use_pooling_creates_pooled_clients(
    mock_discovery: Callable[[list[tuple[str, int]]], None],
) -> None:
    mock_discovery([("10.0.0.1", 11211)])
    client = make_client(
        use_pooling=True,
        discovery_interval=0.0,
        max_pool_size=8,
    )
    client._refresh_clients(force=True)
    assert all(isinstance(c, PooledClient) for c in client.clients.values())


def test_get_client_triggers_retry_refresh_when_ring_empty(
    monkeypatch: MonkeyPatch,
    mock_discovery: Callable[[list[tuple[str, int]]], None],
) -> None:
    mock_discovery([])
    client = make_client(discovery_interval=0.0)

    mock_config_get = Mock(
        side_effect=[
            [],  # 1st call
            [("10.0.0.9", 11211)],  # 2nd call
        ]
    )
    monkeypatch.setattr(
        "django_elastipymemcache.client._ConfigurationEndpointClient.config_get_cluster",
        mock_config_get,
    )

    data_node = client._get_client("test")

    assert isinstance(data_node, (Client, PooledClient))


@pytest.mark.parametrize(
    ("discovery_interval", "keep_connection"),
    [
        (0.0, False),
        (10.0, True),
    ],
)
def test_configuration_endpoint_connection_is_kept_only_for_periodic_discovery(
    mock_discovery: Callable[[list[tuple[str, int]]], None],
    discovery_interval: float,
    keep_connection: bool,
) -> None:
    mock_discovery([("10.0.0.1", 11211)])
    client = make_client(use_pooling=True, discovery_interval=discovery_interval)

    assert client._configuration_endpoint_client._client._keep_connection is keep_connection


def test_disconnect_all_closes_configuration_endpoint_connection() -> None:
    fake_socket_module = FakeSocketModule([CONFIG_GET_CLUSTER_RESPONSE])
    # Periodic discovery keeps the endpoint connection open, so only disconnect_all can close it here.
    client = make_client(use_pooling=True, discovery_interval=10.0, socket_module=fake_socket_module)
    assert [s.closed for s in fake_socket_module.sockets] == [False]

    client.disconnect_all()

    assert [s.closed for s in fake_socket_module.sockets] == [True]
