"""Shared HTTP route-matching patterns.

Used by both the deterministic ``summary.api_endpoints`` sweep
(``application_summary.extract_api_endpoints``) and the generic
``api_endpoint_generic`` regex adapter (``adapters/registry.py``) so the two
can never see a different set of routes in the same source tree. Each
pattern captures ``path`` (always) and ``method`` (where the route
declaration syntax carries one).
"""

from __future__ import annotations

import re

ROUTE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # FastAPI / Flask-blueprint verb decorators: @app.get("/x"), @router.post('/x')
    re.compile(
        r"@(?:app|router)\.(?P<method>get|post|put|patch|delete|options|head)\(\s*"
        r"[\"'](?P<path>[^\"']+)[\"']"
    ),
    # Flask .route() decorator: @app.route("/x"), @blueprint.route('/x')
    re.compile(r"@(?:app|blueprint)\.route\(\s*[\"'](?P<path>[^\"']+)[\"']"),
    # Functional / Express-style registration: app.get('/x', ...), router.use('/x', ...)
    re.compile(
        r"\b(?:app|router)\.(?P<method>get|post|put|patch|delete|use)\(\s*"
        r"[\"'](?P<path>[^\"']+)[\"']"
    ),
)
