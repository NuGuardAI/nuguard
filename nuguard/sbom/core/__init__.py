"""xelo.core — low-level parsing and enrichment utilities.

Active modules
--------------
ts_parser
    TypeScript/JavaScript AST parsing via tree-sitter (regex fallback when
    tree-sitter is not installed).  Used by all TypeScript framework adapters.

application_summary
    Deterministic scan-level summary builder: use-case text, modalities,
    API endpoints, IaC/deployment context.  Used by ``extractor.py``.

confidence
    Confidence aggregation and scoring algorithms for the LLM enrichment
    phase.  Used by ``extractor._llm_enrich()``.

verification
    LLM-based verification pass for uncertain detections (confidence 0.60–0.85).
    Provides the ``verify_uncertain_nodes`` coroutine consumed by
    ``extractor._llm_enrich()``.
"""
