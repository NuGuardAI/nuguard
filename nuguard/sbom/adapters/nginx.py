"""Nginx configuration adapter — extracts DEPLOYMENT and AUTH nodes.

Triggered for files named ``nginx.conf``, ``default.conf``, ``site.conf``,
or matching the glob ``*.nginx``.  The extractor calls ``NginxAdapter.scan()``
directly (not via the FrameworkAdapter chain).

Detected patterns
-----------------
``proxy_pass http(s)://…``
    Upstream forwarding — emits a DEPLOYMENT node.  The target URL is stored
    in ``metadata.upstream_url``.

``listen <port> ssl`` / ``listen [::]:443 ssl``
    TLS termination detected — emits an AUTH node with ``auth_kind=tls``.

``ssl_certificate …``
    Certificate path confirmed — reinforces or augments the AUTH node.

``server_name …``
    Virtual-host name(s) stored as metadata on the DEPLOYMENT node.
"""

from __future__ import annotations

import logging
import re

from xelo.adapters.base import ComponentDetection
from xelo.types import ComponentType

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

# proxy_pass http://127.0.0.1:8420;  or  proxy_pass https://backend:8000/api/;
_PROXY_PASS_RE = re.compile(
    r"^\s*proxy_pass\s+(?P<url>https?://[^\s;]+)",
    re.IGNORECASE | re.MULTILINE,
)

# listen 443 ssl;  /  listen [::]:443 ssl;  /  listen 80;
_LISTEN_RE = re.compile(
    r"^\s*listen\s+(?:\[::\]:)?(?P<port>\d+)(?:\s+(?P<flags>[^;#\n]+))?",
    re.IGNORECASE | re.MULTILINE,
)

# ssl_certificate /etc/nginx/ssl/cert.pem;
_SSL_CERT_RE = re.compile(
    r"^\s*ssl_certificate\s+(?P<path>[^\s;]+)",
    re.IGNORECASE | re.MULTILINE,
)

# server_name example.com www.example.com;
_SERVER_NAME_RE = re.compile(
    r"^\s*server_name\s+(?P<names>[^;#\n]+)",
    re.IGNORECASE | re.MULTILINE,
)

# Filenames that trigger this adapter (checked by the extractor)
_NGINX_FILENAME_RE = re.compile(
    r"(?:^|/)(?:nginx\.conf|default\.conf|site\.conf|[^/]+\.nginx)$",
    re.IGNORECASE,
)


def is_nginx_file(rel_path: str) -> bool:
    """Return True if *rel_path* looks like an nginx config file."""
    return bool(_NGINX_FILENAME_RE.search(rel_path.replace("\\", "/")))


class NginxAdapter:
    """Scan nginx configuration files for deployment and auth nodes.

    Emits:
    - **DEPLOYMENT** node for each ``proxy_pass`` directive found.
    - **AUTH** node when TLS termination is detected (``listen … ssl``
      or ``ssl_certificate``).
    """

    name = "nginx"
    priority = 20

    def scan(self, content: str, rel_path: str) -> list[ComponentDetection]:
        detections: list[ComponentDetection] = []

        # Collect server_names for context
        server_names: list[str] = []
        for m in _SERVER_NAME_RE.finditer(content):
            names = m.group("names").split()
            server_names.extend(n for n in names if n not in ("_", "localhost"))

        detections.extend(self._detect_proxy_pass(content, rel_path, server_names))
        detections.extend(self._detect_tls(content, rel_path, server_names))

        _log.info("nginx adapter: %d detection(s) in %s", len(detections), rel_path)
        return detections

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _detect_proxy_pass(
        self,
        content: str,
        rel_path: str,
        server_names: list[str],
    ) -> list[ComponentDetection]:
        results: list[ComponentDetection] = []
        seen: set[str] = set()

        for match in _PROXY_PASS_RE.finditer(content):
            url = match.group("url").rstrip("/")
            canonical = f"deployment:nginx_proxy:{url.lower()}"
            if canonical in seen:
                continue
            seen.add(canonical)

            line = content[: match.start()].count("\n") + 1
            _log.debug("%s:%d — nginx proxy_pass → %s", rel_path, line, url)

            results.append(
                ComponentDetection(
                    component_type=ComponentType.DEPLOYMENT,
                    canonical_name=canonical,
                    display_name=f"nginx proxy → {url}",
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.88,
                    metadata={
                        "source": "nginx_proxy_pass",
                        "upstream_url": url,
                        "server_names": server_names or None,
                    },
                    file_path=rel_path,
                    line=line,
                    snippet=match.group(0).strip()[:120],
                    evidence_kind="nginx",
                )
            )
        return results

    def _detect_tls(
        self,
        content: str,
        rel_path: str,
        server_names: list[str],
    ) -> list[ComponentDetection]:
        """Emit AUTH node when TLS/SSL is detected."""
        ssl_listen = False
        ssl_listen_line = 1
        cert_path: str | None = None
        cert_line = 1

        for match in _LISTEN_RE.finditer(content):
            flags = (match.group("flags") or "").lower()
            if "ssl" in flags:
                ssl_listen = True
                ssl_listen_line = content[: match.start()].count("\n") + 1
                _log.debug(
                    "%s:%d — SSL listen detected (port=%s)",
                    rel_path,
                    ssl_listen_line,
                    match.group("port"),
                )
                break

        for match in _SSL_CERT_RE.finditer(content):
            cert_path = match.group("path")
            cert_line = content[: match.start()].count("\n") + 1
            _log.debug("%s:%d — ssl_certificate %s", rel_path, cert_line, cert_path)
            ssl_listen = True  # Certificate alone is sufficient evidence
            break

        if not ssl_listen:
            return []

        line = cert_line if cert_path else ssl_listen_line
        canonical = f"auth:tls:{';'.join(server_names[:2]) if server_names else 'nginx'}"
        return [
            ComponentDetection(
                component_type=ComponentType.AUTH,
                canonical_name=canonical,
                display_name="TLS/SSL (nginx)",
                adapter_name=self.name,
                priority=self.priority,
                confidence=0.85,
                metadata={
                    "source": "nginx_tls",
                    "auth_kind": "tls",
                    "cert_path": cert_path,
                    "server_names": server_names or None,
                },
                file_path=rel_path,
                line=line,
                snippet=f"ssl_certificate {cert_path}" if cert_path else "listen … ssl",
                evidence_kind="nginx",
            )
        ]
