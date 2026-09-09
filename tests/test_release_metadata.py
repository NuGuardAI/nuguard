"""Release metadata must project one canonical NuGuard version."""

from __future__ import annotations

import importlib.util
import json
import re
import tomllib
import types
import zipfile
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOWS = _ROOT / ".github" / "workflows"


def _json_version(path: str, *keys: str) -> str:
    value: object = json.loads((_ROOT / path).read_text(encoding="utf-8"))
    for key in keys:
        if isinstance(value, dict):
            value = value[key]
        else:
            assert isinstance(value, list), f"{path}: expected container before {key!r}"
            value = value[int(key)]
    assert isinstance(value, str), f"{path}: expected string at {'.'.join(keys)}"
    return value


def _marketplace_versions(path: str) -> dict[str, str]:
    document = json.loads((_ROOT / path).read_text(encoding="utf-8"))
    assert isinstance(document, dict), f"{path}: expected object"
    metadata = document.get("metadata")
    plugins = document.get("plugins")
    assert isinstance(metadata, dict), f"{path}: expected metadata object"
    assert isinstance(plugins, list), f"{path}: expected plugins array"
    assert plugins, f"{path}: expected at least one plugin"
    versions = {f"{path} metadata": str(metadata.get("version", ""))}
    for index, plugin in enumerate(plugins):
        assert isinstance(plugin, dict), f"{path}: plugin {index} must be an object"
        versions[f"{path} plugin {index}"] = str(plugin.get("version", ""))
    return versions


def _load_workflow(name: str) -> dict[str, object]:
    workflow = yaml.safe_load((_WORKFLOWS / name).read_text(encoding="utf-8"))
    assert isinstance(workflow, dict), f"{name}: expected mapping"
    return workflow


def _workflow_events(workflow: dict[str, object]) -> dict[str, object]:
    # PyYAML may parse the key "on" as a boolean under YAML 1.1 semantics.
    events = workflow.get("on")
    if events is None:
        events = workflow.get(True)
    assert isinstance(events, dict), "workflow on: expected mapping"
    return events


def _job(workflow: dict[str, object], name: str) -> dict[str, object]:
    jobs = workflow.get("jobs")
    assert isinstance(jobs, dict), "workflow jobs: expected mapping"
    job = jobs.get(name)
    assert isinstance(job, dict), f"job {name!r}: expected mapping"
    return job


def _job_needs(job: dict[str, object]) -> set[str]:
    needs = job.get("needs")
    if isinstance(needs, str):
        return {needs}
    assert isinstance(needs, list), "job needs: expected string or list"
    assert all(isinstance(item, str) for item in needs)
    return set(needs)


def _step_run(job: dict[str, object], step_name: str) -> str:
    step = _step(job, step_name)
    run_value = step.get("run")
    assert isinstance(run_value, str), f"step {step_name!r}: expected run script"
    return run_value


def _step(job: dict[str, object], step_name: str) -> dict[str, object]:
    steps = job.get("steps")
    assert isinstance(steps, list), "job steps: expected list"
    for step in steps:
        if isinstance(step, dict) and step.get("name") == step_name:
            return step
    raise AssertionError(f"step {step_name!r} not found")


def _checkout_refs(job: dict[str, object]) -> list[str]:
    refs: list[str] = []
    steps = job.get("steps")
    assert isinstance(steps, list), "job steps: expected list"
    for step in steps:
        if not isinstance(step, dict):
            continue
        if step.get("uses") != "actions/checkout@v4":
            continue
        with_block = step.get("with")
        assert isinstance(with_block, dict), "checkout step with: expected mapping"
        ref = with_block.get("ref")
        assert isinstance(ref, str), "checkout step ref: expected string"
        refs.append(ref)
    return refs


def _load_publish_smithery_module() -> types.ModuleType:
    script = _ROOT / "scripts" / "publish_smithery.py"
    spec = importlib.util.spec_from_file_location("publish_smithery_script", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_release_metadata_matches_pyproject_version() -> None:
    """Every release-facing manifest must match the canonical pyproject version."""
    pyproject = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    expected = pyproject["project"]["version"]

    init_text = (_ROOT / "nuguard" / "__init__.py").read_text(encoding="utf-8")
    init_match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', init_text, re.MULTILINE)
    assert init_match, "nuguard/__init__.py: __version__ not found"

    smithery = yaml.safe_load((_ROOT / "smithery.yaml").read_text(encoding="utf-8"))
    assert isinstance(smithery, dict), "smithery.yaml: expected mapping"

    versions = {
        "nuguard/__init__.py": init_match.group(1),
        "npm/package.json": _json_version("npm/package.json", "version"),
        ".claude-plugin/plugin.json": _json_version(
            ".claude-plugin/plugin.json", "version"
        ),
        "plugin/.claude-plugin/plugin.json": _json_version(
            "plugin/.claude-plugin/plugin.json", "version"
        ),
        "gemini-extension.json": _json_version("gemini-extension.json", "version"),
        "openclaw.plugin.json": _json_version("openclaw.plugin.json", "version"),
        "smithery.yaml": str(smithery.get("version", "")),
    }
    for marketplace in [
        ".claude-plugin/marketplace.json",
        "plugin/.claude-plugin/marketplace.json",
        "marketplace.json",
    ]:
        versions.update(_marketplace_versions(marketplace))

    mismatches = {
        source: version for source, version in versions.items() if version != expected
    }
    assert not mismatches, f"Expected every release version to equal {expected}: {mismatches}"

    lock = tomllib.loads((_ROOT / "uv.lock").read_text(encoding="utf-8"))
    project_packages = [
        package for package in lock["package"] if package.get("name") == "nuguard"
    ]
    assert len(project_packages) == 1
    assert project_packages[0]["version"] == expected


def test_publish_pypi_workflow_requires_tag_push_and_tag_checkout() -> None:
    """Production publishing must run only for version tags and check out that tag."""
    workflow = _load_workflow("publish-pypi.yml")
    events = _workflow_events(workflow)

    assert "workflow_dispatch" not in events
    push = events.get("push")
    assert isinstance(push, dict)
    assert push.get("tags") == ["v*"]

    tag_ref = "${{ github.ref_name }}"
    for name in ["validate", "build", "publish-pypi", "publish-npm", "publish-smithery"]:
        refs = _checkout_refs(_job(workflow, name))
        assert refs, f"{name}: expected at least one checkout step"
        assert set(refs) == {tag_ref}


def test_publish_pypi_workflow_validates_metadata_before_build() -> None:
    """PyPI workflow validate job must gate build with tag/version and metadata checks."""
    workflow = _load_workflow("publish-pypi.yml")
    validate_job = _job(workflow, "validate")
    build_job = _job(workflow, "build")

    script = _step_run(validate_job, "Verify tag and release metadata")
    assert "test \"$RELEASE_TAG\" = \"v$VERSION\"" in script
    assert "uv lock --check" in script
    assert "npm run version:check" in script

    assert _job_needs(build_job) == {"validate", "draft-release"}


def test_publish_pypi_publication_job_dependencies_are_strict() -> None:
    """PyPI publication jobs must preserve dependency order for safe releases."""
    workflow = _load_workflow("publish-pypi.yml")
    assert _job_needs(_job(workflow, "publish-pypi")) == {"build"}
    assert _job_needs(_job(workflow, "publish-npm")) == {"publish-pypi"}
    assert _job_needs(_job(workflow, "publish-smithery")) == {"publish-pypi"}
    assert _job_needs(_job(workflow, "draft-release")) == {"validate"}
    assert _job_needs(_job(workflow, "finalize-release")) == {
        "publish-npm",
        "publish-smithery",
    }

    draft_script = _step_run(_job(workflow, "draft-release"), "Create or reuse draft release")
    assert "gh release create" in draft_script
    assert "--draft" in draft_script
    finalize_script = _step_run(_job(workflow, "finalize-release"), "Publish draft release")
    assert 'gh release edit "$RELEASE_TAG" --draft=false' in finalize_script


def test_publish_workflow_verifies_registry_artifact_provenance() -> None:
    """Existing versions may be skipped only after exact artifact verification."""
    workflow = _load_workflow("publish-pypi.yml")
    pypi_job = _job(workflow, "publish-pypi")
    npm_job = _job(workflow, "publish-npm")

    pypi_script = _step_run(pypi_job, "Verify existing PyPI artifacts")
    assert "verify_registry_artifacts.py pypi" in pypi_script
    npm_script = _step_run(npm_job, "Verify existing npm artifact")
    assert "verify_registry_artifacts.py npm" in npm_script
    assert _step(pypi_job, "Publish to PyPI").get("if") == (
        "steps.pypi-registry.outputs.exists != 'true'"
    )
    assert _step(npm_job, "Publish to npm").get("if") == (
        "steps.npm-registry.outputs.exists != 'true'"
    )

    workflow_text = (_WORKFLOWS / "publish-pypi.yml").read_text(encoding="utf-8")
    assert "skip-existing" not in workflow_text
    assert "npm view" not in workflow_text


def test_publish_helper_reports_missing_executable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Publisher prerequisites should fail preflight without an uncaught traceback."""
    module = _load_publish_smithery_module()

    def raise_missing(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError("node not found")

    monkeypatch.setattr(module.subprocess, "run", raise_missing)
    result = module._run(["node", "scripts/validate-all-plugins.js"])

    assert result.returncode == 127
    assert "node not found" in result.stderr


def test_publish_testpypi_workflow_requires_explicit_ref_and_validation_gate() -> None:
    """TestPyPI workflow must require manual ref input and validate before build."""
    workflow = _load_workflow("publish-testpypi.yml")
    events = _workflow_events(workflow)

    dispatch = events.get("workflow_dispatch")
    assert isinstance(dispatch, dict)
    inputs = dispatch.get("inputs")
    assert isinstance(inputs, dict)
    ref_input = inputs.get("ref")
    assert isinstance(ref_input, dict)
    assert ref_input.get("required") is True
    assert ref_input.get("type") == "string"

    expected_ref = "${{ inputs.ref }}"
    for name in ["validate", "build"]:
        refs = _checkout_refs(_job(workflow, name))
        assert refs, f"{name}: expected checkout step"
        assert set(refs) == {expected_ref}

    validate_script = _step_run(_job(workflow, "validate"), "Verify prerelease metadata")
    assert "uv lock --check" in validate_script
    assert "npm run version:check" in validate_script
    assert "version.is_prerelease" in validate_script
    assert "TestPyPI requires a prerelease version" in validate_script

    assert _job_needs(_job(workflow, "build")) == {"validate"}
    assert _job_needs(_job(workflow, "publish")) == {"build"}


def test_build_smithery_bundle_pins_exact_mcp_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Generated Smithery bundle must pin nuguard[mcp] to an exact package version."""
    module = _load_publish_smithery_module()

    smithery_yaml = tmp_path / "smithery.yaml"
    smithery_yaml.write_text(
        "\n".join(
            [
                "name: nuguard",
                "version: \"9.9.9\"",
                "startCommand:",
                "  configSchema:",
                "    type: object",
                "    properties:",
                "      litellm_api_key:",
                "        type: string",
                "        description: LiteLLM key",
                "        default: \"\"",
                "      nuguard_config_path:",
                "        type: string",
                "        default: \"\"",
                "tools:",
                "  - name: nuguard_scan",
                "    description: Scan target",
                "",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(module, "SMITHERY_YAML", smithery_yaml)
    monkeypatch.setattr(module, "SMITHERY_BUNDLE", tmp_path / "server.mcpb")

    bundle = module._build_smithery_bundle("1.2.3")
    assert bundle.exists()

    with zipfile.ZipFile(bundle, "r") as archive:
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))

    assert manifest["version"] == "1.2.3"
    assert manifest["server"]["mcp_config"]["command"] == "uvx"
    assert manifest["server"]["mcp_config"]["args"] == [
        "--from",
        "nuguard[mcp]==1.2.3",
        "nuguard-mcp",
    ]