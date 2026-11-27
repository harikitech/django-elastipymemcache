import logging
import os
import threading
from typing import Any, Sequence

from django.core.cache import InvalidCacheBackendError
from django.core.cache.backends.memcached import PyMemcacheCache
from django.utils.functional import cached_property

from .client import _AWS_CONFIGURATION_ENDPOINT_PATTERN, AWSElastiCacheClient

logger = logging.getLogger(__name__)

# Django creates a cache instance per context under ASGI, so pooled clients are shared per process to reuse the pool.
_shared_clients: list[tuple[str, dict[str, Any], AWSElastiCacheClient]] = []
_shared_clients_lock = threading.Lock()


def _reset_shared_clients_lock_after_fork() -> None:
    global _shared_clients_lock
    _shared_clients_lock = threading.Lock()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_shared_clients_lock_after_fork)


class ElastiPymemcache(PyMemcacheCache):
    def __init__(
        self,
        server: str | Sequence[str],
        params: dict[str, Any],
    ) -> None:
        super().__init__(server, params)
        self._class = AWSElastiCacheClient
        self._endpoint = self._validate_endpoint()

    def _validate_endpoint(self) -> str:
        if not self._servers or len(self._servers) != 1:  # type: ignore[attr-defined]
            raise InvalidCacheBackendError("ElastiCache requires exactly one Configuration Endpoint (host:port).")

        endpoint = self._servers[0]  # type: ignore[attr-defined]
        if not isinstance(endpoint, str) or not _AWS_CONFIGURATION_ENDPOINT_PATTERN.fullmatch(endpoint):
            raise InvalidCacheBackendError(f"Invalid Configuration Endpoint '{endpoint}'. Expected 'host:port'.")
        return endpoint

    @cached_property
    def _cache(self) -> AWSElastiCacheClient:
        options = self._options  # type: ignore[attr-defined]
        if not options.get("use_pooling"):
            return self._class(configuration_endpoint=self._endpoint, **options)

        with _shared_clients_lock:
            for endpoint, shared_options, client in _shared_clients:
                if endpoint == self._endpoint and shared_options == options:
                    return client

            client = self._class(configuration_endpoint=self._endpoint, **options)
            _shared_clients.append((self._endpoint, options, client))
            return client

    def _safe_close(self, **kwargs: Any) -> None:
        client = self.__dict__.pop("_cache", None)
        if not client:
            return

        try:
            client.close()
        except Exception as e:
            logger.warning("Exception occurred while closing ElastiCache client: %s", e)
