"""Unit tests for the FlaskAdapter.

Each test class targets one detection surface:

  TestEmptyPathDedup   — empty-path (@bp.route("", ...)) routes don't collide
  TestEndpointMetadata — endpoint metadata is set correctly, including for ""
"""

from __future__ import annotations

from typing import Any

from nuguard.sbom.adapters.python.flask_adapter import FlaskAdapter
from nuguard.sbom.ast_parser import parse
from nuguard.sbom.types import ComponentType

_ADAPTER = FlaskAdapter()


def _extract(code: str, file_path: str = "test_app.py") -> list[Any]:
    pr = parse(code)
    return _ADAPTER.extract(code, file_path, pr)


def _endpoints(detections: list[Any]) -> list[Any]:
    return [d for d in detections if d.component_type == ComponentType.API_ENDPOINT]


class TestEmptyPathDedup:
    def test_two_files_with_empty_path_route_produce_distinct_canonical_names(self) -> None:
        code_a = (
            "from flask import Blueprint\n"
            "bp = Blueprint('chat', __name__, url_prefix='/api/chat')\n\n"
            "@bp.route('', methods=['POST'])\n"
            "def chat():\n"
            "    return {}\n"
        )
        code_b = (
            "from flask import Blueprint\n"
            "bp = Blueprint('templates', __name__, url_prefix='/api/templates')\n\n"
            "@bp.route('', methods=['POST'])\n"
            "def create_template():\n"
            "    return {}\n"
        )

        eps_a = _endpoints(_extract(code_a, "server/api/chat.py"))
        eps_b = _endpoints(_extract(code_b, "server/api/templates.py"))

        assert len(eps_a) == 1
        assert len(eps_b) == 1
        assert eps_a[0].canonical_name != eps_b[0].canonical_name
        assert eps_a[0].canonical_name == "endpoint:POST::server/api/chat.py"
        assert eps_b[0].canonical_name == "endpoint:POST::server/api/templates.py"

    def test_non_empty_paths_still_dedupe_across_files(self) -> None:
        """Regression guard: the fix must not break the intentional cross-file
        merge for real (non-empty) shared paths."""
        code_a = (
            "from flask import Flask\n"
            "app = Flask(__name__)\n\n"
            "@app.route('/health')\n"
            "def health():\n"
            "    return {}\n"
        )
        code_b = (
            "from flask import Flask\n"
            "app = Flask(__name__)\n\n"
            "@app.route('/health')\n"
            "def health_check():\n"
            "    return {}\n"
        )
        eps_a = _endpoints(_extract(code_a, "service_one/main.py"))
        eps_b = _endpoints(_extract(code_b, "service_two/main.py"))
        assert eps_a[0].canonical_name == eps_b[0].canonical_name == "endpoint:GET:/health"


class TestEndpointMetadata:
    def test_empty_path_still_sets_endpoint_metadata(self) -> None:
        code = (
            "from flask import Blueprint\n"
            "bp = Blueprint('audit', __name__, url_prefix='/api/audit')\n\n"
            "@bp.route('', methods=['POST'])\n"
            "def log_audit():\n"
            "    return {}\n"
        )
        eps = _endpoints(_extract(code))
        assert len(eps) == 1
        assert eps[0].metadata["endpoint"] == ""

    def test_normal_path_sets_endpoint_metadata(self) -> None:
        code = (
            "from flask import Flask\n"
            "app = Flask(__name__)\n\n"
            "@app.route('/status')\n"
            "def status():\n"
            "    return {}\n"
        )
        eps = _endpoints(_extract(code))
        assert eps[0].metadata["endpoint"] == "/status"


class TestSecurityMisconfigDetection:
    def test_wildcard_cors_flagged(self) -> None:
        code = (
            "from flask import Flask\n"
            "from flask_cors import CORS\n"
            "app = Flask(__name__)\n"
            "CORS(app, supports_credentials=True)\n\n"
            "@app.route('/status')\n"
            "def status():\n"
            "    return {}\n"
        )
        eps = _endpoints(_extract(code))
        cors = eps[0].metadata["cors_policy"]
        assert cors["origin"] == "*"
        assert cors["wildcard_with_credentials"] is True

    def test_debug_run_kwarg_flagged(self) -> None:
        code = (
            "from flask import Flask\n"
            "app = Flask(__name__)\n\n"
            "@app.route('/status')\n"
            "def status():\n"
            "    return {}\n\n"
            "app.run(debug=True)\n"
        )
        eps = _endpoints(_extract(code))
        assert eps[0].metadata["debug_error_leak"] is True

    def test_missing_security_headers_flagged_by_default(self) -> None:
        code = (
            "from flask import Flask\n"
            "app = Flask(__name__)\n\n"
            "@app.route('/status')\n"
            "def status():\n"
            "    return {}\n"
        )
        eps = _endpoints(_extract(code))
        assert eps[0].metadata["security_headers_detail"]["missing"] == [
            "csp", "x_frame_options", "hsts",
        ]

    def test_talisman_clears_missing_headers(self) -> None:
        code = (
            "from flask import Flask\n"
            "from flask_talisman import Talisman\n"
            "app = Flask(__name__)\n"
            "Talisman(app)\n\n"
            "@app.route('/status')\n"
            "def status():\n"
            "    return {}\n"
        )
        eps = _endpoints(_extract(code))
        assert "security_headers_detail" not in eps[0].metadata
