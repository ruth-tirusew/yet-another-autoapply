"""Chunked vector prefilter: posting_chunk_requirements id scheme, cache
sharing across users, embed-stage pre-warming, and threshold gating."""

from __future__ import annotations

import json
from unittest import mock

from src.matching.chunking import chunk_posting
from src.matching.prefilter import PrefilterBatch, posting_chunk_requirements
from tests.helpers import TempDBTestCase

POSTING = """Requirements
- 5+ years of Python experience
- Experience with Kubernetes

Nice to have
- Go
"""

RESUME = {
    "basics": {"summary": "Backend engineer"},
    "work": [
        {
            "position": "Engineer",
            "name": "Acme",
            "startDate": "2018-01",
            "endDate": "",
            "highlights": ["Built Python services", "Ran Kubernetes clusters"],
        }
    ],
}


def _vec(text: str) -> list[float]:
    """Three deliberately orthogonal buckets so unrelated text never collides
    by accident: backend (python/kubernetes), frontend (react/figma/typescript),
    everything else."""
    lowered = text.lower()
    if "python" in lowered or "kubernetes" in lowered:
        return [1.0, 0.0, 0.0]
    if "react" in lowered or "figma" in lowered or "typescript" in lowered:
        return [0.0, 1.0, 0.0]
    return [0.0, 0.0, 1.0]


def _mock_embed(texts, **kwargs):
    return [_vec(t) for t in texts]


class PostingChunkRequirementsTests(TempDBTestCase):
    def test_job_key_and_ids_are_stable_and_disjoint_from_requirement_ids(self):
        job = {"catalog_job_id": 42, "description_full": POSTING}
        job_key, reqs = posting_chunk_requirements(job)
        self.assertEqual(job_key, "pf:42")
        self.assertEqual([r.id for r in reqs], ["pc0", "pc1", "pc2"])
        # "pf:" keeps this cache namespace disjoint from grounded-match
        # requirement ids, which key off the bare catalog job id ("42:r1").
        self.assertNotIn(":r1", job_key)

    def test_falls_back_to_id_when_catalog_job_id_missing(self):
        job = {"id": 7, "description_full": POSTING}
        job_key, _ = posting_chunk_requirements(job)
        self.assertEqual(job_key, "pf:7")

    def test_empty_description_yields_no_chunks(self):
        job_key, reqs = posting_chunk_requirements({"catalog_job_id": 1, "description_full": ""})
        self.assertEqual(reqs, [])


class PrefilterBatchTests(TempDBTestCase):
    def setUp(self):
        super().setUp()
        from src.db import create_user, save_profile
        from src.tenant import set_tenant_user_id

        self.uid = create_user("prefilter@test.com", display_name="P")["id"]
        set_tenant_user_id(self.uid)
        save_profile(RESUME, "", user_id=self.uid)

    def test_retriever_is_built_once_and_reused_across_calls(self):
        with mock.patch("src.matching.vectors.embed_texts", side_effect=_mock_embed) as embed:
            batch = PrefilterBatch.build(self.uid)
            batch.retriever_for("general")
            calls_after_first = embed.call_count
            batch.retriever_for("general")
        self.assertEqual(embed.call_count, calls_after_first, "second call must reuse the built retriever")

    def test_niche_and_general_variants_are_cached_separately(self):
        with mock.patch("src.matching.vectors.embed_texts", side_effect=_mock_embed):
            batch = PrefilterBatch.build(self.uid)
            general = batch.retriever_for("general")
            niche = batch.retriever_for("niche")
        self.assertIsNotNone(general)
        self.assertIsNotNone(niche)


class PrefilterJobTests(TempDBTestCase):
    def setUp(self):
        super().setUp()
        from src.db import create_user, save_profile, update_job, upsert_job
        from src.catalog_db import update_catalog_job
        from src.db import get_job
        from src.tenant import set_tenant_user_id

        self.uid = create_user("prefilter2@test.com", display_name="P2")["id"]
        set_tenant_user_id(self.uid)
        save_profile(RESUME, "", user_id=self.uid)

        self.user_job_id = upsert_job(
            {
                "title": "Backend Engineer",
                "company": "Acme",
                "url": "https://example.com/jobs/pf-1",
                "description": "short",
            },
            source_id="test",
            user_id=self.uid,
        )
        catalog_job_id = get_job(self.user_job_id, user_id=self.uid)["catalog_job_id"]
        update_catalog_job(catalog_job_id, description_full=POSTING)

    def test_prefilter_job_computes_a_coverage_style_score(self):
        with mock.patch("src.matching.vectors.embed_texts", side_effect=_mock_embed):
            score = prefilter_job = __import__("src.prefilter", fromlist=["prefilter_job"]).prefilter_job(
                self.user_job_id, user_id=self.uid
            )
        self.assertIsNotNone(score)
        self.assertGreater(score, 0.5)

    def test_borderline_score_is_also_skipped_not_left_stuck_in_new(self):
        """A score above the floor but below the LLM threshold used to leave
        the job as "new" forever: get_jobs_by_status excludes anything
        below vector_llm_min from every future match batch, so nothing
        would ever revisit it — it just piled up, unresolved, at the front
        of the "new" queue. It needs a terminal status, like the
        below-vector_min case already gets."""
        from src.catalog_db import update_catalog_job
        from src.db import get_job, upsert_job
        from src.prefilter import prefilter_job

        # One chunk overlaps the resume's Python/Kubernetes experience
        # (similarity 1.0), the other (frontend) doesn't (similarity 0.0)
        # — a clean, predictable 0.5 average under the mocked embeddings.
        half_overlap_job_id = upsert_job(
            {
                "title": "Backend Engineer",
                "company": "Acme",
                "url": "https://example.com/jobs/pf-half-overlap",
                "description": "short",
            },
            source_id="test",
            user_id=self.uid,
        )
        catalog_job_id = get_job(half_overlap_job_id, user_id=self.uid)["catalog_job_id"]
        update_catalog_job(
            catalog_job_id,
            description_full="Requirements\n- 5+ years of Python experience\n\nNice to have\n- React and Figma design skills\n",
        )

        cfg = {"vector_prefilter_enabled": True, "vector_min_score": 0.3, "vector_llm_min_score": 0.75}
        with mock.patch("src.matching.vectors.embed_texts", side_effect=_mock_embed), mock.patch(
            "src.matching.prefilter._matching_cfg", return_value=cfg
        ):
            score = prefilter_job(half_overlap_job_id, user_id=self.uid)
        job = get_job(half_overlap_job_id, user_id=self.uid)
        self.assertAlmostEqual(score, 0.5)
        self.assertEqual(job["status"], "skipped")
        self.assertIn("LLM threshold", job["match_summary"])

    def test_low_score_marks_the_job_skipped(self):
        """A posting with no vocabulary overlap at all against the resume."""
        from src.catalog_db import update_catalog_job
        from src.db import get_job, upsert_job
        from src.prefilter import prefilter_job

        no_overlap_job_id = upsert_job(
            {
                "title": "Frontend Engineer",
                "company": "Acme",
                "url": "https://example.com/jobs/pf-no-overlap",
                "description": "short",
            },
            source_id="test",
            user_id=self.uid,
        )
        catalog_job_id = get_job(no_overlap_job_id, user_id=self.uid)["catalog_job_id"]
        update_catalog_job(
            catalog_job_id,
            description_full="Requirements\n- Figma design handoff\n- React and TypeScript UI work\n",
        )

        cfg = {"vector_prefilter_enabled": True, "vector_min_score": 0.9, "vector_llm_min_score": 0.95}
        with mock.patch("src.matching.vectors.embed_texts", side_effect=_mock_embed), mock.patch(
            "src.matching.prefilter._matching_cfg", return_value=cfg
        ):
            score = prefilter_job(no_overlap_job_id, user_id=self.uid)
        job = get_job(no_overlap_job_id, user_id=self.uid)
        self.assertLess(score, 0.9)
        self.assertEqual(job["status"], "skipped")
        self.assertIn("vector score", job["match_summary"])

    def test_disabled_prefilter_returns_none(self):
        from src.prefilter import prefilter_job

        with mock.patch("src.matching.prefilter._matching_cfg", return_value={"vector_prefilter_enabled": False}):
            self.assertIsNone(prefilter_job(self.user_job_id, user_id=self.uid))

    def test_missing_job_returns_none(self):
        from src.prefilter import prefilter_job

        self.assertIsNone(prefilter_job(999999, user_id=self.uid))


class EmbedCatalogJobsTests(TempDBTestCase):
    def setUp(self):
        super().setUp()
        from src.db import create_user, upsert_job
        from src.catalog_db import update_catalog_job
        from src.db import get_job
        from src.tenant import set_tenant_user_id

        self.uid = create_user("embedstage@test.com", display_name="E")["id"]
        set_tenant_user_id(self.uid)
        self.user_job_id = upsert_job(
            {
                "title": "Backend Engineer",
                "company": "Acme",
                "url": "https://example.com/jobs/embed-1",
                "description": "short",
            },
            source_id="test",
            user_id=self.uid,
        )
        self.catalog_job_id = get_job(self.user_job_id, user_id=self.uid)["catalog_job_id"]
        update_catalog_job(self.catalog_job_id, description_full=POSTING)

    def test_embed_stage_warms_the_cache_the_prefilter_later_reads(self):
        from src.embeddings import embed_catalog_jobs
        from src.matching.vectors import OWNER_REQUIREMENT, OWNER_ROLES, get_cached
        from src.matching.chunking import text_hash

        with mock.patch("src.matching.vectors.embed_texts", side_effect=_mock_embed) as embed:
            count = embed_catalog_jobs(limit=10)
        self.assertEqual(count, 1)

        job_key, reqs = posting_chunk_requirements(
            {"catalog_job_id": self.catalog_job_id, "description_full": POSTING}
        )
        # embedding_settings resolves a model name from the (unmocked) real
        # config store; read whatever model the embed stage actually used.
        from src.matching.vectors import embedding_settings

        _, model = embedding_settings(self.uid)
        role = OWNER_ROLES[OWNER_REQUIREMENT]
        cached = get_cached(
            OWNER_REQUIREMENT,
            [(f"{job_key}:{r.id}", f"{role}:{text_hash(r.text)}") for r in reqs],
            model,
        )
        self.assertEqual(len(cached), len(reqs), "embed stage must warm every posting-chunk vector")

        # A second run with the same content must not re-embed.
        with mock.patch("src.matching.vectors.embed_texts", side_effect=_mock_embed) as embed2:
            embed_catalog_jobs(limit=10)
        embed2.assert_not_called()

    def test_enrichment_changing_the_description_triggers_re_embedding(self):
        from src.embeddings import embed_catalog_jobs
        from src.catalog_db import update_catalog_job

        with mock.patch("src.matching.vectors.embed_texts", side_effect=_mock_embed):
            embed_catalog_jobs(limit=10)

        update_catalog_job(
            self.catalog_job_id,
            description_full=POSTING + "\n- Terraform required\n",
        )
        with mock.patch("src.matching.vectors.embed_texts", side_effect=_mock_embed) as embed:
            embed_catalog_jobs(limit=10)
        embed.assert_called_once()

    def test_reset_embeddings_purges_the_vector_cache_and_content_hash(self):
        from src.embeddings import embed_catalog_jobs, reset_embeddings
        from src.db import connect

        with mock.patch("src.matching.vectors.embed_texts", side_effect=_mock_embed):
            embed_catalog_jobs(limit=10)

        result = reset_embeddings()
        self.assertGreater(result["vectors_purged"], 0)
        self.assertEqual(result["catalog_jobs"], 1)

        with connect() as conn:
            row = conn.execute(
                "SELECT embed_content_hash FROM catalog_jobs WHERE id = ?", (self.catalog_job_id,)
            ).fetchone()
        self.assertIsNone(row["embed_content_hash"])


class VectorDimensionDriftTests(TempDBTestCase):
    """An embedding model re-pulled under the same name with a different
    output dimension must never yield a ragged mix of old- and new-dim
    vectors, which numpy rejects and which used to abort a prefilter run."""

    OWNER = "resume_chunk"

    def _items(self, n: int):
        return [(f"u1:chunk-{i}", f"hash-{i}", f"text {i}") for i in range(n)]

    def test_mixed_dim_cache_is_discarded_rather_than_served(self):
        from src.matching.vectors import get_cached, put_many

        put_many(self.OWNER, [("u1:a", "document:h", [1.0] * 4)], "m", user_id=1)
        put_many(self.OWNER, [("u1:b", "document:h", [1.0] * 8)], "m", user_id=1)

        cached = get_cached(
            self.OWNER, [("u1:a", "document:h"), ("u1:b", "document:h")], "m", user_id=1
        )
        self.assertEqual(cached, {}, "a dim disagreement must invalidate the whole batch")

    def test_embed_with_cache_never_returns_mixed_dimensions(self):
        from src.matching.vectors import embed_with_cache, put_many

        items = self._items(3)
        # Two rows cached at the old dim, one absent so the live model runs.
        for oid, h, _ in items[:2]:
            put_many(self.OWNER, [(oid, f"document:{h}", [1.0] * 4)], "m", user_id=1)

        def fake_embed(texts, **kwargs):
            return [[0.5] * 8 for _ in texts]  # model now returns 8 dims

        with mock.patch("src.matching.vectors.embed_texts", side_effect=fake_embed):
            out = embed_with_cache(self.OWNER, items, user_id=1, model="m")

        self.assertEqual(
            {len(v) for v in out.values()},
            {8},
            "stale-dim cached vectors must be dropped, not merged with fresh ones",
        )

    def test_cache_converges_on_the_new_dimension_next_run(self):
        from src.matching.vectors import embed_with_cache, put_many

        items = self._items(3)
        for oid, h, _ in items[:2]:
            put_many(self.OWNER, [(oid, f"document:{h}", [1.0] * 4)], "m", user_id=1)

        def fake_embed(texts, **kwargs):
            return [[0.5] * 8 for _ in texts]

        with mock.patch("src.matching.vectors.embed_texts", side_effect=fake_embed):
            embed_with_cache(self.OWNER, items, user_id=1, model="m")
            second = embed_with_cache(self.OWNER, items, user_id=1, model="m")

        self.assertEqual(len(second), len(items), "every item should be usable on the second run")
        self.assertEqual({len(v) for v in second.values()}, {8})
