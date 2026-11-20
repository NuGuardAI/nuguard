# HTTP calls not derived from model output: 0 expected matches.
import requests

API = "https://api.example.com/status"


def health():
    return requests.get(API, timeout=5)


def fetch(user_supplied_but_validated: str):
    return requests.get("https://allowlisted.example.com/" + "fixed", timeout=5)
