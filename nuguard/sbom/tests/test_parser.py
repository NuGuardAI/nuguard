"""Unit tests for xelo.ast_parser.

Tests validate that the stdlib-ast-based parser correctly extracts imports,
class instantiations, function calls, and string literals from Python source.
"""
from __future__ import annotations


from xelo.ast_parser import ParseResult, parse


class TestImports:
    def test_bare_import(self) -> None:
        result = parse("import openai\n")
        assert len(result.imports) == 1
        imp = result.imports[0]
        assert imp.module == "openai"
        assert imp.names == []
        assert imp.alias is None

    def test_from_import_single(self) -> None:
        result = parse("from langgraph.graph import StateGraph\n")
        imp = result.imports[0]
        assert imp.module == "langgraph.graph"
        assert "StateGraph" in imp.names

    def test_from_import_multiple(self) -> None:
        result = parse("from langchain_core.messages import SystemMessage, HumanMessage\n")
        imp = result.imports[0]
        assert "SystemMessage" in imp.names
        assert "HumanMessage" in imp.names

    def test_import_with_alias(self) -> None:
        result = parse("import numpy as np\n")
        imp = result.imports[0]
        assert imp.module == "numpy"
        assert imp.alias == "np"

    def test_multiple_imports(self) -> None:
        src = "import openai\nfrom anthropic import Anthropic\nimport os\n"
        result = parse(src)
        modules = [i.module for i in result.imports]
        assert "openai" in modules
        assert "anthropic" in modules
        assert "os" in modules


class TestInstantiations:
    def test_simple_instantiation(self) -> None:
        result = parse("agent = Agent(name='bot')\n")
        assert len(result.instantiations) == 1
        inst = result.instantiations[0]
        assert inst.class_name == "Agent"
        assert inst.assigned_to == "agent"
        assert inst.args.get("name") == "bot"

    def test_instantiation_with_positional_args(self) -> None:
        result = parse("graph = StateGraph(AgentState)\n")
        inst = result.instantiations[0]
        assert inst.class_name == "StateGraph"
        # AgentState is a Name node → recorded as $AgentState
        assert any("AgentState" in str(a) for a in inst.positional_args)

    def test_keyword_string_and_int_args(self) -> None:
        result = parse("client = OpenAI(model='gpt-4o', max_tokens=1024)\n")
        inst = result.instantiations[0]
        assert inst.args.get("model") == "gpt-4o"
        assert inst.args.get("max_tokens") == 1024

    def test_keyword_list_arg(self) -> None:
        result = parse("agent = Agent(tools=['search', 'calculator'])\n")
        inst = result.instantiations[0]
        tools = inst.args.get("tools")
        assert isinstance(tools, list)
        assert "search" in tools

    def test_nested_class_call(self) -> None:
        src = "runner = Runner(agent=Agent(name='bot'))\n"
        result = parse(src)
        class_names = [i.class_name for i in result.instantiations]
        assert "Agent" in class_names
        assert "Runner" in class_names

    def test_unassigned_instantiation(self) -> None:
        result = parse("Agent(name='bot')\n")
        assert any(i.class_name == "Agent" for i in result.instantiations)

    def test_line_numbers(self) -> None:
        src = "\n\nllm = ChatOpenAI(model='gpt-4o')\n"
        result = parse(src)
        inst = next(i for i in result.instantiations if i.class_name == "ChatOpenAI")
        assert inst.line == 3


class TestFunctionCalls:
    def test_method_call_with_receiver(self) -> None:
        result = parse("workflow.add_node('agent', my_func)\n")
        call = next(c for c in result.function_calls if c.function_name == "add_node")
        assert call.receiver == "workflow"
        assert "agent" in call.positional_args

    def test_method_call_add_edge(self) -> None:
        result = parse("workflow.add_edge('agent', 'tools')\n")
        call = next(c for c in result.function_calls if c.function_name == "add_edge")
        assert "agent" in call.positional_args
        assert "tools" in call.positional_args

    def test_bare_function_call(self) -> None:
        result = parse("result = create_react_agent(llm, tools)\n")
        call = next(
            (c for c in result.function_calls if c.function_name == "create_react_agent"),
            None,
        )
        assert call is not None
        assert call.assigned_to == "result"

    def test_api_create_call(self) -> None:
        src = "resp = client.chat.completions.create(model='gpt-4o', messages=[])\n"
        result = parse(src)
        call = next(c for c in result.function_calls if c.function_name == "create")
        assert call.args.get("model") == "gpt-4o"


class TestStringLiterals:
    def test_captures_long_string(self) -> None:
        src = 'PROMPT = "You are a helpful assistant that answers questions carefully."\n'
        result = parse(src)
        assert any("helpful assistant" in lit.value for lit in result.string_literals)

    def test_ignores_short_strings(self) -> None:
        result = parse("x = 'hi'\n")
        assert result.string_literals == []

    def test_multiline_string(self) -> None:
        src = '''
SYSTEM = """
You are a senior software engineer. Your task is to review code for bugs and
security vulnerabilities. Be thorough and precise in your analysis.
"""
'''
        result = parse(src)
        assert any("senior software engineer" in lit.value for lit in result.string_literals)

    def test_scope_context(self) -> None:
        src = '''
def my_agent():
    prompt = "You are an expert research assistant with deep knowledge of AI systems."
'''
        result = parse(src)
        lit = next(s for s in result.string_literals if "research assistant" in s.value)
        assert lit.context == "my_agent"


class TestEdgeCases:
    def test_syntax_error_graceful(self) -> None:
        result = parse("def bad syntax (\n")
        assert result.parse_error is not None
        assert result.imports == []
        assert result.instantiations == []

    def test_empty_source(self) -> None:
        result = parse("")
        assert isinstance(result, ParseResult)
        assert result.imports == []

    def test_type_annotation_assignment(self) -> None:
        result = parse("llm: ChatOpenAI = ChatOpenAI(model='gpt-4o')\n")
        insts = [i for i in result.instantiations if i.class_name == "ChatOpenAI"]
        assert insts

    def test_star_import_excluded(self) -> None:
        result = parse("from langgraph import *\n")
        imp = result.imports[0]
        assert imp.names == []
