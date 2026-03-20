"""xelo.adapters — pluggable framework detection adapters.

Sub-packages
------------
python/
    AST-aware adapters for Python files.  Each module targets one agentic
    framework: LangGraph, OpenAI Agents, AutoGen, CrewAI, Semantic Kernel,
    LlamaIndex, and generic LLM client detection.

typescript/
    Tree-sitter (or regex) adapters for TypeScript/JavaScript files.
    Mirrors the Python adapter set and adds: LangGraph TS, OpenAI Agents TS,
    Bedrock Agents, Google ADK, DataStores, and Prompts.

Base classes / registry
-----------------------
base.py      ``FrameworkAdapter`` and ``DetectionAdapter`` ABCs, plus
             ``ComponentDetection`` and ``RelationshipHint`` data classes.
registry.py  ``default_framework_adapters()`` and ``default_registry()``
             factory functions used by ``AiSbomExtractor``.
"""
