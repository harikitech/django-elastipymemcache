"""
Derived from pymemcache's AWS ElastiCache client

Copy from: https://github.com/pinterest/pymemcache/blob/master/pymemcache/client/ext/aws_ec_client.py
"""

import logging
import random
import re
import socket
import threading
import time
from types import ModuleType
from typing import Any, Callable, Concatenate, ParamSpec, TypeVar

from django.utils.encoding import force_str
from pymemcache import MemcacheUnknownCommandError
from pymemcache.client import Client, PooledClient, RetryingClient
from pymemcache.client.hash import HashClient
from pymemcache.client.rendezvous import RendezvousHash
from pymemcache.exceptions import MemcacheError

logger = logging.getLogger(__name__)

# Accept either host:port or [IPv4]:port
_AWS_CONFIGURATION_ENDPOINT_PATTERN = re.compile(
    r"^(?:(?:[\w\d-]{0,61}[\w\d]\.)+[\w]{1,6}|\[(?:[\d]{1,3}\.){3}[\d]{1,3}\]):\d{1,5}$"
)


class _ConfigurationEndpointClient:
    """ElastiCache's configuration endpoint client."""

    client_class = Client

    #: default: prefer VPC IPs (index 1). FQDN==0, IP==1
    DEFAULT_VPC_ADDRESS_INDEX = 1

    def __init__(
        self,
        configuration_endpoint: str,
        default_kwargs: dict[str, Any] | None = None,
        use_pooling: bool = False,
        use_vpc_ip_address: bool = True,
    ) -> None:
        self.configuration_endpoint = configuration_endpoint
        host, port = self.configuration_endpoint.rsplit(":", 1)
        self._server = (host, int(port))

        self._default_kwargs = default_kwargs or {}
        self._use_pooling = bool(use_pooling)
        self._use_vpc_ip_address = use_vpc_ip_address

        self._lock = threading.Lock()
        self._client: PooledClient | None = None

    def _new_client(self) -> Client:
        client_class = PooledClient if self._use_pooling else self.client_class
        client = client_class(self._server, **self._default_kwargs)
        if self._use_pooling and isinstance(client, PooledClient):
            client.client_class = self.client_class
        return client

    def _get_client(self) -> Client | PooledClient:
        if not self._use_pooling:
            return self._new_client()

        with self._lock:
            if self._client is None:
                self._client = self._new_client()
            return self._client

    def _recycle_client(self) -> None:
        if not self._use_pooling:
            return

        with self._lock:
            if self._client is not None:
                try:
                    self._client.close()
                finally:
                    self._client = None

    def _raw_config_get_cluster(self, client: Client | PooledClient) -> bytes:
        return bytes(
            client.raw_command(
                b"config get cluster",
                end_tokens=b"\n\r\nEND\r\n",
            )
        )

    def _parse_config_get_cluster_response(self, response: bytes) -> list[tuple[str, int]]:
        lines = [force_str(line.strip()) for line in response.splitlines() if line.strip()]

        if not lines:
            raise MemcacheError("ElastiCache discovery: empty response")
        elif len(lines) < 3:
            logger.warning("ElastiCache discovery: response too short: %r", lines)
            raise MemcacheError(f"ElastiCache discovery: response too short: {len(lines)}")
        elif "END" not in lines:
            logger.warning("ElastiCache discovery: response missing END token: %r", lines)
            raise MemcacheError("ElastiCache discovery: response missing END token")

        membership_line = lines[lines.index("END") - 1]
        if not membership_line:
            logger.warning("ElastiCache discovery: no membership line in response: %r", lines)
            raise MemcacheError("ElastiCache discovery: no membership line found")

        nodes: list[tuple[str, int]] = []
        for token in membership_line.split(" "):
            try:
                host, ip, port = token.split("|")
            except ValueError:
                logger.warning("ElastiCache discovery: bad node format in token: %r", token)
                continue

            addr = self._use_vpc_ip_address and ip or host
            nodes.append((addr, int(port)))
        if not nodes:
            logger.warning(
                "ElastiCache discovery: no nodes parsed from response: %r",
                membership_line,
            )
            raise MemcacheError("ElastiCache discovery: no nodes parsed")

        return nodes

    def config_get_cluster(self) -> list[tuple[str, int]]:
        client = self._get_client()
        try:
            response = self._raw_config_get_cluster(client)
        except Exception:
            logger.warning("ElastiCache discovery: config get cluster failed", exc_info=True)
            self._recycle_client()
            raise
        finally:
            if not self._use_pooling:
                try:
                    client.close()
                except Exception:
                    pass

        return self._parse_config_get_cluster_response(response)

    def close(self) -> None:
        self._recycle_client()


P = ParamSpec("P")
R = TypeVar("R")


def _retry_refresh_clients(
    method: Callable[Concatenate["AWSElastiCacheClient", P], R],
) -> Callable[Concatenate["AWSElastiCacheClient", P], R]:
    def wrapped(
        self: "AWSElastiCacheClient",
        /,
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> R:
        last_exception: Exception | None = None

        for _ in range(self.retry_attempts + 1):
            try:
                return method(self, *args, **kwargs)
            except (MemcacheError, OSError) as exc:
                last_exception = exc
                if getattr(self, "_discovery_retry_delay", 0.0) > 0.0:
                    time.sleep(self._discovery_retry_delay)
                try:
                    self._refresh_clients(force=True)
                except Exception as refresh_exc:
                    logger.debug("Discovery refresh failed during retry: %r", refresh_exc)

        assert last_exception is not None
        raise last_exception

    return wrapped


class AWSElastiCacheClient(HashClient):  # type: ignore[misc]
    """ElastiCache-aware HashClient with"""

    def __init__(
        self,
        configuration_endpoint: str,
        # Data client & behavior
        hasher: type[RendezvousHash] = RendezvousHash,
        serde: object | None = None,
        serializer: object | None = None,
        deserializer: object | None = None,
        connect_timeout: float | None = None,
        timeout: float | None = None,
        no_delay: bool = False,
        socket_module: ModuleType = socket,
        socket_keepalive: object | None = None,
        key_prefix: bytes = b"",
        max_pool_size: int | None = None,
        pool_idle_timeout: int = 0,
        lock_generator: object | None = None,
        retry_attempts: int = 2,
        retry_timeout: int = 1,
        dead_timeout: int = 60,
        use_pooling: bool = False,
        ignore_exc: bool = False,
        allow_unicode_keys: bool = False,
        default_noreply: bool = True,
        encoding: str = "ascii",
        tls_context: object | None = None,
        # Discovery & topology management
        use_vpc_ip_address: bool = True,
        discovery_interval: float | int = 0.0,
        discovery_retry_delay: float | int = 0.0,
    ) -> None:
        if not _AWS_CONFIGURATION_ENDPOINT_PATTERN.fullmatch(configuration_endpoint):
            raise ValueError(
                f"Invalid configuration endpoint '{configuration_endpoint}' (expected 'host:port' or '[ip]:port')."
            )

        self.configuration_endpoint: str = configuration_endpoint

        # HashClient core fields (names, semantics)
        self.clients: dict[str, Client] = {}
        self.retry_attempts: int = retry_attempts
        self.retry_timeout: int = retry_timeout
        self.dead_timeout: int = dead_timeout
        self.use_pooling: bool = use_pooling
        self.key_prefix: bytes = key_prefix
        self.ignore_exc: bool = ignore_exc
        self.allow_unicode_keys: bool = allow_unicode_keys

        self._failed_clients: dict[tuple[str, int], Client] = {}
        self._dead_clients: dict[tuple[str, int], Client] = {}
        self._last_dead_check_time: float = time.time()
        self.hasher = hasher()

        self.default_kwargs = {
            "connect_timeout": connect_timeout,
            "timeout": timeout,
            "no_delay": no_delay,
            "socket_module": socket_module,
            "socket_keepalive": socket_keepalive,
            "key_prefix": key_prefix,
            "serde": serde,
            "serializer": serializer,
            "deserializer": deserializer,
            "allow_unicode_keys": allow_unicode_keys,
            "default_noreply": default_noreply,
            "encoding": encoding,
            "tls_context": tls_context,
        }

        if use_pooling:
            self.default_kwargs.update(
                {
                    "max_pool_size": max_pool_size,
                    "pool_idle_timeout": pool_idle_timeout,
                    "lock_generator": lock_generator,
                }
            )

        self.encoding = encoding
        self.tls_context = tls_context

        configuration_endpoint_client = _ConfigurationEndpointClient(
            configuration_endpoint,
            default_kwargs=self.default_kwargs,
            use_pooling=use_pooling,
            use_vpc_ip_address=use_vpc_ip_address,
        )

        self._configuration_endpoint_client = RetryingClient(
            configuration_endpoint_client,
            attempts=retry_attempts,
            retry_delay=discovery_retry_delay,
            do_not_retry_for=(MemcacheUnknownCommandError,),
        )
        self._use_auto_discovery = bool(discovery_interval)
        self._discovery_interval = (
            self._use_auto_discovery
            # Jitter discovery interval
            and float(discovery_interval) * random.uniform(0.8, 1.2)
            or float(discovery_interval)
        )
        self._discovery_retry_delay = float(discovery_retry_delay)
        self._last_discovery_time: float = 0.0
        self._topology_lock = threading.Lock()
        try:
            self._refresh_clients(force=True)
        except Exception as e:
            logger.exception(f"Initial discovery failed: {e}")

    def _discover_client_keys(self) -> set[str]:
        node = self._configuration_endpoint_client.config_get_cluster()
        return set(map(self._make_client_key, node))

    def _refresh_clients(self, force: bool = False) -> None:
        if not force and not self._use_auto_discovery:
            return

        now = time.monotonic()
        if not force and (now - self._last_discovery_time) < self._discovery_interval:
            return

        old_clients: list[Client | PooledClient] = []

        with self._topology_lock:
            current_keys = set(self.clients.keys())
            new_keys = self._discover_client_keys()

            # remove
            for client_key in current_keys - new_keys:
                old_client = self.clients.pop(client_key, None)
                if old_client:
                    old_clients.append(old_client)

                self.hasher.remove_node(client_key)

                host, port = client_key.split(":", 1)
                server = (host, int(port))
                self._failed_clients.pop(server, None)
                self._dead_clients.pop(server, None)

            # add
            for client_key in new_keys - current_keys:
                host, port = client_key.split(":", 1)
                super().add_server((host, int(port)))

            self._last_discovery_time = now

        for old_client in old_clients:
            try:
                old_client.close()
            except Exception:
                logger.exception("Failed to close during topology refresh")

    @_retry_refresh_clients
    def _get_client(self, key: str) -> Client | PooledClient:
        self._refresh_clients()
        return super()._get_client(key)
