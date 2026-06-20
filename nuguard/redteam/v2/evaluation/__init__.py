"""Phase 6 — layered evaluation pipeline (false-positive reduction core).

Runs evaluation layers in order — deterministic detectors → semantic
multi-judge → side-effect verifier → transferability scorer → judge-robustness
guard — escalating to LLMs only when deterministic layers are inconclusive, and
produces a single ``Verdict``.

Not implemented yet (scaffold).
"""
