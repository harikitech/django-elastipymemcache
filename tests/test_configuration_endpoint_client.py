import pytest
from pymemcache.exceptions import MemcacheError
from pytest import MonkeyPatch

from django_elastipymemcache.client import _ConfigurationEndpointClient

EXAMPLE_RESPONSE = (
    b"CONFIG cluster 0 147\r\n"
    b"12\n"
    b"test.0001.use1.cache.amazonaws.com|10.82.235.120|11211 "
    b"test.0002.use1.cache.amazonaws.com|10.80.249.27|11211\n\r\n"
    b"END\r\n"
)


def _client(use_vpc_ip: bool = True) -> _ConfigurationEndpointClient:
    return _ConfigurationEndpointClient(
        configuration_endpoint="config.example:11211",
        default_kwargs={},
        use_pooling=False,
        use_vpc_ip_address=use_vpc_ip,
    )


def test_parse_ok_with_vpc_ip(monkeypatch: MonkeyPatch) -> None:
    client = _client(use_vpc_ip=True)
    monkeypatch.setattr(client, "_raw_config_get_cluster", lambda _client: EXAMPLE_RESPONSE)

    nodes = client.config_get_cluster()
    assert nodes == [("10.82.235.120", 11211), ("10.80.249.27", 11211)]


def test_parse_ok_with_hostnames(monkeypatch: MonkeyPatch) -> None:
    client = _client(use_vpc_ip=False)
    monkeypatch.setattr(client, "_raw_config_get_cluster", lambda _client: EXAMPLE_RESPONSE)

    nodes = client.config_get_cluster()
    assert nodes == [
        ("test.0001.use1.cache.amazonaws.com", 11211),
        ("test.0002.use1.cache.amazonaws.com", 11211),
    ]


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"CONFIG cluster 0 1\r\nX\r\n",
        b"CONFIG cluster 0 1\r\n\n\r\nEND\r\n",
        b"CONFIG cluster 0 1\r\nbad|format\r\nEND\r\n",
    ],
)
def test_parse_errors(monkeypatch: MonkeyPatch, payload: bytes) -> None:
    client = _client()
    monkeypatch.setattr(client, "_raw_config_get_cluster", lambda _client: payload)

    with pytest.raises(MemcacheError):
        client.config_get_cluster()
