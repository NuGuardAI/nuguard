"""Tests for C# project and NuGet manifest dependency scanning."""

from __future__ import annotations

from pathlib import Path

from nuguard.sbom.deps import DependencyScanner, _to_nuget_purl


class TestNugetPurl:
    def test_concrete_version_is_embedded(self) -> None:
        assert (
            _to_nuget_purl("Microsoft.SemanticKernel", "==1.45.0")
            == "pkg:nuget/Microsoft.SemanticKernel@1.45.0"
        )

    def test_non_concrete_version_is_not_embedded(self) -> None:
        assert (
            _to_nuget_purl("Azure.AI.OpenAI", "$(AzureOpenAIVersion)")
            == "pkg:nuget/Azure.AI.OpenAI"
        )


class TestCsprojScanning:
    def test_inline_and_nested_versions(self, tmp_path: Path) -> None:
        (tmp_path / "App.csproj").write_text(
            """<Project Sdk="Microsoft.NET.Sdk">
  <ItemGroup>
    <PackageReference Include="Microsoft.SemanticKernel"
                      Version="1.45.0" />
    <PackageReference Include="Azure.AI.OpenAI">
      <Version>2.1.0</Version>
    </PackageReference>
  </ItemGroup>
</Project>
""",
            encoding="utf-8",
        )

        deps = {dep.name: dep for dep in DependencyScanner().scan(tmp_path)}

        semantic_kernel = deps["Microsoft.SemanticKernel"]
        assert semantic_kernel.version_spec == "==1.45.0"
        assert semantic_kernel.version == "1.45.0"
        assert semantic_kernel.purl == "pkg:nuget/Microsoft.SemanticKernel@1.45.0"
        assert semantic_kernel.source_file == "App.csproj"

        azure_openai = deps["Azure.AI.OpenAI"]
        assert azure_openai.version_spec == "==2.1.0"
        assert azure_openai.purl == "pkg:nuget/Azure.AI.OpenAI@2.1.0"

    def test_xml_namespace_is_supported(self, tmp_path: Path) -> None:
        (tmp_path / "Legacy.csproj").write_text(
            """<Project xmlns="http://schemas.microsoft.com/developer/msbuild/2003">
  <ItemGroup>
    <PackageReference Include="Microsoft.ML" Version="4.0.1" />
  </ItemGroup>
</Project>
""",
            encoding="utf-8",
        )

        deps = DependencyScanner().scan(tmp_path)

        assert [dep.purl for dep in deps] == ["pkg:nuget/Microsoft.ML@4.0.1"]

    def test_msbuild_property_version_remains_unresolved(self, tmp_path: Path) -> None:
        (tmp_path / "App.csproj").write_text(
            """<Project>
  <ItemGroup>
    <PackageReference Include="Azure.AI.OpenAI"
                      Version="$(AzureOpenAIVersion)" />
  </ItemGroup>
</Project>
""",
            encoding="utf-8",
        )

        dep = DependencyScanner().scan(tmp_path)[0]

        assert dep.version_spec == "$(AzureOpenAIVersion)"
        assert dep.version is None
        assert dep.purl == "pkg:nuget/Azure.AI.OpenAI"

    def test_central_package_management_and_inline_override(self, tmp_path: Path) -> None:
        (tmp_path / "Directory.Packages.props").write_text(
            """<Project xmlns="http://schemas.microsoft.com/developer/msbuild/2003">
  <ItemGroup>
    <PackageVersion Include="Microsoft.SemanticKernel"
                    Version="1.45.0" />
    <PackageVersion Include="Azure.AI.OpenAI">
      <Version>2.1.0</Version>
    </PackageVersion>
    <PackageVersion Include="Microsoft.ML" Version="4.0.1" />
  </ItemGroup>
</Project>
""",
            encoding="utf-8",
        )

        app_dir = tmp_path / "src" / "App"
        app_dir.mkdir(parents=True)

        (app_dir / "App.csproj").write_text(
            """<Project>
  <ItemGroup>
    <PackageReference Include="Microsoft.SemanticKernel" />
    <PackageReference Include="Azure.AI.OpenAI" Version="2.2.0" />
  </ItemGroup>
</Project>
""",
            encoding="utf-8",
        )

        deps = {dep.name: dep for dep in DependencyScanner().scan(tmp_path)}

        semantic_kernel = deps["Microsoft.SemanticKernel"]
        assert semantic_kernel.purl == "pkg:nuget/Microsoft.SemanticKernel@1.45.0"
        assert semantic_kernel.source_file == "src/App/App.csproj"

        azure_openai = deps["Azure.AI.OpenAI"]
        assert azure_openai.purl == "pkg:nuget/Azure.AI.OpenAI@2.2.0"
        assert azure_openai.source_file == "src/App/App.csproj"

        central_only = deps["Microsoft.ML"]
        assert central_only.purl == "pkg:nuget/Microsoft.ML@4.0.1"
        assert central_only.source_file == "Directory.Packages.props"

    def test_nearest_directory_packages_props_wins(self, tmp_path: Path) -> None:
        (tmp_path / "Directory.Packages.props").write_text(
            """<Project><ItemGroup>
  <PackageVersion Include="Azure.AI.OpenAI" Version="2.1.0" />
</ItemGroup></Project>
""",
            encoding="utf-8",
        )

        nested = tmp_path / "src"
        nested.mkdir()

        (nested / "Directory.Packages.props").write_text(
            """<Project><ItemGroup>
  <PackageVersion Include="Azure.AI.OpenAI" Version="2.2.0" />
</ItemGroup></Project>
""",
            encoding="utf-8",
        )

        (nested / "App.csproj").write_text(
            """<Project><ItemGroup>
  <PackageReference Include="Azure.AI.OpenAI" />
</ItemGroup></Project>
""",
            encoding="utf-8",
        )

        dep = next(
            dep for dep in DependencyScanner().scan(tmp_path) if dep.source_file == "src/App.csproj"
        )

        assert dep.purl == "pkg:nuget/Azure.AI.OpenAI@2.2.0"


class TestPackagesConfigScanning:
    def test_packages_config_and_development_dependency(self, tmp_path: Path) -> None:
        (tmp_path / "packages.config").write_text(
            """<packages xmlns="urn:nuget-packages">
  <package id="Newtonsoft.Json" version="13.0.3" />
  <package id="Microsoft.CodeAnalysis"
           version="4.11.0"
           developmentDependency="true" />
</packages>
""",
            encoding="utf-8",
        )

        deps = {dep.name: dep for dep in DependencyScanner().scan(tmp_path)}

        assert deps["Newtonsoft.Json"].purl == "pkg:nuget/Newtonsoft.Json@13.0.3"
        assert deps["Newtonsoft.Json"].group == "runtime"
        assert deps["Microsoft.CodeAnalysis"].group == "dev"
        assert deps["Microsoft.CodeAnalysis"].source_file == "packages.config"


class TestCsharpManifestRobustness:
    def test_malformed_or_incomplete_xml_is_ignored(self, tmp_path: Path) -> None:
        (tmp_path / "Broken.csproj").write_text(
            "<Project><ItemGroup><PackageReference",
            encoding="utf-8",
        )

        (tmp_path / "packages.config").write_text(
            "<packages><package id='Newtonsoft.Json'",
            encoding="utf-8",
        )

        (tmp_path / "Directory.Packages.props").write_text(
            "<Project><ItemGroup>",
            encoding="utf-8",
        )

        assert DependencyScanner().scan(tmp_path) == []

    def test_duplicates_are_deduplicated_and_unrelated_xml_is_ignored(self, tmp_path: Path) -> None:
        (tmp_path / "App.csproj").write_text(
            """<Project><ItemGroup>
  <PackageReference Include="Microsoft.SemanticKernel"
                    Version="1.45.0" />
  <PackageReference Include="microsoft.semantickernel"
                    Version="1.45.0" />
</ItemGroup></Project>
""",
            encoding="utf-8",
        )

        (tmp_path / "unrelated.xml").write_text(
            """<Project><ItemGroup>
  <PackageReference Include="Should.Not.Be.Scanned"
                    Version="9.9.9" />
</ItemGroup></Project>
""",
            encoding="utf-8",
        )

        deps = DependencyScanner().scan(tmp_path)

        assert len(deps) == 1
        assert deps[0].name == "Microsoft.SemanticKernel"
        assert deps[0].purl == "pkg:nuget/Microsoft.SemanticKernel@1.45.0"
