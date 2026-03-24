"""Exercise the current NuGuard CLI by scanning one benchmark repo fixture.

This is intentionally CLI-only. It materializes a cached benchmark fixture to a
temporary folder, runs ``nuguard sbom generate``, and prints the resulting SBOM.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .evaluate import generate_sbom_cli, materialize_cached_fixture


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="Benchmark repo fixture name")
    parser.add_argument("--llm", action="store_true")
    parser.add_argument("--output", type=Path, help="Optional output path for the raw SBOM JSON")
    args = parser.parse_args()

    source_dir = materialize_cached_fixture(args.repo)
    try:
        doc = generate_sbom_cli(source_dir, use_llm=args.llm)
        text = doc.model_dump_json(indent=2, exclude_none=True)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text, encoding="utf-8")
        else:
            print(text)
    finally:
        import shutil

        shutil.rmtree(source_dir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
