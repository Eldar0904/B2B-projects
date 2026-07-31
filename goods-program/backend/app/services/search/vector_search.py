"""Vector search against a single Qdrant `products` collection (spec
section 9: one collection, metadata-filtered - not one DB per category
or per batch).

Uses whichever `EmbeddingProvider` is configured (see embeddings.py) to
embed both the master catalog (once, at index build time) and each
destination product (at query time), then does a cosine-similarity nearest
neighbor search in Qdrant.
"""

from __future__ import annotations

from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from app.services.search.embeddings import EmbeddingProvider
from app.services.search.types import MasterProductRecord


def build_qdrant_client(url: str | None, local_path: str) -> QdrantClient:
    """`url` set -> real Qdrant server (e.g. docker-compose). Otherwise ->
    embedded local mode, on-disk, no server process required.
    """
    if url:
        return QdrantClient(url=url)
    return QdrantClient(path=local_path)


class VectorIndex:
    def __init__(
        self,
        client: QdrantClient,
        embedding_provider: EmbeddingProvider,
        collection_name: str = "products",
    ):
        self._client = client
        self._embedding_provider = embedding_provider
        self._collection_name = collection_name
        self._id_by_point_id: dict[int, str] = {}

    def build(self, records: list[MasterProductRecord]) -> int:
        """(Re)build the collection from scratch with the given master
        product records. Returns the number of points indexed.
        """
        indexable = [r for r in records if not r.is_group_header]
        texts = [r.search_text() for r in indexable]

        self._embedding_provider.fit(texts)
        vectors = self._embedding_provider.embed(texts) if texts else []

        dim = self._embedding_provider.dim
        if self._client.collection_exists(self._collection_name):
            self._client.delete_collection(self._collection_name)
        self._client.create_collection(
            collection_name=self._collection_name,
            vectors_config=qmodels.VectorParams(size=dim, distance=qmodels.Distance.COSINE),
        )

        if not indexable:
            return 0

        points = []
        self._id_by_point_id = {}
        for i, (record, vector) in enumerate(zip(indexable, vectors)):
            self._id_by_point_id[i] = record.id
            points.append(
                qmodels.PointStruct(
                    id=i,
                    vector=vector.tolist(),
                    payload={
                        "master_product_id": record.id,
                        "external_id": record.external_id,
                        "normalized_name": record.normalized_name,
                    },
                )
            )

        # Batch upsert to avoid one giant request on large catalogs.
        batch_size = 500
        for start in range(0, len(points), batch_size):
            self._client.upsert(
                collection_name=self._collection_name,
                points=points[start : start + batch_size],
            )
        return len(points)

    def search(self, query_text: str, top_k: int = 20) -> list[tuple[str, float]]:
        """Returns [(master_product_id, score_0_to_1), ...]. Qdrant cosine
        scores are in [-1, 1]; clamped to [0, 1] since negative similarity
        isn't meaningful as a "match strength" signal here.
        """
        if not query_text.strip():
            return []
        vector = self._embedding_provider.embed([query_text])[0]
        response = self._client.query_points(
            collection_name=self._collection_name,
            query=vector.tolist(),
            limit=top_k,
        )
        return [(hit.payload["master_product_id"], max(hit.score, 0.0)) for hit in response.points]
