"""Compatibility and bounded-runtime regressions for Dockerfile RUN extraction."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from nuguard.sbom.adapters.dockerfile import DockerfileAdapter


@pytest.mark.parametrize(
    ("command", "manager", "name", "version"),
    [
        ("apt-get -qq --yes install curl=8.0", "apt", "curl", "8.0"),
        ("apt install curl", "apt", "curl", None),
        ("apk --no-cache add curl=8.0", "apk", "curl", "8.0"),
        ("yum -y install curl", "yum", "curl", None),
        ("dnf --assumeyes install curl", "yum", "curl", None),
        ("microdnf -y install curl", "yum", "curl", None),
        ("pip -q --disable-pip-version-check install fastapi==0.110", "pip", "fastapi", "0.110"),
        ("pip3.12\t-q\t\t--isolated\tinstall fastapi", "pip", "fastapi", None),
        ("pip install fastapi", "pip", "fastapi", None),
        ("npm install express@4.0", "npm", "express", "4.0"),
        ("yarn add express@4.0", "npm", "express", "4.0"),
    ],
)
def test_install_commands_preserve_package_metadata(
    command: str, manager: str, name: str, version: str | None,
) -> None:
    detections = DockerfileAdapter().scan(f"FROM python:3.12\nRUN {command}\n", "Dockerfile")
    app = next(d for d in detections if d.metadata.get("image_role") == "app")
    assert app.metadata["image_packages"] == [
        {"name": name, "version": version, "manager": manager, "source": "dockerfile_run"}
    ]


def test_repeated_flags_without_install_verb_finish_within_deadline() -> None:
    # Run in a child process so a regression cannot hang the test suite. This is
    # CodeQL alert #94's failing input, scaled beyond realistic command lengths.
    script = """
import json
from nuguard.sbom.adapters.dockerfile import DockerfileAdapter

packages = []
for manager in ('apt', 'apt-get', 'apk', 'yum', 'dnf', 'microdnf', 'pip', 'pip3.12'):
    command = manager + ' ' + '-! -' * 10000 + 'not-install'
    detections = DockerfileAdapter().scan('FROM python:3.12\\nRUN ' + command, 'Dockerfile')
    app = next(d for d in detections if d.metadata.get('image_role') == 'app')
    packages.extend(app.metadata.get('image_packages', []))
print(json.dumps(packages))
"""
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True, timeout=10,
    )
    assert json.loads(result.stdout) == []
