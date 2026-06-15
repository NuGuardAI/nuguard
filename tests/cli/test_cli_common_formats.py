from pathlib import Path

import pytest

from nuguard.cli.common import output_path_for_format, parse_output_formats


def test_parse_output_formats_repeated_and_comma_values() -> None:
    formats = parse_output_formats(
        ["json", "markdown,sarif", "json"],
        default_format="markdown",
        allowed_formats={"markdown", "json", "sarif"},
    )
    assert formats == ["json", "markdown", "sarif"]


def test_parse_output_formats_default_when_empty() -> None:
    formats = parse_output_formats(
        None,
        default_format="text",
        allowed_formats={"text", "json", "markdown"},
    )
    assert formats == ["text"]


def test_parse_output_formats_invalid_value() -> None:
    with pytest.raises(ValueError, match="Unknown format"):
        parse_output_formats(
            ["xml"],
            default_format="markdown",
            allowed_formats={"markdown", "json", "sarif"},
        )


def test_output_path_for_format_multi_uses_base_name() -> None:
    path = output_path_for_format(
        Path("reports/run.md"),
        fmt="json",
        all_formats=["json", "markdown"],
        extension_map={"json": ".json", "markdown": ".md"},
    )
    assert path.as_posix().endswith("reports/run.json")


def test_output_path_for_format_single_preserves_path() -> None:
    original = Path("reports/run.md")
    path = output_path_for_format(
        original,
        fmt="markdown",
        all_formats=["markdown"],
        extension_map={"markdown": ".md"},
    )
    assert path == original
