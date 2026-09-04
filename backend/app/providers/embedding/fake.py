import hashlib


class FakeEmbedding:
    def __init__(self, dim: int = 4):
        self.dim = dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            h = hashlib.sha256(t.encode()).digest()
            vec = [b / 255.0 for b in h[: self.dim]]
            out.append(vec)
        return out
