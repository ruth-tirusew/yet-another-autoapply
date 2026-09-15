#!/usr/bin/env python3
"""CLI for job application automation pipeline."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description="Job application automation")
    sub = parser.add_subparsers(dest="command")

    up = sub.add_parser("upload-cv", help="Upload PDF CV to build profile")
    up.add_argument("pdf_path", help="Path to resume PDF")

    sub.add_parser("evaluate-profile", help="Re-run hiring_agent profile evaluation")

    profile_p = sub.add_parser("profile", help="Profile management")
    profile_sub = profile_p.add_subparsers(dest="profile_cmd")
    gen_p = profile_sub.add_parser("generalize", help="Build generalized resume variant + niche terms")
    gen_p.add_argument("--force", action="store_true", help="Regenerate even if cache is valid")

    sub.add_parser("crawl", help="Crawl all enabled sources")
    sub.add_parser("enrich", help="Fetch full job descriptions (catalog)")
    sub.add_parser("sync", help="Sync catalog jobs to your queue")
    sub.add_parser("prefilter", help="Vector pre-filter before LLM match")

    emb = sub.add_parser("embeddings", help="Embedding management")
    emb_sub = emb.add_subparsers(dest="embeddings_cmd")
    emb_sub.add_parser(
        "reset",
        help="Clear profile/catalog embeddings and user vector scores (run after switching models)",
    )

    recheck_p = sub.add_parser("recheck-eligibility", help="Skip ineligible jobs by location")
    recheck_p.add_argument("--limit", type=int, default=2000, help="Max jobs to check per user")
    match_p = sub.add_parser("match", help="Score new jobs against profile (new → scored/queued)")
    match_p.add_argument("--limit", type=int, default=None, help="Jobs per batch (default: match_limit from settings)")
    match_p.add_argument("--all", action="store_true", help="Keep scoring batches until no new jobs remain")
    sub.add_parser("generate", help="Generate tailored CVs and cover letters for queued jobs")

    disc = sub.add_parser("discover", help="List job boards from awesome-job-boards")
    disc.add_argument("--limit", type=int, default=30)

    sub.add_parser("sources", help="List enabled sources from database")

    cfg = sub.add_parser("config", help="Configuration management")
    cfg_sub = cfg.add_subparsers(dest="config_cmd")
    cfg_sub.add_parser("import-yaml", help="Import config.yaml and sources.yaml into database")

    pipe = sub.add_parser("pipeline", help="Run full pipeline")
    pipe.add_argument("action", choices=["run"], nargs="?", default="run")

    review = sub.add_parser("review", help="Review queue commands")
    review_sub = review.add_subparsers(dest="review_cmd")
    review_sub.add_parser("list", help="List queued jobs")
    review_sub.add_parser("serve", help="Start review web UI")
    approve = review_sub.add_parser("approve", help="Approve a job")
    approve.add_argument("job_id", type=int)
    reject = review_sub.add_parser("reject", help="Reject a job")
    reject.add_argument("job_id", type=int)
    skip = review_sub.add_parser("skip", help="Skip a job")
    skip.add_argument("job_id", type=int)

    apply_p = sub.add_parser("apply", help="Apply to a job")
    apply_p.add_argument("job_id", type=int)
    apply_p.add_argument("--force", action="store_true")
    apply_p.add_argument(
        "--headed",
        action="store_true",
        help="Show the browser window (overrides Settings → headless for this run)",
    )

    demo_p = sub.add_parser(
        "demo-greenhouse",
        help="Fill a Greenhouse form in a visible browser without submitting (for testing)",
    )
    demo_p.add_argument("job_id", type=int)
    demo_p.add_argument("--wait", type=int, default=120, help="Seconds to keep browser open (default: 120)")
    demo_p.add_argument("--slow-mo", type=int, default=350, help="Delay between actions in ms (default: 350)")

    retry_p = sub.add_parser("retry", help="Retry failed applications")
    retry_p.add_argument("job_id", type=int, nargs="?", help="Job ID to retry")
    retry_p.add_argument("--all", action="store_true", help="Retry all failed applications")

    verify_p = sub.add_parser("verify", help="Submit an email verification code for a job awaiting OTP")
    verify_p.add_argument("job_id", type=int)
    verify_p.add_argument("code", help="Verification code from the email")

    fetch_otp_p = sub.add_parser(
        "fetch-otp", help="Poll IMAP for a Greenhouse verification code and complete the application"
    )
    fetch_otp_p.add_argument("job_id", type=int)

    sub.add_parser("export", help="Export jobs to Excel")

    coaching = sub.add_parser("coaching", help="Skills gap coaching")
    coaching_sub = coaching.add_subparsers(dest="coaching_cmd")
    coaching_sub.add_parser("report", help="Print skills gap report")
    coaching_sub.add_parser("profile-guide", help="Print cached profile improvement guide")

    args = parser.parse_args()

    from src.db import init_db

    init_db()

    from src.db import get_user_by_id
    from src.tenant import set_tenant_user_id

    if get_user_by_id(1):
        set_tenant_user_id(1)

    if args.command == "upload-cv":
        from src.profile import upload_cv

        result = upload_cv(args.pdf_path)
        for warning in result.get("warnings") or []:
            print(f"[profile] Warning: {warning}")

    elif args.command == "evaluate-profile":
        from src.hiring_agent_bridge import HiringAgentError, evaluation_total_score, format_evaluation_for_match
        from src.profile import evaluate_stored_profile

        try:
            ev = evaluate_stored_profile()
        except HiringAgentError as e:
            print(f"Evaluation failed: {e}")
            sys.exit(1)
        print(f"Profile score: {evaluation_total_score(ev)}/100")
        print(format_evaluation_for_match(ev))

    elif args.command == "profile":
        if args.profile_cmd == "generalize":
            from src.coaching.generalize_profile import build_general_resume, generalize_status
            from src.embeddings import save_profile_embeddings

            status = generalize_status()
            if not status.get("cache_key"):
                print("No profile loaded. Run: python cli.py upload-cv <pdf>")
                sys.exit(1)
            result = build_general_resume(force=args.force)
            print(f"Generalized resume ready ({len(result.niche_terms)} niche terms)")
            for term in result.niche_terms[:12]:
                print(f"  • {term}")
            if len(result.niche_terms) > 12:
                print(f"  … and {len(result.niche_terms) - 12} more")
            if save_profile_embeddings():
                print("Profile embeddings updated (general + niche)")
            else:
                print("Profile embeddings not updated (check Ollama embed model)")
        else:
            profile_p.print_help()

    elif args.command == "crawl":
        from src.crawler.orchestrator import crawl_all

        crawl_all()

    elif args.command == "enrich":
        from src.enrich import enrich_jobs

        enrich_jobs()

    elif args.command == "sync":
        from src.catalog_sync import sync_user_jobs

        sync_user_jobs()

    elif args.command == "prefilter":
        from src.prefilter import prefilter_all

        prefilter_all()

    elif args.command == "embeddings":
        if args.embeddings_cmd == "reset":
            from src.embeddings import reset_embeddings

            result = reset_embeddings()
            print(
                "Cleared embeddings: "
                f"{result['profiles']} profile(s), "
                f"{result['catalog_jobs']} catalog job(s), "
                f"{result['vector_scores']} vector score(s), "
                f"{result['vectors_purged']} cached vector(s). "
                "Re-run embed + prefilter pipeline stages."
            )
        else:
            emb.print_help()

    elif args.command == "recheck-eligibility":
        from src.eligibility_backfill import recheck_eligibility

        result = recheck_eligibility(limit=args.limit)
        print(
            f"Checked {result['checked']} jobs: "
            f"{result['skipped']} skipped, {result['unchanged']} unchanged"
        )

    elif args.command == "match":
        from src.matcher import match_new_jobs

        result = match_new_jobs(limit=args.limit, all_remaining=args.all)
        print(
            f"Done: {result['processed']} scored/queued "
            f"({result['scored']} scored, {result['queued']} queued) "
            f"across {result['batches']} batch(es)"
        )

    elif args.command == "generate":
        from src.ats import parse_match_details, should_queue
        from src.config import get_config
        from src.cover_letter import CoverLetterSkipped, generate_cover_letter
        from src.db import get_jobs_by_statuses
        from src.tailor import TailorSkipped, tailor_cv

        cfg = get_config()
        pipeline = cfg.get("pipeline", {})
        threshold = cfg["match_threshold"]
        role_fit_min = int(pipeline.get("role_fit_min", 15))
        maybe_boost = int(pipeline.get("maybe_score_boost", 10))

        for job in get_jobs_by_statuses(["queued"], limit=50):
            match = parse_match_details(job)
            if match:
                ok, reason = should_queue(
                    match, threshold, role_fit_min=role_fit_min, maybe_score_boost=maybe_boost
                )
                if not ok:
                    print(f"  Skip job {job['id']}: {reason}")
                    continue
            try:
                tailor_cv(job["id"], force=True)
                generate_cover_letter(job["id"], force=True)
                print(f"  Generated for job {job['id']}")
            except CoverLetterSkipped as e:
                print(f"  Skip job {job['id']}: {e}")
            except TailorSkipped as e:
                print(f"  Skip job {job['id']}: {e}")
            except Exception as e:
                print(f"  Failed job {job['id']}: {e}")

    elif args.command == "discover":
        from src.source_discovery import fetch_awesome_list

        for item in fetch_awesome_list()[: args.limit]:
            print(
                f"{item.get('adapter', '?'):15s}  "
                f"{item.get('name', '')[:40]:40s}  "
                f"{item.get('seed_url', '')}"
            )

    elif args.command == "sources":
        from src.settings import reload_sources

        for s in reload_sources():
            print(f"  [{s.get('adapter', '?'):12s}] {s.get('id')} — {s.get('name')}")

    elif args.command == "config":
        if args.config_cmd == "import-yaml":
            from src.yaml_import import import_yaml_to_db

            summary = import_yaml_to_db(rename_after=True)
            print(f"Imported {summary['collections']} collections, {summary['sources']} sources.")
            if summary.get("users_seeded"):
                print(f"Seeded sources for {summary['users_seeded']} users.")
        else:
            cfg.print_help()

    elif args.command == "pipeline":
        from src.pipeline import run_pipeline

        run_pipeline()

    elif args.command == "review":
        if args.review_cmd == "list":
            from src.db import get_jobs_by_statuses

            for j in get_jobs_by_statuses(["queued"], limit=50):
                score = j.get("match_score") or "—"
                print(f"{j['id']:4d}  {score:>3}  {j.get('title', '')[:50]}  @ {j.get('company', '')}")
        elif args.review_cmd == "serve":
            import uvicorn

            uvicorn.run("src.web.app:app", host="127.0.0.1", port=8088, reload=False)
        elif args.review_cmd == "approve":
            from src.apply.dispatcher import apply_to_job
            from src.db import set_job_status
            from src.settings import get_config

            set_job_status(args.job_id, "approved")
            if get_config().get("auto_apply"):
                r = apply_to_job(args.job_id, force=True)
                print(r["message"])
            else:
                print(f"Job {args.job_id} approved")
        elif args.review_cmd == "reject":
            from src.db import set_job_status

            set_job_status(args.job_id, "rejected")
            print(f"Job {args.job_id} rejected")
        elif args.review_cmd == "skip":
            from src.db import set_job_status

            set_job_status(args.job_id, "skipped")
            print(f"Job {args.job_id} skipped")
        else:
            review.print_help()

    elif args.command == "apply":
        from src.apply.dispatcher import apply_to_job

        if args.headed:
            os.environ["AUTO_APPLY_HEADED"] = "1"
        r = apply_to_job(args.job_id, force=args.force)
        print(f"{r['method']}: {r['message']}")

    elif args.command == "demo-greenhouse":
        import time
        from pathlib import Path

        from playwright.sync_api import sync_playwright

        from src.apply.base import detect_adapter, resolve_greenhouse_apply_url
        from src.apply.greenhouse import _fill_greenhouse_form
        from src.db import get_application, get_job
        from src.settings import applications_dir, get_config

        job = get_job(args.job_id)
        if not job:
            print(f"Job {args.job_id} not found")
            sys.exit(1)
        if detect_adapter(job.get("url", "")) != "greenhouse":
            print("demo-greenhouse only works for Greenhouse job URLs")
            sys.exit(1)
        app = get_application(args.job_id)
        if not app or not app.get("tailored_cv_path") or not Path(app["tailored_cv_path"]).exists():
            print("No tailored CV — run generate first")
            sys.exit(1)
        url = resolve_greenhouse_apply_url(job.get("url", ""))
        applicant = get_config().get("applicant", {})
        cover_path = app.get("cover_letter_path", "")
        cover = Path(cover_path).read_text(encoding="utf-8") if cover_path and Path(cover_path).exists() else ""
        out_dir = applications_dir() / str(args.job_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        print(f"Opening visible browser: {url}")
        print(f"Form will fill slowly; browser stays open {args.wait}s. Submit is NOT clicked.")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False, slow_mo=args.slow_mo)
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(url, wait_until="networkidle", timeout=90000)
            page.wait_for_selector("#first_name", timeout=30000)
            page.wait_for_function("() => !!window.__remixContext?.state?.loaderData", timeout=30000)
            time.sleep(1)
            _fill_greenhouse_form(page, applicant, cover, app["tailored_cv_path"], out_dir)
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            print("Form filled — review the browser window.")
            time.sleep(args.wait)
            shot = out_dir / "headed_demo.png"
            page.screenshot(path=str(shot), full_page=True)
            browser.close()
        print(f"Screenshot saved: {shot}")

    elif args.command == "retry":
        from src.apply.dispatcher import retry_application, retry_failed_applications

        if args.all or args.job_id is None:
            summary = retry_failed_applications(force=True)
            print(
                f"Retried {summary['total']}: {summary['succeeded']} succeeded, "
                f"{summary['failed']} still failed"
            )
            for result in summary["results"]:
                status = "OK" if result.get("success") else "FAIL"
                print(f"  [{status}] job {result['job_id']}: {result.get('message', '')}")
        else:
            result = retry_application(args.job_id, force=True)
            status = "OK" if result.get("success") else "FAIL"
            print(f"[{status}] job {result['job_id']}: {result.get('message', '')}")

    elif args.command == "verify":
        from src.apply.dispatcher import complete_verification

        result = complete_verification(args.job_id, args.code)
        status = "OK" if result.get("success") else "FAIL"
        print(f"[{status}] job {args.job_id}: {result.get('message', '')}")

    elif args.command == "fetch-otp":
        from src.apply.dispatcher import try_auto_verify

        result = try_auto_verify(args.job_id)
        status = "OK" if result.get("success") else "FAIL"
        print(f"[{status}] job {args.job_id}: {result.get('message', '')}")

    elif args.command == "export":
        from src.crawler.export import export_excel

        export_excel()

    elif args.command == "coaching":
        if args.coaching_cmd == "report":
            from src.coaching.skills_gap import build_gap_report

            report = build_gap_report(use_llm=False)
            print(report.summary)
            for gap, count in report.gap_frequency.items():
                print(f"  {gap}: {count}")
            for path in report.learning_paths:
                print(f"  → {path}")
        elif args.coaching_cmd == "profile-guide":
            from src.coaching.profile_guide import build_profile_guide, guide_cache_status

            status = guide_cache_status()
            if not status["profile_loaded"]:
                print("No profile loaded. Run: python cli.py upload-cv <pdf>")
                sys.exit(1)
            report = status["guide"] or build_profile_guide(use_llm=True)
            print(report.summary)
            print("\nCV tips:")
            for tip in report.cv_tips:
                print(f"  • {tip.title}: {tip.detail}")
            print("\nGitHub tips:")
            for tip in report.github_tips:
                print(f"  • {tip.title}: {tip.detail}")
            print("\nOSS projects:")
            for proj in report.oss_projects:
                print(f"  • {proj.name} ({proj.url})")
                print(f"    {proj.why}")
        else:
            coaching.print_help()

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
