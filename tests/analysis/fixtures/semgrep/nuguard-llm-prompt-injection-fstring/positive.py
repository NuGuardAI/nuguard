# Each call interpolates raw user input into an LLM prompt: 5 expected matches.
import anthropic
from openai import OpenAI

client = OpenAI()
claude = anthropic.Anthropic()


def ask_openai(user_text: str) -> str:
    resp = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "system", "content": "Be helpful."},
                  {"role": "user", "content": f"Answer this: {user_text}"}],
    )
    return resp.choices[0].message.content


def ask_claude(user_text: str) -> str:
    msg = claude.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=256,
        messages=[{"role": "user", "content": f"Summarize: {user_text}"}],
    )
    return msg.content[0].text


def ask_legacy(llm, user_text: str) -> str:
    return llm.chat(messages=[{"role": "user", "content": f"Q: {user_text}"}])


def ask_chain(chain, user_text: str) -> str:
    return chain.invoke(f"Translate to French: {user_text}")


async def ask_chain_async(chain, user_text: str) -> str:
    return await chain.ainvoke(f"Classify: {user_text}")
