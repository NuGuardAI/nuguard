"""Streaming-style benchmark runner for NuGuard.

This keeps the older ``evaluate_streaming.py`` entrypoint name but runs the
current benchmark evaluation pipeline one repo at a time and emits JSON lines.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from .evaluate import evaluate_repo, list_available_benchmarks


async def _run(repo_names: list[str], mode: str, use_llm: bool) -> list[dict]:
    results = []
    for repo_name in repo_names:
        result = await evaluate_repo(repo_name, mode=mode, use_llm=use_llm, verbose=False)
        payload = result.model_dump(mode="json")
        results.append(payload)
        print(json.dumps(payload), flush=True)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", help="Evaluate one benchmark repo")
    parser.add_argument("--all", action="store_true", help="Evaluate all benchmark repos")
    parser.add_argument("--mode", choices=["python", "cli"], default="python")
    parser.add_argument("--llm", action="store_true")
    parser.add_argument("--output", type=Path, help="Optional JSON file for the collected results")
    args = parser.parse_args()

    if not args.repo and not args.all:
        parser.error("pass --repo <name> or --all")

    repo_names = [args.repo] if args.repo else list_available_benchmarks()
    results = asyncio.run(_run(repo_names, args.mode, args.llm))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

