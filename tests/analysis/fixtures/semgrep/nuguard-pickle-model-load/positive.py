# Unsafe deserialization of model artifacts: 4 expected matches.
import pickle

import joblib
import torch


def load_all(path: str, blob: bytes):
    with open(path, "rb") as fh:
        a = pickle.load(fh)
    b = pickle.loads(blob)
    c = torch.load(path)
    d = joblib.load(path)
    return a, b, c, d
