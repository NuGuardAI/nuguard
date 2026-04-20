from __future__ import annotations

from nuguard.sbom.extractor.config import AiSbomConfig
from nuguard.sbom.extractor.core import AiSbomExtractor


def test_iter_files_skips_versioned_virtualenv_directories(tmp_path) -> None:
    source_dir = tmp_path / "sample-app"
    source_dir.mkdir()
    (source_dir / "app.py").write_text("print('ok')\n", encoding="utf-8")

    versioned_venv = source_dir / ".venv39" / "lib" / "python3.9" / "site-packages"
    versioned_venv.mkdir(parents=True)
    (versioned_venv / "vendored.py").write_text("print('skip')\n", encoding="utf-8")

    config = AiSbomConfig(include_extensions={".py"}, enable_llm=False, max_files=20)

    files = list(AiSbomExtractor._iter_files(source_dir, config))
    names = {path.name for path, _size in files}

    assert "app.py" in names
    assert "vendored.py" not in names