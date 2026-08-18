"""Tests for the lightweight C# structural parser."""

from __future__ import annotations

from nuguard.sbom.core.csharp_parser import parse_csharp


def test_using_directives_capture_global_static_and_alias() -> None:
    result = parse_csharp(
        """global using Azure.AI.OpenAI;
using static System.Math;
using SK = Microsoft.SemanticKernel;
""",
        "Imports.cs",
    )

    assert result.file_path == "Imports.cs"

    assert [item.namespace for item in result.using_directives] == [
        "Azure.AI.OpenAI",
        "System.Math",
        "Microsoft.SemanticKernel",
    ]

    assert result.using_directives[0].is_global
    assert result.using_directives[1].is_static
    assert result.using_directives[2].alias == "SK"
    assert result.imports == result.using_directives


def test_type_declarations_capture_kinds_modifiers_and_bases() -> None:
    result = parse_csharp(
        """public sealed class ChatService<T> : IChatService, IDisposable
    where T : class
{
}
public interface IChatClient {}
public readonly record struct ChatResult(string Value);
"""
    )

    assert [item.name for item in result.type_declarations] == [
        "ChatService",
        "IChatClient",
        "ChatResult",
    ]

    chat_service = result.type_declarations[0]

    assert chat_service.kind == "class"
    assert chat_service.modifiers == (
        "public",
        "sealed",
    )
    assert chat_service.base_types == (
        "IChatService",
        "IDisposable",
    )
    assert result.type_declarations[2].kind == "record struct"
    assert result.classes == result.type_declarations


def test_methods_and_constructor_capture_signatures() -> None:
    result = parse_csharp(
        """public class ChatService
{
    public ChatService(string name, int retries = 3) {}

    public async Task<string> CompleteAsync(
        string input,
        CancellationToken cancellationToken = default)
    {
        return input;
    }
}
"""
    )

    constructor, method = result.method_declarations

    assert constructor.is_constructor
    assert constructor.return_type is None
    assert constructor.containing_type == "ChatService"
    assert constructor.parameters == (
        "string name",
        "int retries = 3",
    )

    assert method.name == "CompleteAsync"
    assert method.return_type == "Task<string>"
    assert method.modifiers == (
        "public",
        "async",
    )
    assert method.parameters[1] == "CancellationToken cancellationToken = default"
    assert result.methods == result.method_declarations


def test_interface_and_expression_bodied_methods_are_parsed() -> None:
    result = parse_csharp(
        """public interface IChatClient
{
    Task<string> CompleteAsync(string prompt);
}
[ApiController]
[Route("api/format")]
public class Formatter
{
    [HttpPost("trim")]
    public string Format(string value) => value.Trim();
}
"""
    )

    assert [
        (
            item.name,
            item.return_type,
        )
        for item in result.method_declarations
    ] == [
        (
            "CompleteAsync",
            "Task<string>",
        ),
        (
            "Format",
            "string",
        ),
    ]

    assert result.type_declarations[1].attributes == (
        "ApiController",
        'Route("api/format")',
    )

    assert result.method_declarations[1].attributes == ('HttpPost("trim")',)


def test_strings_capture_forms_assignment_and_method_context() -> None:
    source = (
        "public class PromptBuilder\n"
        "{\n"
        "    public string Build(string user)\n"
        "    {\n"
        '        var regular = "System: be helpful\\nUser:";\n'
        '        var verbatim = @"C:\\prompts\\system.txt";\n'
        '        var interpolated = $"Hello {user}";\n'
        '        var raw = """\n'
        "You are a careful assistant.\n"
        '""";\n'
        '        var rawInterpolated = $$"""Hello {{user}}""";\n'
        "        return interpolated;\n"
        "    }\n"
        "}\n"
    )

    result = parse_csharp(source)

    literals = {item.assigned_to: item for item in result.string_literals}

    assert literals["regular"].value == "System: be helpful\nUser:"
    assert literals["regular"].enclosing_method == "Build"
    assert literals["verbatim"].is_verbatim
    assert literals["interpolated"].interpolation_expressions == ("user",)
    assert literals["raw"].is_raw
    assert literals["raw"].is_potential_prompt
    assert literals["rawInterpolated"].interpolation_expressions == ("user",)


def test_comments_chars_and_string_contents_are_not_declarations() -> None:
    result = parse_csharp(
        """// using Fake.Namespace;
/* public class FakeBlock { void Bad() {} } */
public class Real
{
    private char quote = '\\'';
    private string sample = "class FakeString { void Nope() {} }";
    public void Good() {}
}
"""
    )

    assert [item.name for item in result.type_declarations] == ["Real"]

    assert [item.name for item in result.method_declarations] == ["Good"]

    assert result.using_directives == []


def test_malformed_source_preserves_partial_results() -> None:
    result = parse_csharp(
        """using Azure.AI.OpenAI;
public class Broken
{
    public void Run()
    {
        var prompt = "You are still visible";
"""
    )

    assert result.parse_error is not None
    assert "Unmatched opening brace" in result.parse_error
    assert result.using_directives[0].namespace == "Azure.AI.OpenAI"
    assert result.type_declarations[0].name == "Broken"
    assert result.method_declarations[0].name == "Run"
    assert result.string_literals[0].assigned_to == "prompt"


def test_empty_source_is_false_and_preserves_context() -> None:
    result = parse_csharp(
        "",
        "Empty.cs",
    )

    assert not result
    assert result.file_path == "Empty.cs"
    assert result.parse_error is None


def test_method_parser_handles_nested_parameters_and_initializer() -> None:
    result = parse_csharp(
        """public class Service : BaseService
{
    public Service(
        string name = nameof(DefaultName),
        Func<(int X, int Y), Task> handler = null)
        : base(CreateOptions())
    {
    }

    public void Run(
        (int X, int Y) point,
        Action<Func<int, string>> callback)
    {
    }
}
"""
    )

    assert [method.name for method in result.method_declarations] == [
        "Service",
        "Run",
    ]

    constructor, method = result.method_declarations

    assert constructor.is_constructor
    assert constructor.parameters == (
        "string name = nameof(DefaultName)",
        "Func<(int X, int Y), Task> handler = null",
    )
    assert ": base(CreateOptions())" in constructor.signature

    assert method.parameters == (
        "(int X, int Y) point",
        "Action<Func<int, string>> callback",
    )


def test_method_calls_inside_bodies_are_not_declarations() -> None:
    result = parse_csharp(
        """public class PromptBuilder
{
    public string Run()
    {
        return BuildPrompt();
    }

    private string BuildPrompt() => "prompt";
}
"""
    )

    assert [method.name for method in result.method_declarations] == [
        "Run",
        "BuildPrompt",
    ]


def test_raw_string_uses_matching_quote_delimiter_length() -> None:
    result = parse_csharp(
        "public class RawPrompt\n"
        "{\n"
        "    public void Run()\n"
        "    {\n"
        '        var prompt = """"text with """ inside"""";\n'
        "    }\n"
        "}\n"
    )

    assert result.parse_error is None
    assert len(result.string_literals) == 1

    literal = result.string_literals[0]

    assert literal.is_raw
    assert literal.value == 'text with """ inside'
    assert literal.assigned_to == "prompt"
    assert literal.enclosing_method == "Run"


def test_multiline_string_assignment_preserves_target() -> None:
    result = parse_csharp(
        """public class PromptBuilder
{
    public string Build()
    {
        var prompt =
            "You are a careful assistant.";
        return prompt;
    }
}
"""
    )

    assert len(result.string_literals) == 1
    assert result.string_literals[0].assigned_to == "prompt"


def test_unterminated_strings_are_masked_and_reported() -> None:
    cases = (
        (
            '"',
            "unterminated regular string",
        ),
        (
            '@"',
            "unterminated verbatim string",
        ),
        (
            '""""',
            "unterminated raw string",
        ),
    )

    for opener, expected_error in cases:
        source = (
            "public class Real\n"
            "{\n"
            "    public void Run()\n"
            "    {\n"
            f"        var broken = {opener}unterminated\n"
            "        public class Fake\n"
            "        {\n"
            "            public void Bad() {}\n"
            "        }\n"
            "    }\n"
            "}\n"
        )

        result = parse_csharp(source)

        assert result.parse_error is not None
        assert expected_error in result.parse_error.lower()
        assert [item.name for item in result.type_declarations] == ["Real"]
        assert [item.name for item in result.method_declarations] == ["Run"]


def test_combined_attribute_sections_are_split() -> None:
    result = parse_csharp(
        """public class SearchPlugin
{
    [KernelFunction, Description("query, text")]
    public string Search(string query) => query;
}
"""
    )

    assert len(result.method_declarations) == 1

    method = result.method_declarations[0]

    assert method.name == "Search"
    assert method.attributes == (
        "KernelFunction",
        'Description("query, text")',
    )
