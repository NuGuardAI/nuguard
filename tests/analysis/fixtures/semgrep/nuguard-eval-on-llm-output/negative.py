# Constant expressions and safe parsing: 0 expected matches.
import ast


def run(text: str):
    value = ast.literal_eval(text)
    eval("1 + 1")
    exec("x = 2")
    return value
