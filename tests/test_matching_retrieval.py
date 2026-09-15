"""Retrieval: lexical ranking, the model-tagged vector cache, and fallback."""

from __future__ import annotations

from unittest import mock

from src.matching.models import Requirement, ResumeChunk
from src.matching.retrieval import LexicalIndex, Retriever
from src.matching.vectors import EmbeddingUnavailable
from tests.helpers import TempDBTestCase

CHUNKS = [
    ResumeChunk(
        id="work-0",
        kind="work",
        ref="Backend Engineer @ Acme",
        text="Built Django and FastAPI services in Python; ran them on Kubernetes",
    ),
    ResumeChunk(
        id="work-1",
        kind="work",
        ref="Frontend Engineer @ Beta",
        text="React and TypeScript dashboards with Redux",
    ),
    ResumeChunk(id="skills-0", kind="skills", ref="Skills", text="python; sql; terraform"),
]

REQUIREMENTS = [
    Requirement(id="r1", text="Experience running services on Kubernetes"),
    Requirement(id="r2", text="Strong React and TypeScript skills"),
]


class LexicalIndexTests(TempDBTestCase):
    def test_ranks_the_relevant_chunk_first(self):
        index = LexicalIndex(CHUNKS)
        scores = index.scores("Kubernetes deployments")
        self.assertEqual(max(range(len(scores)), key=scores.__getitem__), 0)

    def test_unrelated_query_scores_zero(self):
        self.assertEqual(max(LexicalIndex(CHUNKS).scores("underwater basket weaving")), 0.0)

    def test_stopwords_alone_do_not_rank(self):
        self.assertEqual(max(LexicalIndex(CHUNKS).scores("years of experience with the")), 0.0)


class LexicalRetrieverTests(TempDBTestCase):
    def test_each_requirement_gets_its_own_best_chunk(self):
        retriever = Retriever(CHUNKS, allow_vectors=False)
        retrieved = retriever.retrieve(REQUIREMENTS)
        self.assertEqual(retriever.backend, "lexical")
        self.assertEqual(retrieved["r1"][0][0].id, "work-0")
        self.assertEqual(retrieved["r2"][0][0].id, "work-1")

    def test_no_chunks_yields_empty_evidence(self):
        retriever = Retriever([], allow_vectors=False)
        self.assertEqual(retriever.retrieve(REQUIREMENTS), {"r1": [], "r2": []})

    def test_top_k_caps_evidence(self):
        retriever = Retriever(CHUNKS, allow_vectors=False)
        retrieved = retriever.retrieve([Requirement(id="r1", text="python kubernetes react")], top_k=2)
        self.assertLessEqual(len(retrieved["r1"]), 2)


class VectorRetrieverTests(TempDBTestCase):
    """Vector path with a stub embedder, so no model is needed."""

    VECTORS = {
        "Built Django and FastAPI services in Python; ran them on Kubernetes": [1.0, 0.0],
        "React and TypeScript dashboards with Redux": [0.0, 1.0],
        "python; sql; terraform": [0.7, 0.0],
        "Experience running services on Kubernetes": [1.0, 0.0],
        "Strong React and TypeScript skills": [0.0, 1.0],
    }

    def _embed(self, texts, **kwargs):
        return [self.VECTORS.get(t, [0.0, 0.0]) for t in texts]

    def test_vector_backend_ranks_by_similarity(self):
        with mock.patch("src.matching.vectors.embed_texts", side_effect=self._embed), mock.patch(
            "src.matching.vectors.embedding_settings", return_value=("ollama", "test-model")
        ):
            retriever = Retriever(CHUNKS, user_id=1, owner_prefix="u1:")
            self.assertEqual(retriever.backend, "vector")
            retrieved = retriever.retrieve(REQUIREMENTS, job_key="job-1")
        self.assertEqual(retrieved["r1"][0][0].id, "work-0")
        self.assertEqual(retrieved["r2"][0][0].id, "work-1")

    def test_vectors_are_cached_and_reused_across_runs(self):
        embed = mock.Mock(side_effect=self._embed)
        with mock.patch("src.matching.vectors.embed_texts", embed), mock.patch(
            "src.matching.vectors.embedding_settings", return_value=("ollama", "test-model")
        ):
            Retriever(CHUNKS, user_id=1, owner_prefix="u1:")
            first_calls = embed.call_count
            Retriever(CHUNKS, user_id=1, owner_prefix="u1:")
            self.assertEqual(embed.call_count, first_calls, "second build must hit the cache")

    def test_changing_the_embedding_model_invalidates_the_cache(self):
        embed = mock.Mock(side_effect=self._embed)
        with mock.patch("src.matching.vectors.embed_texts", embed):
            with mock.patch(
                "src.matching.vectors.embedding_settings", return_value=("ollama", "model-a")
            ):
                Retriever(CHUNKS, user_id=1, owner_prefix="u1:")
            calls_after_first = embed.call_count
            with mock.patch(
                "src.matching.vectors.embedding_settings", return_value=("ollama", "model-b")
            ):
                Retriever(CHUNKS, user_id=1, owner_prefix="u1:")
        self.assertGreater(embed.call_count, calls_after_first, "a new model must re-embed")

    def test_edited_chunk_text_invalidates_its_vector(self):
        embed = mock.Mock(side_effect=self._embed)
        with mock.patch("src.matching.vectors.embed_texts", embed), mock.patch(
            "src.matching.vectors.embedding_settings", return_value=("ollama", "test-model")
        ):
            Retriever(CHUNKS, user_id=1, owner_prefix="u1:")
            calls_after_first = embed.call_count
            edited = [CHUNKS[0].model_copy(update={"text": "Now writes Rust"}), *CHUNKS[1:]]
            Retriever(edited, user_id=1, owner_prefix="u1:")
        self.assertGreater(embed.call_count, calls_after_first)

    def test_unavailable_backend_falls_back_to_lexical_with_a_warning(self):
        with mock.patch(
            "src.matching.vectors.embed_texts",
            side_effect=EmbeddingUnavailable("provider has no embeddings endpoint"),
        ), mock.patch(
            "src.matching.vectors.embedding_settings", return_value=("groq", "none")
        ):
            retriever = Retriever(CHUNKS, user_id=1, owner_prefix="u1:")
            retrieved = retriever.retrieve(REQUIREMENTS, job_key="job-1")
        self.assertEqual(retriever.backend, "lexical")
        self.assertIn("embeddings", retriever.warning)
        self.assertEqual(retrieved["r1"][0][0].id, "work-0")
