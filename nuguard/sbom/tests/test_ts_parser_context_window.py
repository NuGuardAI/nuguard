"""Regression tests for docs/sbom-accuracy-plan.md #2: the tree-sitter
*fallback* regex path (``TypeScriptParser._parse_with_regex`` ->
``_extract_string_literals``'s ``find_context_in_text``) must attribute a
matched string literal's context to the closest preceding key/assignment on
the line, not the first/leftmost one. Before this fix, a line with multiple
sibling object-literal entries (the common i18n/locale-bundle shape) had
every literal on the line wrongly attributed to the first key — for two
compounding reasons this module pins independently: (1) ``.search()`` always
returns the first match, and (2) the text slice fed to the context patterns
excluded the literal's own opening quote, which the patterns require as a
trailing anchor, making the *actually* closest key structurally unmatchable
so it silently fell back to an earlier, unrelated key.
"""
from __future__ import annotations

from nuguard.sbom.core.ts_parser import TypeScriptParser


def test_context_attributes_to_closest_preceding_key_not_first():
    source = (
        'const x = { name: "Foo bar baz qux quux corge grault garply waldo fred", '
        'content: "Bar baz qux quux corge grault garply waldo fred plugh xyzzy" };'
    )
    result = TypeScriptParser()._parse_with_regex(source)

    by_value = {lit.value: lit.context for lit in result.string_literals}
    assert by_value["Foo bar baz qux quux corge grault garply waldo fred"] == "name"
    assert (
        by_value["Bar baz qux quux corge grault garply waldo fred plugh xyzzy"]
        == "content"
    )


def test_sibling_literals_on_same_line_get_distinct_contexts():
    source = (
        'const strings = { title: "Some very long title string here padded out nicely", '
        'body: "Some other very long body string here also padded out nicely too" };'
    )
    result = TypeScriptParser()._parse_with_regex(source)

    contexts = {lit.value: lit.context for lit in result.string_literals}
    assert len(contexts) == 2
    assert len(set(contexts.values())) == 2
    assert contexts["Some very long title string here padded out nicely"] == "title"
    assert (
        contexts["Some other very long body string here also padded out nicely too"]
        == "body"
    )
