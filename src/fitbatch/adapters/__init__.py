"""Platform adapters: one adapter per platform and access method (D1, D4, N11).

The rest of the application only sees the ``PlatformAdapter`` interface and its declared
capabilities; it never knows which platform or method is behind it.
"""

from fitbatch.adapters.base import (
    AdapterError,
    AuthenticationError,
    PlatformAdapter,
    RateLimitError,
)

__all__ = ["AdapterError", "AuthenticationError", "PlatformAdapter", "RateLimitError"]
