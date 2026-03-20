"""Test the Terraform .tf file scanner."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from nuguard.models.sbom import NodeType
from nuguard.sbom.extractor.iac_scanners.terraform import TerraformScanner

TF_IAM_ROLE = """
resource "aws_iam_role" "lambda_role" {
  name = "lambda_execution_role"
  assume_role_policy = jsonencode({
    Statement = [{
      Action = "sts:AssumeRole"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}
"""

TF_IAM_POLICY_ATTACHMENT_ADMIN = """
resource "aws_iam_role_policy_attachment" "admin_attach" {
  role       = aws_iam_role.lambda_role.name
  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}
"""

TF_GOOGLE_SA = """
resource "google_service_account" "app_sa" {
  account_id   = "app-service-account"
  display_name = "App Service Account"
}
"""

TF_AZURE_IDENTITY = """
resource "azurerm_user_assigned_identity" "app_identity" {
  name                = "app-managed-identity"
  resource_group_name = azurerm_resource_group.main.name
  location            = "eastus"
}
"""

TF_ECS_TASK = """
resource "aws_ecs_task_definition" "app" {
  family = "app-task"
  container_definitions = jsonencode([{
    name  = "app"
    "image" : "nginx:1.24"
    memory = 512
    cpu    = 256
  }])
}
"""


@pytest.fixture
def scanner() -> TerraformScanner:
    return TerraformScanner()


def _write_tf(content: str) -> Path:
    f = tempfile.NamedTemporaryFile(suffix=".tf", delete=False, mode="w")
    f.write(content)
    f.close()
    return Path(f.name)


def test_aws_iam_role_detected(scanner: TerraformScanner) -> None:
    """aws_iam_role resource creates an IAM node."""
    path = _write_tf(TF_IAM_ROLE)
    nodes, edges, findings = scanner.scan(path)
    iam_nodes = [n for n in nodes if n.component_type == NodeType.IAM]
    assert len(iam_nodes) >= 1
    assert any(n.name == "lambda_role" for n in iam_nodes)


def test_iam_role_type(scanner: TerraformScanner) -> None:
    """aws_iam_role node has iam_type='aws_iam_role'."""
    path = _write_tf(TF_IAM_ROLE)
    nodes, _, _ = scanner.scan(path)
    role_node = next(n for n in nodes if n.name == "lambda_role")
    assert role_node.metadata.iam_type == "aws_iam_role"


def test_admin_policy_attachment_finding(scanner: TerraformScanner) -> None:
    """AdministratorAccess policy → 'overly_permissive_iam' finding."""
    path = _write_tf(TF_IAM_POLICY_ATTACHMENT_ADMIN)
    nodes, edges, findings = scanner.scan(path)
    assert "overly_permissive_iam" in findings


def test_google_service_account_detected(scanner: TerraformScanner) -> None:
    """google_service_account resource creates an IAM node."""
    path = _write_tf(TF_GOOGLE_SA)
    nodes, _, _ = scanner.scan(path)
    iam_nodes = [n for n in nodes if n.component_type == NodeType.IAM]
    assert any(n.metadata.iam_type == "gcp_service_account" for n in iam_nodes)


def test_azure_managed_identity_detected(scanner: TerraformScanner) -> None:
    """azurerm_user_assigned_identity creates an IAM node."""
    path = _write_tf(TF_AZURE_IDENTITY)
    nodes, _, _ = scanner.scan(path)
    iam_nodes = [n for n in nodes if n.component_type == NodeType.IAM]
    assert any(n.metadata.iam_type == "azure_managed_identity" for n in iam_nodes)


def test_ecs_task_definition_creates_deployment(scanner: TerraformScanner) -> None:
    """aws_ecs_task_definition creates a DEPLOYMENT node."""
    path = _write_tf(TF_ECS_TASK)
    nodes, _, _ = scanner.scan(path)
    dep_nodes = [n for n in nodes if n.component_type == NodeType.DEPLOYMENT]
    assert len(dep_nodes) >= 1


def test_ecs_container_image_extracted(scanner: TerraformScanner) -> None:
    """Container image in ECS task definition creates a CONTAINER_IMAGE node."""
    path = _write_tf(TF_ECS_TASK)
    nodes, _, _ = scanner.scan(path)
    img_nodes = [n for n in nodes if n.component_type == NodeType.CONTAINER_IMAGE]
    assert len(img_nodes) >= 1
    assert any(n.name == "nginx" for n in img_nodes)


def test_nonexistent_file_returns_empty(scanner: TerraformScanner) -> None:
    nodes, edges, findings = scanner.scan(Path("/nonexistent/file.tf"))
    assert nodes == []
    assert edges == []
    assert findings == []
