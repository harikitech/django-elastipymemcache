from unittest.mock import Mock, patch

import django
from django.core.cache import InvalidCacheBackendError
from nose.tools import eq_, raises

from django_elastipymemcache.client import ConfigurationEndpointClient


@raises(InvalidCacheBackendError)
def test_multiple_servers():
    from django_elastipymemcache.backend import ElastiPymemcache
    ElastiPymemcache('h1:0,h2:0', {})


@raises(InvalidCacheBackendError)
def test_wrong_server_format():
    from django_elastipymemcache.backend import ElastiPymemcache
    ElastiPymemcache('h', {})


@patch.object(ConfigurationEndpointClient, 'get_cluster_info')
def test_split_servers(get_cluster_info):
    from django_elastipymemcache.backend import ElastiPymemcache
    backend = ElastiPymemcache('h:0', {})
    servers = [('h1', 0), ('h2', 0)]
    get_cluster_info.return_value = {
        'nodes': servers
    }
    backend._class = Mock()
    assert backend._cache
    get_cluster_info.assert_called()
    backend._class.assert_called_once()
    eq_(backend._class.call_args[0], (servers,))


@patch.object(ConfigurationEndpointClient, 'get_cluster_info')
def test_node_info_cache(get_cluster_info):
    from django_elastipymemcache.backend import ElastiPymemcache
    servers = [('h1', 0), ('h2', 0)]
    get_cluster_info.return_value = {
        'nodes': servers
    }

    backend = ElastiPymemcache('h:0', {})
    backend._class = Mock()
    backend.set('key1', 'val')
    backend.get('key1')
    backend.set('key2', 'val')
    backend.get('key2')
    backend._class.assert_called_once()
    eq_(backend._class.call_args[0], (servers,))
    eq_(backend._cache.get.call_count, 2)
    eq_(backend._cache.set.call_count, 2)

    get_cluster_info.assert_called_once()


@patch.object(ConfigurationEndpointClient, 'get_cluster_info')
def test_failed_to_connect_servers(get_cluster_info):
    from django_elastipymemcache.backend import ElastiPymemcache
    backend = ElastiPymemcache('h:0', {})
    get_cluster_info.side_effect = OSError()
    eq_(backend.client_servers, [])


@patch.object(ConfigurationEndpointClient, 'get_cluster_info')
def test_invalidate_cache(get_cluster_info):
    from django_elastipymemcache.backend import ElastiPymemcache
    servers = [('h1', 0), ('h2', 0)]
    get_cluster_info.return_value = {
        'nodes': servers
    }

    backend = ElastiPymemcache('h:0', {})
    # backend._class = Mock()
    assert backend._cache
    backend._cache.get = Mock()
    backend._cache.get.side_effect = Exception()
    try:
        backend.get('key1', 'val')
    except Exception:
        pass
    # This should have removed the _cache instance
    assert '_cache' not in backend.__dict__
    # Again
    backend._cache.get = Mock()
    backend._cache.get.side_effect = Exception()
    try:
        backend.get('key1', 'val')
    except Exception:
        pass
    assert '_cache' not in backend.__dict__
    assert backend._cache
    eq_(get_cluster_info.call_count, 3)


@patch.object(ConfigurationEndpointClient, 'get_cluster_info')
def test_client_add(get_cluster_info):
    from django_elastipymemcache.backend import ElastiPymemcache

    servers = [('h1', 0), ('h2', 0)]
    get_cluster_info.return_value = {
        'nodes': servers
    }

    backend = ElastiPymemcache('h:0', {})
    ret = backend.add('key1', 'value1')
    eq_(ret, False)


@patch.object(ConfigurationEndpointClient, 'get_cluster_info')
def test_client_delete(get_cluster_info):
    from django_elastipymemcache.backend import ElastiPymemcache

    servers = [('h1', 0), ('h2', 0)]
    get_cluster_info.return_value = {
        'nodes': servers
    }

    backend = ElastiPymemcache('h:0', {})
    ret = backend.delete('key1')
    if django.get_version() >= '3.1':
        eq_(ret, False)
    else:
        eq_(ret, None)


@patch.object(ConfigurationEndpointClient, 'get_cluster_info')
def test_client_get_many(get_cluster_info):
    from django_elastipymemcache.backend import ElastiPymemcache

    servers = [('h1', 0), ('h2', 0)]
    get_cluster_info.return_value = {
        'nodes': servers
    }

    backend = ElastiPymemcache('h:0', {})
    ret = backend.get_many(['key1'])
    eq_(ret, {})

    # When server does not found...
    with patch('pymemcache.client.hash.HashClient._get_client') as p:
        p.return_value = None
        ret = backend.get_many(['key2'])
        eq_(ret, {})

    with patch('pymemcache.client.hash.HashClient._safely_run_func') as p2:
        p2.return_value = {
            ':1:key3': 1509111630.048594
        }

        ret = backend.get_many(['key3'])
        eq_(ret, {'key3': 1509111630.048594})

    # If False value is included, include it.
    with patch('pymemcache.client.hash.HashClient.get_multi') as p:
        p.return_value = {
            ':1:key1': 1509111630.048594,
            ':1:key2': False,
            ':1:key3': 1509111630.058594,
        }
        ret = backend.get_many(['key1', 'key2', 'key3'])
        eq_(
            ret,
            {
                'key1': 1509111630.048594,
                'key2': False,
                'key3': 1509111630.058594
            },
        )

    # Even None is valid. Only key not found is not returned.
    with patch('pymemcache.client.hash.HashClient.get_multi') as p:
        p.return_value = {
            ':1:key1': None,
            ':1:key2': 1509111630.048594,
        }
        ret = backend.get_many(['key1', 'key2', 'key3'])
        eq_(
            ret,
            {
                'key1': None,
                'key2': 1509111630.048594,
            },
        )


@patch.object(ConfigurationEndpointClient, 'get_cluster_info')
def test_client_set_many(get_cluster_info):
    from django_elastipymemcache.backend import ElastiPymemcache

    servers = [('h1', 0), ('h2', 0)]
    get_cluster_info.return_value = {
        'nodes': servers
    }

    backend = ElastiPymemcache('h:0', {})
    ret = backend.set_many({'key1': 'value1', 'key2': 'value2'})
    eq_(ret, ['key1', 'key2'])


@patch.object(ConfigurationEndpointClient, 'get_cluster_info')
def test_client_delete_many(get_cluster_info):
    from django_elastipymemcache.backend import ElastiPymemcache

    servers = [('h1', 0), ('h2', 0)]
    get_cluster_info.return_value = {
        'nodes': servers
    }

    backend = ElastiPymemcache('h:0', {})
    ret = backend.delete_many(['key1', 'key2'])
    eq_(ret, None)


@patch.object(ConfigurationEndpointClient, 'get_cluster_info')
def test_client_incr(get_cluster_info):
    from django_elastipymemcache.backend import ElastiPymemcache

    servers = [('h1', 0), ('h2', 0)]
    get_cluster_info.return_value = {
        'nodes': servers
    }

    backend = ElastiPymemcache('h:0', {})
    ret = backend.incr('key1', 1)
    eq_(ret, False)


@patch.object(ConfigurationEndpointClient, 'get_cluster_info')
def test_client_decr(get_cluster_info):
    from django_elastipymemcache.backend import ElastiPymemcache

    servers = [('h1', 0), ('h2', 0)]
    get_cluster_info.return_value = {
        'nodes': servers
    }

    backend = ElastiPymemcache('h:0', {})
    ret = backend.decr('key1', 1)
    eq_(ret, False)
