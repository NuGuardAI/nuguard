"""``python -m nuguard.service`` / ``nuguard-service``: run the remote scan API."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def run(config: Path | None = None, host: str | None = None, port: int | None = None) -> int:
    """Load settings, refuse unsafe configurations, and serve with uvicorn."""
    os.environ.setdefault("NUGUARD_LOG_FORMAT", "json")
    try:
        import uvicorn  # noqa: PLC0415
    except ImportError:
        print('The service needs extra dependencies: pip install "nuguard[service]"',
              file=sys.stderr)
        return 2

    from nuguard.service.app import create_app  # noqa: PLC0415
    from nuguard.service.settings import ServiceConfigError, load_service_settings  # noqa: PLC0415

    try:
        settings = load_service_settings(config)
        if host:
            settings.bind = host
        if port:
            settings.port = port
        settings.validate_for_startup()
    except (ServiceConfigError, ValueError) as exc:
        print(f"Refusing to start: {exc}", file=sys.stderr)
        return 2

    app = create_app(settings)
    tls: dict[str, str] = {}
    if settings.tls.cert_file and settings.tls.key_file:
        tls = {"ssl_certfile": str(settings.tls.cert_file),
               "ssl_keyfile": str(settings.tls.key_file)}
    uvicorn.run(
        app,
        host=settings.bind,
        port=settings.port,
        proxy_headers=settings.trust_proxy,
        forwarded_allow_ips="*" if settings.trust_proxy else None,
        server_header=False,
        access_log=False,
        **tls,  # type: ignore[arg-type]
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nuguard-service", description=__doc__)
    parser.add_argument("--config", type=Path, default=None, help="nuguard.yaml with a service: section")
    parser.add_argument("--host", default=None, help="Override service.bind")
    parser.add_argument("--port", type=int, default=None, help="Override service.port")
    args = parser.parse_args(argv)
    return run(args.config, args.host, args.port)


if __name__ == "__main__":
    sys.exit(main())
