# Weights-only loading and JSON: 0 expected matches.
import json

import torch


def load(path: str):
    weights = torch.load(path, weights_only=True)
    with open(path + ".json") as fh:
        meta = json.load(fh)
    return weights, meta
