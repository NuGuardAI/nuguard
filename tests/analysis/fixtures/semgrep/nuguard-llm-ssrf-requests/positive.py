# URLs taken from model output reach HTTP clients: 3 expected matches.
import httpx
import requests


def fetch_openai(client):
    url = client.chat.completions.create(model="gpt-4o", messages=[]).choices[0].message.content
    return requests.get(url, timeout=5)


def fetch_claude(claude):
    reply = claude.messages.create(model="claude-sonnet-4-5", max_tokens=64, messages=[])
    target = reply.content[0].text
    return httpx.post(target, json={})


def fetch_chain(chain):
    link = chain.invoke("Which URL should I fetch?")
    return requests.post(link)
