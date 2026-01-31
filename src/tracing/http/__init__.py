"""
Tracing Module HTTP Integration.

Re-exports HTTP client components for convenient access.
"""

from .http import (
    RequestMetrics,
    TracedHTTPClient,
    TracedResponse,
    traced_delete,
    traced_get,
    traced_http_request,
    traced_patch,
    traced_post,
    traced_put,
)

__all__ = [
    "RequestMetrics",
    "TracedHTTPClient",
    "TracedResponse",
    "traced_http_request",
    "traced_get",
    "traced_post",
    "traced_put",
    "traced_patch",
    "traced_delete",
]
