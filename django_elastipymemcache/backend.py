import logging
from typing import Any, Sequence

from django.core.cache import InvalidCacheBackendError
from django.core.cache.backends.memcached import PyMemcacheCache
from django.utils.functional import cached_property

from .client import _AWS_CONFIGURATION_ENDPOINT_PATTERN, AWSElastiCacheClient

logger = logging.getLogger(__name__)


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
        return self._class(
            configuration_endpoint=self._endpoint,
            **self._options,  # type: ignore[attr-defined]
        )

    def _safe_close(self, **kwargs: Any) -> None:
        client = self.__dict__.pop("_cache", None)
        if not client:
            return

        try:
            client.close()
        except Exception as e:
            logger.warning("Exception occurred while closing ElastiCache client: %s", e)
