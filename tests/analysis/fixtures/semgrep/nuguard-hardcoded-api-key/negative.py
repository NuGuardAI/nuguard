# Keys from the environment, placeholders, and unrelated strings: 0 expected matches.
import os

from openai import OpenAI

OPENAI_KEY = os.environ["OPENAI_API_KEY"]
PLACEHOLDER = "sk-..."
SKU = "sku-12345"
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
