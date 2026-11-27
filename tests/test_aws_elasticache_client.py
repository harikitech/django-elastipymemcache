import itertools
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable
from unittest.mock import Mock

import pytest
from pymemcache.client import Client, PooledClient
from pymemcache.exceptions import MemcacheError, MemcacheIllegalInputError
from pytest import MonkeyPatch

from django_elastipymemcache.client import AWSElastiCacheClient

from .conftest import FakeSocketModule

CONFIG_GET_CLUSTER_RESPONSE = (
    b"CONFIG cluster 0 52\r\n1\ntest.0001.use1.cache.amazonaws.com|10.0.0.1|11211\n\r\nEND\r\n"
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


def test_disconnect_all_keeps_pooled_connections() -> None:
    fake_socket_module = FakeSocketModule([CONFIG_GET_CLUSTER_RESPONSE])
    client = make_client(use_pooling=True, socket_module=fake_socket_module)

    client.disconnect_all()

    assert [s.closed for s in fake_socket_module.sockets] == [False]


def test_disconnect_all_logs_configuration_endpoint_close_failure(
    mock_discovery: Callable[[list[tuple[str, int]]], None],
    monkeypatch: MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    mock_discovery([("10.0.0.1", 11211)])
    client = make_client()
    endpoint_client = Mock(**{"close.side_effect": OSError("boom")})
    monkeypatch.setattr(client, "_configuration_endpoint_client", endpoint_client)

    client.disconnect_all()

    endpoint_client.close.assert_called_once_with(force=True)
    assert "Exception occurred while closing configuration endpoint client" in caplog.text


NODE_1 = ("10.0.0.1", 11211)
NODE_2 = ("10.0.0.2", 11211)
KEYS = [f"key{i}" for i in range(20)]


@pytest.fixture
def two_node_client(mock_discovery: Callable[[list[tuple[str, int]]], None]) -> AWSElastiCacheClient:
    mock_discovery([NODE_1, NODE_2])
    client = make_client(use_pooling=True, ignore_exc=True, socket_module=FakeSocketModule([b"END\r\n"]))
    mock_discovery([NODE_2])
    return client


def remove_node_during_get_node(monkeypatch: MonkeyPatch, client: AWSElastiCacheClient) -> None:
    get_node = client.hasher.get_node

    def wrapped(key: str) -> str:
        node = get_node(key)
        if node == "10.0.0.1:11211" and "10.0.0.1:11211" in client.clients:
            client._refresh_clients(force=True)
        return node

    monkeypatch.setattr(client.hasher, "get_node", wrapped)


def remove_node_after_batching(monkeypatch: MonkeyPatch, client: AWSElastiCacheClient) -> None:
    get_client = client._get_client
    calls = []

    def wrapped(key: str) -> Client | PooledClient:
        result = get_client(key)
        calls.append(key)
        if len(calls) == len(KEYS):
            client._refresh_clients(force=True)
        return result

    monkeypatch.setattr(client, "_get_client", wrapped)


def test_get_when_node_removed_during_lookup(monkeypatch: MonkeyPatch, two_node_client: AWSElastiCacheClient) -> None:
    key = next(k for k in KEYS if two_node_client.hasher.get_node(k) == "10.0.0.1:11211")
    remove_node_during_get_node(monkeypatch, two_node_client)

    assert two_node_client.get(key) is None
    assert set(two_node_client.clients) == {"10.0.0.2:11211"}


@pytest.mark.parametrize("remove_node", [remove_node_during_get_node, remove_node_after_batching])
def test_get_multi_when_node_removed(
    monkeypatch: MonkeyPatch,
    two_node_client: AWSElastiCacheClient,
    remove_node: Callable[[MonkeyPatch, AWSElastiCacheClient], None],
) -> None:
    node = two_node_client.clients["10.0.0.2:11211"]
    get_many = Mock(wraps=node.get_many)
    monkeypatch.setattr(node, "get_many", get_many)
    remove_node(monkeypatch, two_node_client)

    assert two_node_client.get_multi(KEYS) == {}
    assert set(two_node_client.clients) == {"10.0.0.2:11211"}
    assert sorted(get_many.call_args.args[0]) == sorted(KEYS)


@pytest.mark.parametrize("remove_node", [remove_node_during_get_node, remove_node_after_batching])
def test_set_multi_when_node_removed(
    monkeypatch: MonkeyPatch,
    two_node_client: AWSElastiCacheClient,
    remove_node: Callable[[MonkeyPatch, AWSElastiCacheClient], None],
) -> None:
    node = two_node_client.clients["10.0.0.2:11211"]
    set_many = Mock(wraps=node.set_many)
    monkeypatch.setattr(node, "set_many", set_many)
    remove_node(monkeypatch, two_node_client)

    assert two_node_client.set_multi(dict.fromkeys(KEYS, "value")) == []
    assert set(two_node_client.clients) == {"10.0.0.2:11211"}
    assert sorted(set_many.call_args.args[0]) == sorted(KEYS)


def test_multi_aliases_use_overridden_methods() -> None:
    assert vars(AWSElastiCacheClient)["get_multi"] is vars(AWSElastiCacheClient)["get_many"]
    assert vars(AWSElastiCacheClient)["set_multi"] is vars(AWSElastiCacheClient)["set_many"]


def test_get_multi_raises_when_data_node_is_down(monkeypatch: MonkeyPatch) -> None:
    config_get_cluster = Mock(return_value=[NODE_1])
    monkeypatch.setattr(
        "django_elastipymemcache.client._ConfigurationEndpointClient.config_get_cluster",
        config_get_cluster,
    )
    socket_module = FakeSocketModule([])
    socket_module.socket = Mock(side_effect=ConnectionRefusedError)  # type: ignore[method-assign]
    client = make_client(use_pooling=True, socket_module=socket_module)
    config_get_cluster.reset_mock()

    with pytest.raises(ConnectionRefusedError):
        client.get_multi(["key"])
    assert config_get_cluster.call_count == 0


def test_get_multi_raises_for_illegal_key(monkeypatch: MonkeyPatch) -> None:
    config_get_cluster = Mock(return_value=[NODE_1])
    monkeypatch.setattr(
        "django_elastipymemcache.client._ConfigurationEndpointClient.config_get_cluster",
        config_get_cluster,
    )
    client = make_client(use_pooling=True)
    config_get_cluster.reset_mock()

    with pytest.raises(MemcacheIllegalInputError):
        client.get_multi(["bad key"])
    assert config_get_cluster.call_count == 0


def test_flush_all_when_node_removed(monkeypatch: MonkeyPatch, two_node_client: AWSElastiCacheClient) -> None:
    safely_run_func = two_node_client._safely_run_func

    def wrapped(*args: Any, **kwargs: Any) -> Any:
        two_node_client._refresh_clients(force=True)
        return safely_run_func(*args, **kwargs)

    monkeypatch.setattr(two_node_client, "_safely_run_func", wrapped)

    two_node_client.flush_all()

    assert set(two_node_client.clients) == {"10.0.0.2:11211"}


def test_remove_server_waits_for_retry_dead(mock_discovery: Callable[[list[tuple[str, int]]], None]) -> None:
    mock_discovery([NODE_1, NODE_2])
    client = make_client(use_pooling=True, ignore_exc=True, dead_timeout=0)
    client.hasher.remove_node("10.0.0.1:11211")
    client._failed_clients[NODE_2] = {"failed_time": 0.0, "attempts": 0}
    removed = threading.Event()

    def remove_server() -> None:
        client.remove_server(NODE_2)
        removed.set()

    class IteratingDict(dict[tuple[str, int], float]):
        def items(self) -> Any:
            for item in super().items():
                yield item
                threading.Thread(target=remove_server).start()
                removed.wait(0.1)

    client._dead_clients = IteratingDict({NODE_1: 0.0})

    client._retry_dead()

    assert removed.wait(1)
    assert set(client._dead_clients) == {NODE_2}


def test_failure_after_refresh_does_not_revive_removed_node(two_node_client: AWSElastiCacheClient) -> None:
    two_node_client._refresh_clients(force=True)

    two_node_client._mark_failed_server(NODE_1)
    two_node_client.remove_server(NODE_1)

    assert NODE_1 not in two_node_client._failed_clients
    assert NODE_1 not in two_node_client._dead_clients


def test_remove_server_twice(two_node_client: AWSElastiCacheClient) -> None:
    two_node_client._failed_clients[NODE_1] = {"failed_time": 0.0, "attempts": 2}

    two_node_client.remove_server(NODE_1)
    two_node_client.remove_server(NODE_1)

    assert set(two_node_client._dead_clients) == {NODE_1}
    assert "10.0.0.1:11211" not in two_node_client.hasher.nodes


@pytest.fixture
def failed_node_client(mock_discovery: Callable[[list[tuple[str, int]]], None]) -> AWSElastiCacheClient:
    mock_discovery([NODE_1])
    client = make_client(use_pooling=True, socket_module=FakeSocketModule([b"VALUE key 0 5\r\nvalue\r\nEND\r\n"]))
    client._failed_clients[NODE_1] = {"failed_time": 0.0, "attempts": 0}
    return client


def test_failed_node_recovered_by_another_thread(
    monkeypatch: MonkeyPatch,
    failed_node_client: AWSElastiCacheClient,
) -> None:
    node = failed_node_client.clients["10.0.0.1:11211"]
    get = node.get

    def wrapped(*args: Any, **kwargs: Any) -> Any:
        failed_node_client._failed_clients.pop(NODE_1)
        return get(*args, **kwargs)

    monkeypatch.setattr(node, "get", wrapped)

    assert failed_node_client.get("key") == b"value"


def test_failed_node_entry_removed_after_check(failed_node_client: AWSElastiCacheClient) -> None:
    class RacingFailedClients(type(failed_node_client._failed_clients)):  # type: ignore[misc]
        def __contains__(self, key: object) -> bool:
            found = super().__contains__(key)
            self.pop(key)
            return found

    failed_node_client._failed_clients = RacingFailedClients(failed_node_client._failed_clients)

    assert failed_node_client.get("key") == b"value"


def refresh_in_threads(
    monkeypatch: MonkeyPatch,
    client: AWSElastiCacheClient,
    second_starts_during_discovery: bool,
) -> tuple[list[threading.Thread], list[threading.Thread]]:
    discovering = threading.Event()
    release = threading.Event()
    calls: list[threading.Thread] = []

    def config_get_cluster(self: Any) -> list[tuple[str, int]]:
        calls.append(threading.current_thread())
        discovering.set()
        release.wait(1)
        return [NODE_1]

    monkeypatch.setattr(
        "django_elastipymemcache.client._ConfigurationEndpointClient.config_get_cluster",
        config_get_cluster,
    )
    first = threading.Thread(target=client._refresh_clients, kwargs={"force": True})
    second = threading.Thread(target=client._refresh_clients, kwargs={"force": True})
    requested = {first: threading.Event(), second: threading.Event()}
    monotonic = time.monotonic

    def wrapped_monotonic() -> float:
        value = monotonic()
        if threading.current_thread() in requested:
            requested[threading.current_thread()].set()
        return value

    monkeypatch.setattr(time, "monotonic", wrapped_monotonic)

    if second_starts_during_discovery:
        first.start()
        assert discovering.wait(1)
        second.start()
        assert requested[second].wait(1)
    else:
        with client._discovery_lock:
            first.start()
            second.start()
            assert requested[first].wait(1)
            assert requested[second].wait(1)
    release.set()
    first.join(1)
    second.join(1)
    return [first, second], calls


def test_refresh_skips_when_discovery_started_after_request(
    monkeypatch: MonkeyPatch,
    mock_discovery: Callable[[list[tuple[str, int]]], None],
) -> None:
    mock_discovery([NODE_1])
    client = make_client(use_pooling=True)

    _, calls = refresh_in_threads(monkeypatch, client, second_starts_during_discovery=False)

    assert len(calls) == 1


def test_refresh_runs_again_when_requested_during_discovery(
    monkeypatch: MonkeyPatch,
    mock_discovery: Callable[[list[tuple[str, int]]], None],
) -> None:
    mock_discovery([NODE_1])
    client = make_client(use_pooling=True)

    threads, calls = refresh_in_threads(monkeypatch, client, second_starts_during_discovery=True)

    assert calls == threads


def test_get_client_is_not_blocked_by_discovery(
    monkeypatch: MonkeyPatch,
    two_node_client: AWSElastiCacheClient,
) -> None:
    two_node_client._failed_clients[NODE_1] = {"failed_time": 0.0, "attempts": 2}
    two_node_client.remove_server(NODE_1)
    discovering = threading.Event()
    release = threading.Event()

    def config_get_cluster(self: Any) -> list[tuple[str, int]]:
        discovering.set()
        release.wait(5)
        return [NODE_1, NODE_2]

    monkeypatch.setattr(
        "django_elastipymemcache.client._ConfigurationEndpointClient.config_get_cluster",
        config_get_cluster,
    )
    refresh = threading.Thread(target=two_node_client._refresh_clients, kwargs={"force": True})
    refresh.start()
    try:
        assert discovering.wait(1)
        clients: list[Client | PooledClient] = []
        lookup = threading.Thread(target=lambda: clients.append(two_node_client._get_client("key")))
        lookup.start()
        lookup.join(0.5)

        assert clients == [two_node_client.clients["10.0.0.2:11211"]]
    finally:
        release.set()
        refresh.join(1)


def test_refresh_and_failure_tracking_do_not_deadlock(
    monkeypatch: MonkeyPatch,
    two_node_client: AWSElastiCacheClient,
) -> None:
    topologies = itertools.cycle([[NODE_1, NODE_2], [NODE_2]])
    monkeypatch.setattr(
        "django_elastipymemcache.client._ConfigurationEndpointClient.config_get_cluster",
        lambda self: next(topologies),
    )
    two_node_client.dead_timeout = 0

    def repeat(func: Callable[[], None]) -> None:
        for _ in range(200):
            func()

    def fail_node() -> None:
        two_node_client._mark_failed_server(NODE_1)
        two_node_client.remove_server(NODE_1)

    def retry_dead() -> None:
        two_node_client._last_dead_check_time = 0.0
        two_node_client._retry_dead()

    executor = ThreadPoolExecutor(max_workers=3)
    try:
        futures = [
            executor.submit(repeat, lambda: two_node_client._refresh_clients(force=True)),
            executor.submit(repeat, fail_node),
            executor.submit(repeat, retry_dead),
        ]
        for future in futures:
            future.result(timeout=5)
    finally:
        executor.shutdown(wait=False)


def test_get_does_not_rediscover_when_node_removed_during_lookup(
    monkeypatch: MonkeyPatch,
    two_node_client: AWSElastiCacheClient,
) -> None:
    config_get_cluster = Mock(return_value=[NODE_2])
    monkeypatch.setattr(
        "django_elastipymemcache.client._ConfigurationEndpointClient.config_get_cluster",
        config_get_cluster,
    )
    key = next(k for k in KEYS if two_node_client.hasher.get_node(k) == "10.0.0.1:11211")
    remove_node_during_get_node(monkeypatch, two_node_client)

    assert two_node_client.get(key) is None
    assert config_get_cluster.call_count == 1


def test_lookup_waits_for_topology_update(monkeypatch: MonkeyPatch, two_node_client: AWSElastiCacheClient) -> None:
    key = next(k for k in KEYS if two_node_client.hasher.get_node(k) == "10.0.0.1:11211")
    node = two_node_client.clients["10.0.0.2:11211"]
    get = Mock(wraps=node.get)
    monkeypatch.setattr(node, "get", get)
    config_get_cluster = Mock(return_value=[NODE_2])
    monkeypatch.setattr(
        "django_elastipymemcache.client._ConfigurationEndpointClient.config_get_cluster",
        config_get_cluster,
    )
    removing = threading.Event()

    def remove_node() -> None:
        with two_node_client._topology_lock:
            two_node_client.clients.pop("10.0.0.1:11211")
            removing.set()
            time.sleep(0.1)
            two_node_client.hasher.remove_node("10.0.0.1:11211")

    thread = threading.Thread(target=remove_node)
    thread.start()
    assert removing.wait(1)

    assert two_node_client.get(key) is None
    thread.join(1)
    assert get.call_args.args[0] == key
    assert config_get_cluster.call_count == 0


@pytest.fixture
def missing_node_client(monkeypatch: MonkeyPatch, two_node_client: AWSElastiCacheClient) -> AWSElastiCacheClient:
    monkeypatch.setattr(two_node_client.hasher, "get_node", lambda key: "10.0.0.9:11211")
    return two_node_client


def test_lookup_returns_default_when_node_is_still_missing(missing_node_client: AWSElastiCacheClient) -> None:
    assert missing_node_client.get("key", "default") == "default"
    assert missing_node_client.get_multi(KEYS) == {}
    assert missing_node_client.set_multi(dict.fromkeys(KEYS, "value")) == KEYS


def test_lookup_raises_when_node_is_still_missing(missing_node_client: AWSElastiCacheClient) -> None:
    missing_node_client.ignore_exc = False

    with pytest.raises(MemcacheError, match="node removed during lookup"):
        missing_node_client.get("key")
    with pytest.raises(MemcacheError, match="node removed during lookup"):
        missing_node_client.get_multi(KEYS)
    with pytest.raises(MemcacheError, match="node removed during lookup"):
        missing_node_client.set_multi(dict.fromkeys(KEYS, "value"))


def test_failed_discovery_keeps_current_nodes(monkeypatch: MonkeyPatch, two_node_client: AWSElastiCacheClient) -> None:
    monkeypatch.setattr(
        "django_elastipymemcache.client._ConfigurationEndpointClient.config_get_cluster",
        Mock(side_effect=MemcacheError("boom")),
    )

    two_node_client._refresh_clients(force=True)

    assert set(two_node_client.clients) == {"10.0.0.1:11211", "10.0.0.2:11211"}
    assert two_node_client._get_client("key") is not None


def test_retry_dead_closes_replaced_client(monkeypatch: MonkeyPatch, two_node_client: AWSElastiCacheClient) -> None:
    old_client = two_node_client.clients["10.0.0.1:11211"]
    close = Mock(wraps=old_client.close)
    monkeypatch.setattr(old_client, "close", close)
    two_node_client._failed_clients[NODE_1] = {"failed_time": 0.0, "attempts": 2}
    two_node_client.remove_server(NODE_1)
    two_node_client.dead_timeout = 0
    two_node_client._last_dead_check_time = 0.0

    two_node_client._retry_dead()

    assert two_node_client.clients["10.0.0.1:11211"] is not old_client
    assert "10.0.0.1:11211" in two_node_client.hasher.nodes
    close.assert_called_once_with()


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires os.fork")
def test_fork_resets_pooled_clients(two_node_client: AWSElastiCacheClient) -> None:
    two_node_client.get_multi(KEYS)
    sockets = two_node_client.default_kwargs["socket_module"].sockets
    assert sockets
    parent_clients = dict(two_node_client.clients)
    parent_locks = (two_node_client._discovery_lock, two_node_client._topology_lock)

    pid = os.fork()
    if pid == 0:
        ok = (
            set(two_node_client.clients) == set(parent_clients)
            and all(two_node_client.clients[key] is not client for key, client in parent_clients.items())
            and two_node_client._discovery_lock is not parent_locks[0]
            and two_node_client._topology_lock is not parent_locks[1]
            and not any(s.closed for s in sockets)
            and two_node_client.get_multi(KEYS) == {}
        )
        os._exit(0 if ok else 1)

    assert os.waitpid(pid, 0)[1] == 0
