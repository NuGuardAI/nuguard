import re

def preprocess_text(text: str) -> str:
    if not text:
        return ""
    # Lowercase, remove special characters
    text = text.lower().strip()
    text = re.sub(r"[^a-zA-Z0-9\s]", "", text)
    return text
