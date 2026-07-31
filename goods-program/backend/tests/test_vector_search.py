from qdrant_client import QdrantClient

from app.services.search.embeddings import TfidfEmbeddingProvider
from app.services.search.types import MasterProductRecord
from app.services.search.vector_search import VectorIndex


def _records():
    return [
        MasterProductRecord(id="1", external_id="A", normalized_name="стол детский регулируемый"),
        MasterProductRecord(id="2", external_id="B", normalized_name="кресло офисное кожаное"),
        MasterProductRecord(id="3", external_id="C", normalized_name="стол ученический регулируемый"),
        MasterProductRecord(id="4", external_id="G", normalized_name="группа мебель", is_group_header=True),
    ]


def test_build_indexes_only_non_group_headers(tmp_path):
    client = QdrantClient(path=str(tmp_path / "qdrant"))
    index = VectorIndex(client, TfidfEmbeddingProvider(), "products")
    count = index.build(_records())
    assert count == 3  # the group header is excluded


def test_search_ranks_exact_match_first(tmp_path):
    client = QdrantClient(path=str(tmp_path / "qdrant"))
    index = VectorIndex(client, TfidfEmbeddingProvider(), "products")
    index.build(_records())

    results = index.search("стол детский регулируемый", top_k=3)
    assert results[0][0] == "1"
    result_ids = [r[0] for r in results]
    assert "4" not in result_ids  # group header never returned


def test_empty_query_returns_no_results(tmp_path):
    client = QdrantClient(path=str(tmp_path / "qdrant"))
    index = VectorIndex(client, TfidfEmbeddingProvider(), "products")
    index.build(_records())
    assert index.search("   ", top_k=3) == []
