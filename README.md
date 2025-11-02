# django-elastipymemcache

[![Coverage](https://codecov.io/gh/harikitech/django-elastipymemcache/branch/master/graph/badge.svg)](https://codecov.io/gh/harikitech/django-elastipymemcache)

## Overview

**django-elastipymemcache** is a Django cache backend for **Amazon ElastiCache (memcached)** clusters.
It is built on top of [pymemcache](https://github.com/pinterest/pymemcache) and connects to all cluster nodes via
[ElastiCache Auto Discovery](https://docs.aws.amazon.com/AmazonElastiCache/latest/UserGuide/AutoDiscovery.html).

Originally forked from [django-elasticache](https://github.com/gusdan/django-elasticache), this implementation adds:

- Thread-safe topology updates (atomic swaps)
- Auto discovery for scaling events
- Connection pooling (data nodes & config endpoint)
- Optional TLS connectivity
- Compatibility with Django’s cache interface

## Requirements

- Python >= 3.10
- Django >= 4.2
- pymemcache >= 4.0.0

## Installation

Get it from PyPI:

```bash
python3 -m pip install django-elastipymemcache
```

## Usage

### Basic

```python
CACHES = {
    "default": {
        "BACKEND": "django_elastipymemcache.backend.ElastiPymemcache",
        "LOCATION": "[configuration-endpoint]:11211",
        "OPTIONS": {
            "ignore_exc": True,
        },
    }
}
```

### Connection Pooling

```python
CACHES = {
    "default": {
        "BACKEND": "django_elastipymemcache.backend.ElastiPymemcache",
        "LOCATION": "[configuration-endpoint]:11211",
        "OPTIONS": {
            # Enable pooling for both config endpoint and data nodes
            "use_pooling": True,
            "max_pool_size": 50,
            "pool_idle_timeout": 30,
            "connect_timeout": 0.3,
            "timeout": 0.5,
            "ignore_exc": True,
        },
    }
}
```

### Auto Discovery (with pooling)

```python
CACHES = {
    "default": {
        "BACKEND": "django_elastipymemcache.backend.ElastiPymemcache",
        "LOCATION": "[configuration-endpoint]:11211",
        "OPTIONS": {
            "use_pooling": True,
            "discovery_interval": 60.0,
            "discovery_retry_delay": 2.0,
            "ignore_exc": True,
        },
    }
}
```

## Options

The backend accepts a combination of **ElastiPymemcache-specific options** and
**pymemcache client options**. For the complete list of pymemcache options, see:
<https://pymemcache.readthedocs.io/>

### ElastiPymemcache-specific options

| Option                  | Type  | Default | Description                                                        |
| ----------------------- | ----- | ------- | ------------------------------------------------------------------ |
| `discovery_interval`    | float | `0.0`   | Periodic auto-discovery interval in seconds. Set `0.0` to disable. |
| `discovery_retry_delay` | float | `0.0`   | Delay (seconds) before retrying discovery after failure.           |
| `use_vpc_ip_address`    | bool  | `True`  | Prefer VPC private IPs over DNS hostnames (recommended on AWS).    |

### Notes

- According to the official Amazon ElastiCache documentation, **auto-discovery must be enabled to support vertical scaling**.
  <https://docs.aws.amazon.com/AmazonElastiCache/latest/dg/Scaling-self-designed.mem-heading.html>
- Auto-discovery also runs **on demand** when the ring is empty, even if `discovery_interval` is `0.0`.
  This helps recover after scale events.
- If you use TLS, pass the appropriate `tls_context` through `OPTIONS` (this is a pymemcache option)
  and ensure your ElastiCache cluster supports TLS.

## Notice

### Datadog `ddtrace` & `pymemcache` instrumentation (temporary workaround)

When using `ddtrace` with Django or other frameworks, enabling the `pymemcache` integration may trigger runtime errors such as:

```text
ValueError: wrapper has not been initialized
```

This issue occurs due to `wrapt` interfering with class initialization order inside `ddtrace`’s `pymemcache` integration.
Until Datadog releases a fix, disable the `pymemcache` tracer.

#### Environment variable

```sh
DD_TRACE_PYMEMCACHE_ENABLED=false
```

#### Code-level patch

```python
patch_all(pymemcache=False)
```
