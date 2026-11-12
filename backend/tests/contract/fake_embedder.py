"""Deterministic embedder for steward's embedded mode: hashed bag of words, L2-normalised.

Same idea as steward's own test fake (its `tests/fakes/embedder.py`, not part of the package): texts
sharing words get similar vectors, so retrieval works without downloading a model.
"""

import hashlib
import math
import re

DIM = 384
_TOKEN = re.compile(r"[\w]+", re.UNICODE)


class FakeEmbedder:
    model = "banyan-fake-hashed-bow"
    dim = DIM

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]

    @staticmethod
    def _one(text: str) -> list[float]:
        vector = [0.0] * DIM
        for token in _TOKEN.findall(text.lower()) or [text]:
            digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
            vector[int.from_bytes(digest[:4], "big") % DIM] += 1.0 if digest[4] & 1 else -1.0
        norm = math.sqrt(sum(x * x for x in vector)) or 1.0
        return [x / norm for x in vector]
