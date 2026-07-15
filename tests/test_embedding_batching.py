from types import SimpleNamespace

from app.services.vector_embedding_service import DashScopeEmbeddings


class _FakeEmbeddingsEndpoint:
    def __init__(self):
        self.batches = []

    def create(self, *, input, **_kwargs):
        self.batches.append(input)
        data = [
            SimpleNamespace(index=index, embedding=[float(index)])
            for index, _text in reversed(list(enumerate(input)))
        ]
        return SimpleNamespace(data=data)


def test_embed_documents_splits_requests_and_preserves_response_order():
    endpoint = _FakeEmbeddingsEndpoint()
    embeddings = DashScopeEmbeddings.__new__(DashScopeEmbeddings)
    embeddings.client = SimpleNamespace(embeddings=endpoint)
    embeddings.model = "text-embedding-v4"
    embeddings.dimensions = 1024

    result = embeddings.embed_documents([f"chunk-{index}" for index in range(14)])

    assert [len(batch) for batch in endpoint.batches] == [10, 4]
    assert result == [[float(index)] for index in range(10)] + [
        [float(index)] for index in range(4)
    ]
