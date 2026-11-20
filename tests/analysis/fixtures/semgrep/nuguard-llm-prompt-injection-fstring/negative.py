# No raw user input interpolated into a prompt: 0 expected matches.
from openai import OpenAI

client = OpenAI()


def sanitize(text: str) -> str:
    return text.replace("{", "").replace("}", "")


def ask_sanitized(user_text: str) -> str:
    resp = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": f"Answer this: {sanitize(user_text)}"}],
    )
    return resp.choices[0].message.content


def ask_plain(user_text: str) -> str:
    resp = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": user_text}],
    )
    return resp.choices[0].message.content


def ask_constant(chain) -> str:
    return chain.invoke("What is the capital of France?")


def system_fstring(name: str) -> str:
    return client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "system", "content": f"You are {name}."}],
    )
