"""A private git repository served over smart HTTP with basic auth, for clone tests.

Runs ``git http-backend`` (CGI) behind a tiny threaded HTTP server on
127.0.0.1. Every request must carry ``Authorization: Basic`` whose password is
the configured token, mirroring GitHub's ``x-access-token:<token>`` scheme.
"""

from __future__ import annotations

import base64
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def make_bare_repo(workdir: Path, name: str, files: dict[str, str]) -> tuple[Path, str]:
    """Create ``<workdir>/srv/<name>.git`` with one commit of *files*; return (root, sha)."""
    src = workdir / f"{name}-src"
    src.mkdir(parents=True)
    for rel, content in files.items():
        path = src / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
    }

    def git(*args: str, cwd: Path) -> str:
        return subprocess.run(
            ["git", *args], cwd=cwd, env=env, check=True, capture_output=True
        ).stdout.decode().strip()

    git("init", "-q", "-b", "main", cwd=src)
    git("add", "-A", cwd=src)
    git("commit", "-q", "-m", "init", cwd=src)
    sha = git("rev-parse", "HEAD", cwd=src)
    root = workdir / "srv"
    root.mkdir(exist_ok=True)
    git("clone", "-q", "--bare", str(src), str(root / f"{name}.git"), cwd=workdir)
    # Allow fetching a commit by SHA, as GitHub does.
    git("config", "uploadpack.allowReachableSHA1InWant", "true", cwd=root / f"{name}.git")
    return root, sha


class GitHttpServer:
    """Context manager serving every bare repo under *root* at ``http://127.0.0.1:<port>/``."""

    def __init__(self, root: Path, token: str) -> None:
        self.root = root
        self.token = token
        self.auth_headers: list[str] = []
        self._server: ThreadingHTTPServer | None = None

    def url(self, name: str) -> str:
        assert self._server is not None
        return f"http://127.0.0.1:{self._server.server_address[1]}/{name}.git"

    def __enter__(self) -> "GitHttpServer":
        server = self
        expected = "Basic " + base64.b64encode(f"x-access-token:{self.token}".encode()).decode()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: object) -> None:  # keep test output quiet
                return

            def do_GET(self) -> None:
                self._cgi()

            def do_POST(self) -> None:
                self._cgi()

            def _cgi(self) -> None:
                auth = self.headers.get("Authorization") or ""
                server.auth_headers.append(auth)
                if auth != expected:
                    self.send_response(401)
                    self.send_header("WWW-Authenticate", 'Basic realm="git"')
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                path, _, query = self.path.partition("?")
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                env = {
                    "PATH": os.environ.get("PATH", ""),
                    "GIT_PROJECT_ROOT": str(server.root),
                    "GIT_HTTP_EXPORT_ALL": "1",
                    "PATH_INFO": path,
                    "QUERY_STRING": query,
                    "REQUEST_METHOD": self.command,
                    "CONTENT_TYPE": self.headers.get("Content-Type", ""),
                    "CONTENT_LENGTH": str(len(body)),
                    "HTTP_CONTENT_ENCODING": self.headers.get("Content-Encoding", ""),
                    "GIT_PROTOCOL": self.headers.get("Git-Protocol", ""),
                    "REMOTE_USER": "x-access-token",
                    "GIT_CONFIG_NOSYSTEM": "1",
                }
                out = subprocess.run(
                    ["git", "http-backend"], input=body, env=env, capture_output=True
                ).stdout
                head, _, payload = out.partition(b"\r\n\r\n")
                status = 200
                headers: list[tuple[str, str]] = []
                for line in head.split(b"\r\n"):
                    key, _, value = line.decode("latin-1").partition(":")
                    if key.lower() == "status":
                        status = int(value.strip().split()[0])
                    elif key:
                        headers.append((key, value.strip()))
                self.send_response(status)
                for key, value in headers:
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *_exc: object) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
