"""Tests for the Checkov IaC scanner plugin's JSON parsing.

Covers ``_parse_checkov_output`` against fixtures shaped like real Checkov
output (top-level ``check_name``/``guideline``/``severity`` — no nested
``"check"`` object, which never exists in actual Checkov JSON).
"""

from __future__ import annotations

from nuguard.analysis.plugins.checkov_scanner import _parse_checkov_output


def _base_check_result(**overrides: object) -> dict:
    result = {
        "check_id": "CKV_AWS_150",
        "check_name": "Ensure that S3 buckets are encrypted with KMS by default",
        "check_result": {"result": "FAILED"},
        "resource": "aws_s3_bucket.example",
        "file_path": "/main.tf",
        "file_line_range": [10, 15],
        "guideline": "https://docs.example.com/CKV_AWS_150",
        "severity": "HIGH",
    }
    result.update(overrides)
    return result


def _checkov_output(*check_results: dict) -> dict:
    return {"results": {"failed_checks": list(check_results)}}


def test_resolves_real_check_name_not_rule_id():
    data = _checkov_output(_base_check_result())
    findings = _parse_checkov_output(data, "/main.tf")

    assert len(findings) == 1
    finding = findings[0]
    assert finding["rule_id"] == "CKV_AWS_150"
    assert "Ensure that S3 buckets are encrypted with KMS by default" in finding["title"]
    assert finding["title"] != finding["rule_id"]


def test_title_includes_category_and_affected_resource():
    data = _checkov_output(_base_check_result())
    finding = _parse_checkov_output(data, "/main.tf")[0]

    assert finding["title"] == (
        "[IaC misconfiguration] Ensure that S3 buckets are encrypted with "
        "KMS by default — aws_s3_bucket.example"
    )


def test_populates_remediation_and_severity_from_top_level_fields():
    data = _checkov_output(_base_check_result())
    finding = _parse_checkov_output(data, "/main.tf")[0]

    assert finding["remediation"] == "https://docs.example.com/CKV_AWS_150"
    assert finding["url"] == "https://docs.example.com/CKV_AWS_150"
    assert finding["severity"] == "HIGH"


def test_normal_resource_used_as_affected_component():
    data = _checkov_output(_base_check_result())
    finding = _parse_checkov_output(data, "/main.tf")[0]

    assert finding["affected"] == ["aws_s3_bucket.example"]
    assert "aws_s3_bucket.example" in finding["description"]


def test_evidence_populated_with_file_and_line_for_normal_resource_check():
    data = _checkov_output(_base_check_result())
    finding = _parse_checkov_output(data, "/main.tf")[0]

    assert finding["evidence"] == "/main.tf:10"


def test_secret_hash_resource_falls_back_to_file_location():
    secret_result = _base_check_result(
        check_id="CKV_SECRET_13",
        check_name="CKV_SECRET_13",
        resource="967649db3de73fc65f333fcbdf3fe58e334bcc7a",
        file_path="/app/config.py",
        file_line_range=[42, 42],
        guideline="",
    )
    data = _checkov_output(secret_result)
    finding = _parse_checkov_output(data, "/app/config.py")[0]

    assert finding["affected"] == ["/app/config.py:42"]
    assert "967649db3de73fc65f333fcbdf3fe58e334bcc7a" not in finding["affected"][0]
    assert "967649db3de73fc65f333fcbdf3fe58e334bcc7a" not in finding["description"]
    # Evidence would just repeat the affected-component value here — omitted.
    assert finding["evidence"] is None


def test_missing_line_range_falls_back_to_bare_file_path():
    secret_result = _base_check_result(
        check_id="CKV_SECRET_6",
        check_name="CKV_SECRET_6",
        resource="a" * 40,
        file_path="/app/settings.py",
        file_line_range=[],
    )
    data = _checkov_output(secret_result)
    finding = _parse_checkov_output(data, "/app/settings.py")[0]

    assert finding["affected"] == ["/app/settings.py"]


def test_missing_check_name_falls_back_to_check_id():
    check_result = _base_check_result()
    del check_result["check_name"]
    data = _checkov_output(check_result)
    finding = _parse_checkov_output(data, "/main.tf")[0]

    assert finding["title"] == "[IaC misconfiguration] CKV_AWS_150 — aws_s3_bucket.example"
