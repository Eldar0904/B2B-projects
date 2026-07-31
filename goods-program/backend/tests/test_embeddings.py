import numpy as np

from app.services.search.embeddings import TfidfEmbeddingProvider


def _cosine(a, b):
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


def test_similar_texts_have_higher_similarity_than_unrelated():
    provider = TfidfEmbeddingProvider()
    corpus = [
        "стол детский регулируемый",
        "стол ученический регулируемый",
        "кресло офисное кожаное",
    ]
    provider.fit(corpus)
    vectors = provider.embed(corpus)

    sim_similar = _cosine(vectors[0], vectors[1])  # both tables
    sim_unrelated = _cosine(vectors[0], vectors[2])  # table vs chair
    assert sim_similar > sim_unrelated


def test_identical_text_has_similarity_one():
    provider = TfidfEmbeddingProvider()
    provider.fit(["стол детский регулируемый"])
    vectors = provider.embed(["стол детский регулируемый", "стол детский регулируемый"])
    assert _cosine(vectors[0], vectors[1]) > 0.999


def test_empty_corpus_does_not_crash():
    provider = TfidfEmbeddingProvider()
    provider.fit([])
    vectors = provider.embed(["стол"])
    assert vectors.shape[0] == 1
