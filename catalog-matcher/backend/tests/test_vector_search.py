import pytest
from qdrant_client import QdrantClient

from app.services.search import vector_search as vector_search_module
from app.services.search.embeddings import TfidfEmbeddingProvider
from app.services.search.types import MasterProductRecord
from app.services.search.vector_search import VectorIndex


@pytest.fixture(autouse=True)
def _isolated_index_cache(tmp_path, monkeypatch):
    """Every test in this file gets its own on-disk build cache directory -
    never the real default (_CACHE_DIR's module-level default resolves to
    a real, shared /app/.index_cache inside the container) - so a test run
    can never leave stray manifest files behind or, in principle, read one
    left over from a previous run. Applies to every test here automatically,
    including ones that don't otherwise care about the cache, since
    VectorIndex.build() always consults it now.
    """
    monkeypatch.setattr(vector_search_module, "_CACHE_DIR", tmp_path / ".index_cache")


def _records():
    return [
        MasterProductRecord(id="1", external_id="A", normalized_name="стол детский регулируемый"),
        MasterProductRecord(id="2", external_id="B", normalized_name="кресло офисное кожаное"),
        MasterProductRecord(id="3", external_id="C", normalized_name="стол ученический регулируемый"),
        MasterProductRecord(id="4", external_id="G", normalized_name="группа мебель", is_group_header=True),
    ]


class _CountingProvider(TfidfEmbeddingProvider):
    """Same real behavior as TfidfEmbeddingProvider, plus a call counter on
    embed() - the specific method the on-disk build cache (HANDOFF.md
    section 18) is meant to skip on a cache hit. fit() is deliberately NOT
    counted here - it's supposed to run every time, cache hit or not (see
    VectorIndex.build()'s own comment for why).
    """

    def __init__(self):
        super().__init__()
        self.embed_calls = 0

    def embed(self, texts):
        self.embed_calls += 1
        return super().embed(texts)


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


# --- On-disk build cache (HANDOFF.md section 18 - skip re-embedding on a
# restart when the catalog hasn't actually changed) -------------------------
#
# Each test points _CACHE_DIR at an isolated tmp_path so these never read or
# write the real dev/prod cache at /app/.index_cache, and reuses ONE
# QdrantClient across two separate VectorIndex instances - simulating two
# separate app process starts talking to the same durable Qdrant storage,
# same as the real backend restarting against the real Qdrant server.


def test_build_skips_reembedding_when_content_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(vector_search_module, "_CACHE_DIR", tmp_path / ".index_cache")
    client = QdrantClient(path=str(tmp_path / "qdrant"))

    provider1 = _CountingProvider()
    count1 = VectorIndex(client, provider1, "products").build(_records())
    assert provider1.embed_calls == 1
    assert count1 == 3

    provider2 = _CountingProvider()
    count2 = VectorIndex(client, provider2, "products").build(_records())  # same content, simulates a restart
    assert provider2.embed_calls == 0  # the whole point of the cache
    assert count2 == 3


def test_build_recomputes_when_content_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(vector_search_module, "_CACHE_DIR", tmp_path / ".index_cache")
    client = QdrantClient(path=str(tmp_path / "qdrant"))

    provider1 = _CountingProvider()
    VectorIndex(client, provider1, "products").build(_records())
    assert provider1.embed_calls == 1

    changed = _records()
    changed[0].normalized_name = "совершенно другое название товара"

    provider2 = _CountingProvider()
    count2 = VectorIndex(client, provider2, "products").build(changed)
    assert provider2.embed_calls == 1  # real content change -> a real rebuild, not skipped
    assert count2 == 3


def test_search_still_correct_after_a_cache_hit(tmp_path, monkeypatch):
    """Not just "embed() wasn't called" - the skipped build must leave a
    genuinely queryable, correct collection behind, not an empty or stale
    one.
    """
    monkeypatch.setattr(vector_search_module, "_CACHE_DIR", tmp_path / ".index_cache")
    client = QdrantClient(path=str(tmp_path / "qdrant"))

    VectorIndex(client, TfidfEmbeddingProvider(), "products").build(_records())
    index2 = VectorIndex(client, TfidfEmbeddingProvider(), "products")
    index2.build(_records())  # cache hit

    results = index2.search("стол детский регулируемый", top_k=3)
    assert results[0][0] == "1"
    assert "4" not in [r[0] for r in results]


def test_build_treats_a_corrupt_manifest_as_a_cache_miss(tmp_path, monkeypatch):
    """A manifest that can't be parsed must fall back to a real rebuild,
    never crash the whole startup - same "never let a cache failure take
    down something that would otherwise succeed" rule as _write_manifest's
    own try/except.
    """
    cache_dir = tmp_path / ".index_cache"
    monkeypatch.setattr(vector_search_module, "_CACHE_DIR", cache_dir)
    client = QdrantClient(path=str(tmp_path / "qdrant"))

    VectorIndex(client, TfidfEmbeddingProvider(), "products").build(_records())
    (cache_dir / "products.json").write_text("not valid json{{{", encoding="utf-8")

    provider2 = _CountingProvider()
    count2 = VectorIndex(client, provider2, "products").build(_records())
    assert provider2.embed_calls == 1  # fell back to a real rebuild
    assert count2 == 3


def test_build_recomputes_when_qdrant_collection_is_missing_despite_a_valid_manifest(tmp_path, monkeypatch):
    """The manifest can be right while Qdrant itself is wrong (e.g. a fresh
    Qdrant volume with an old cache file left over from before) - the
    collection's actual existence/count must always be the final check, not
    just the manifest's say-so.
    """
    monkeypatch.setattr(vector_search_module, "_CACHE_DIR", tmp_path / ".index_cache")
    client = QdrantClient(path=str(tmp_path / "qdrant"))

    provider1 = _CountingProvider()
    VectorIndex(client, provider1, "products").build(_records())
    assert provider1.embed_calls == 1

    client.delete_collection("products")  # simulate a wiped/fresh Qdrant

    provider2 = _CountingProvider()
    count2 = VectorIndex(client, provider2, "products").build(_records())
    assert provider2.embed_calls == 1  # manifest alone was not trusted
    assert count2 == 3
