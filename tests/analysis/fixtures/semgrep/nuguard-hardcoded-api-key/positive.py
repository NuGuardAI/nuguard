# Literal provider keys: 4 expected matches.
from openai import OpenAI

OPENAI_KEY = "sk-proj-abcdefghijklmnopqrstuvwxyz012345"
GOOGLE_KEY = "AIzaSyA1234567890abcdefghijklmnopqrstu"
GITHUB_TOKEN = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"
client = OpenAI(api_key="sk-ant-abcdefghijklmnopqrstuvwxyz0123")
