import pytest
from pymemcache.exceptions import MemcacheError

from django_elastipymemcache.client import _ConfigurationEndpointClient

from .conftest import FakeSocketModule

EXAMPLE_RESPONSE = (
    b"CONFIG cluster 0 147\r\n"
    b"12\n"
    b"test.0001.use1.cache.amazonaws.com|10.0.0.1|11211 "
    b"test.0002.use1.cache.amazonaws.com|10.0.0.2|11211\n\r\n"
    b"END\r\n"
)


def _client(
    use_vpc_ip: bool,
    socket_module: FakeSocketModule,
) -> _ConfigurationEndpointClient:
    return _ConfigurationEndpointClient(
        configuration_endpoint="config.use1.cache.amazonaws.com:11211",
        default_kwargs={
            "socket_module": socket_module,
        },
        use_pooling=False,
        use_vpc_ip_address=use_vpc_ip,
    )


def test_raw_command_vpc_ip() -> None:
    fake_socket_module = FakeSocketModule([EXAMPLE_RESPONSE])
    client = _client(use_vpc_ip=True, socket_module=fake_socket_module)

    nodes = client.config_get_cluster()

    assert nodes == [
        ("10.0.0.1", 11211),
        ("10.0.0.2", 11211),
    ]
    assert fake_socket_module.sockets, "client did not open a socket"
    assert fake_socket_module.sockets[0].sent[-1] == b"config get cluster\r\n"


def test_raw_command_hostnames() -> None:
    fake_socket_module = FakeSocketModule([EXAMPLE_RESPONSE])
    client = _client(use_vpc_ip=False, socket_module=fake_socket_module)

    nodes = client.config_get_cluster()

    assert nodes == [
        ("test.0001.use1.cache.amazonaws.com", 11211),
        ("test.0002.use1.cache.amazonaws.com", 11211),
    ]


def test_parse_multiline_membership() -> None:
    payload = (
        b"CONFIG cluster 0 147\r\n"
        b"12\n"
        b"test.0001.use1.cache.amazonaws.com|10.0.0.1|11211\n"
        b"test.0002.use1.cache.amazonaws.com|10.0.0.2|11211\n\r\n"
        b"END\r\n"
    )
    fake_socket_module = FakeSocketModule([payload])
    client = _client(use_vpc_ip=True, socket_module=fake_socket_module)

    nodes = client.config_get_cluster()
    assert nodes == [
        ("10.0.0.1", 11211),
        ("10.0.0.2", 11211),
    ]


@pytest.mark.parametrize(
    "payload",
    [
        b"",  # empty reply
        b"CONFIG cluster 0 1\r\nX\r\n",  # only header + bad body
        b"CONFIG cluster 0 1\r\n\n\r\nEND\r\n",  # blank version/body
        b"CONFIG cluster 0 1\r\nbad|format\r\nEND\r\n",  # malformed token
    ],
)
def test_parse_errors_via_raw_command(payload: bytes) -> None:
    fake_socket_module = FakeSocketModule([payload])
    client = _client(use_vpc_ip=True, socket_module=fake_socket_module)

    with pytest.raises(MemcacheError):
        client.config_get_cluster()
