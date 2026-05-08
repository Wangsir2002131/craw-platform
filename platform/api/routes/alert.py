"""Alert API routes.

Forwards to the canonical implementation in platform.alerts.api.routes.alert,
which is backed by the global AlertManager singleton.
"""

from platform.alerts.api.routes.alert import router  # noqa: F401

__all__ = ["router"]
