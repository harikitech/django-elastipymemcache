"""
Backend for django cache
"""
import logging
import socket
from functools import wraps

from django.core.cache import InvalidCacheBackendError
from django.core.cache.backends.memcached import PyMemcacheCache

from .client import ConfigurationEndpointClient

logger = logging.getLogger(__name__)


def invalidate_cache_after_error(f):
    """
    Catch any exception and invalidate internal cache with list of nodes
    """
    @wraps(f)
    def wrapper(self, *args, **kwds):
        try:
            return f(self, *args, **kwds)
        except Exception:
            self.clear_cluster_nodes_cache()
            raise
    return wrapper


class ElastiPymemcache(PyMemcacheCache):
    """
    Backend for Amazon ElastiCache (memcached) with auto discovery mode
    it used pymemcache
    """
    def __init__(self, server, params):

        super().__init__(server, params)

        self._options.setdefault('ignore_exc', True)

        self._cluster_timeout = self._options.pop(
            'cluster_timeout',
            socket._GLOBAL_DEFAULT_TIMEOUT,
        )
        self._ignore_cluster_errors = self._options.pop(
            'ignore_cluster_errors',
            False,
        )

        if len(self._servers) > 1:
            raise InvalidCacheBackendError(
                'ElastiCache should be configured with only one server '
                '(Configuration Endpoint)',
            )
        try:
            host, port = self._servers[0].split(':')
            port = int(port)
        except ValueError:
            raise InvalidCacheBackendError(
                'Server configuration should be in format IP:Port',
            )

        self.configuration_endpoint_client = ConfigurationEndpointClient(
            (host, port),
            ignore_cluster_errors=self._ignore_cluster_errors,
            **self._options,
        )

    def clear_cluster_nodes_cache(self):
        """Clear internal cache with list of nodes in cluster"""
        try:
            del self._cache
        except AttributeError:
            # self._cache has not been constructed
            pass

    @property
    def client_servers(self):
        try:
            return self.configuration_endpoint_client \
                .get_cluster_info()['nodes']
        except (
            OSError,
            socket.gaierror,
            socket.timeout,
        ) as e:
            logger.warn(
                'Cannot connect to cluster %s, err: %s',
                self.configuration_endpoint_client.server,
                e,
            )
            return []

    @invalidate_cache_after_error
    def add(self, *args, **kwargs):
        return super().add(*args, **kwargs)

    @invalidate_cache_after_error
    def get(self, *args, **kwargs):
        return super().get(*args, **kwargs)

    @invalidate_cache_after_error
    def set(self, *args, **kwargs):
        return super().set(*args, **kwargs)

    @invalidate_cache_after_error
    def delete(self, *args, **kwargs):
        return super().delete(*args, **kwargs)

    @invalidate_cache_after_error
    def get_many(self, *args, **kwargs):
        return super().get_many(*args, **kwargs)

    @invalidate_cache_after_error
    def set_many(self, *args, **kwargs):
        return super().set_many(*args, **kwargs)

    @invalidate_cache_after_error
    def delete_many(self, *args, **kwargs):
        return super().delete_many(*args, **kwargs)

    @invalidate_cache_after_error
    def incr(self, *args, **kwargs):
        return super().incr(*args, **kwargs)

    @invalidate_cache_after_error
    def decr(self, *args, **kwargs):
        return super().decr(*args, **kwargs)
