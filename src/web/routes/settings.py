"""Settings routes."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from src.config_store import get_all_collections
from src.keywords import INCLUDE_GROUPS, build_search_query
from src.llm.catalog import PROVIDER_MODELS
from src.settings import get_config
from src.tenant import get_tenant_user_id
from src.user_config import load_user_config_raw
from src.web.deps import SETTINGS_TABS, render
from src.web.services.config_service import (
    get_full_config,
    save_applicant_config,
    save_auto_apply_config,
    save_cover_letter_config,
    save_hiring_agent_config,
    save_integrations_config,
    save_keywords_config,
    save_models_config,
    save_pipeline_config,
)
from src.web.services.llm_health import list_models, test_model

router = APIRouter()


def _raw_for_panel() -> dict:
    if get_tenant_user_id() is not None:
        return load_user_config_raw()
    return get_all_collections(None)


def _panel_context(tab: str, request: Request | None = None) -> dict:
    from src.db import list_oauth_accounts
    from src.web.auth.oauth import oauth_enabled

    cfg = get_config()
    raw = _raw_for_panel()
    full = get_full_config()
    ctx: dict = {}
    if tab == "account":
        from src.web.auth.deps import get_current_user

        user = get_current_user(request) if request else None
        oauth_accounts = list_oauth_accounts(user["id"]) if user else []
        providers = {a["provider"] for a in oauth_accounts}
        ctx["oauth_accounts"] = oauth_accounts
        ctx["oauth_providers"] = {"github": "github" in providers, "google": "google" in providers}
        ctx["github_enabled"] = oauth_enabled("github")
        ctx["google_enabled"] = oauth_enabled("google")
    elif tab == "models":
        ctx["models"] = full["models"]
        provider = full["models"].get("provider", "ollama")
        ctx["available_models"] = list_models()
        if not ctx["available_models"] and provider in PROVIDER_MODELS:
            ctx["available_models"] = PROVIDER_MODELS[provider]
    elif tab == "pipeline":
        ctx["pipeline"] = raw.get("pipeline", {})
    elif tab == "hiring_agent":
        ctx["hiring_agent"] = raw.get("hiring_agent", {"evaluate_on_upload": True})
    elif tab == "keywords":
        from src.keywords import get_keywords_config

        ctx["keywords"] = get_keywords_config()
        ctx["keyword_groups"] = INCLUDE_GROUPS
    elif tab == "auto_apply":
        from src.db import get_imap_credential_meta
        from src.tenant import get_tenant_user_id

        auto_raw = raw.get("auto_apply", {})
        otp_imap = auto_raw.get("otp_imap", {}) if isinstance(auto_raw, dict) else {}
        uid = get_tenant_user_id()
        imap_meta = get_imap_credential_meta(uid) if uid else get_imap_credential_meta(1)
        ctx["auto_apply"] = {
            "enabled": cfg["auto_apply"],
            "allowlist": cfg["auto_apply_sources"],
            "max_per_day": cfg["max_applications_per_day"],
            "headless": auto_raw.get("headless", True) if isinstance(auto_raw, dict) else True,
            "otp_auto_fetch": bool(auto_raw.get("otp_auto_fetch", False)) if isinstance(auto_raw, dict) else False,
            "otp_imap": {
                "host": otp_imap.get("host", "imap.gmail.com"),
                "port": int(otp_imap.get("port", 993)),
                "username": otp_imap.get("username", ""),
                "use_tls": bool(otp_imap.get("use_tls", True)),
                "poll_timeout_sec": int(otp_imap.get("poll_timeout_sec", 90)),
                "poll_interval_sec": int(otp_imap.get("poll_interval_sec", 5)),
            },
            "has_imap_password": bool(imap_meta),
            "imap_password_hint": (imap_meta or {}).get("key_hint", ""),
        }
    elif tab == "applicant":
        ctx["applicant"] = raw.get("applicant", {})
    elif tab == "cover_letter":
        ctx["cover_letter"] = raw.get("cover_letter", {})
    elif tab == "integrations":
        ctx["ever_jobs"] = raw.get("ever_jobs", {})
        ctx["scheduler"] = raw.get("scheduler", {})
        ctx["source_discovery"] = raw.get("source_discovery", {})
    return ctx


@router.get("/settings")
def settings_index(request: Request):
    return render(
        request,
        "settings/index.html",
        {"tabs": SETTINGS_TABS, "active_tab": "account"},
    )


@router.get("/settings/panel/{tab}")
def settings_panel(request: Request, tab: str):
    if tab not in SETTINGS_TABS:
        return HTMLResponse("Unknown tab", status_code=404)
    return render(request, f"settings/_{tab}.html", _panel_context(tab, request))


@router.post("/settings/save/models")
async def save_models(request: Request):
    form = await request.form()
    save_models_config(
        {
            "provider": form.get("provider", "ollama"),
            "ollama_host": form.get("ollama_host", ""),
            "default_model": form.get("default_model", ""),
            "quality_model": form.get("quality_model", ""),
            "embedding_model": form.get("embedding_model", "mxbai-embed-large"),
            "api_key": form.get("api_key", ""),
        }
    )
    return Response(headers={"HX-Trigger": "configSaved"})


@router.post("/settings/save/pipeline")
async def save_pipeline(request: Request):
    form = await request.form()
    save_pipeline_config(
        {
            "match_threshold": int(form.get("match_threshold", 70)),
            "role_fit_min": int(form.get("role_fit_min", 15)),
            "maybe_score_boost": int(form.get("maybe_score_boost", 10)),
            "stale_days": int(form.get("stale_days", 30)),
            "enrich_delay_seconds": float(form.get("enrich_delay_seconds", 1.0)),
            "crawl_delay_seconds": float(form.get("crawl_delay_seconds", 1.5)),
            "enrich_limit": int(form.get("enrich_limit", 25)),
            "match_limit": int(form.get("match_limit", 10)),
            "generate_limit": int(form.get("generate_limit", 5)),
        }
    )
    return Response(headers={"HX-Trigger": "configSaved"})


@router.post("/settings/save/hiring_agent")
async def save_hiring_agent(request: Request):
    form = await request.form()
    save_hiring_agent_config(
        {"evaluate_on_upload": form.get("evaluate_on_upload") == "true"}
    )
    return Response(headers={"HX-Trigger": "configSaved"})


@router.post("/settings/save/keywords")
async def save_keywords(request: Request):
    form = await request.form()

    def split_field(name: str) -> list[str]:
        raw = form.get(name, "")
        return [k.strip() for k in str(raw).split(",") if k.strip()]

    primary = form.getlist("primary_groups") if hasattr(form, "getlist") else []
    if not primary:
        primary = [form.get("primary_groups")] if form.get("primary_groups") else []

    include: dict[str, list[str]] = {}
    for g in INCLUDE_GROUPS:
        include[g] = split_field(f"include_{g}")

    save_keywords_config(
        {
            "primary_groups": [p for p in primary if p],
            "include": include,
            "exclude": split_field("exclude"),
        }
    )
    return Response(headers={"HX-Trigger": "configSaved"})


@router.post("/settings/save/auto_apply")
async def save_auto_apply(request: Request):
    form = await request.form()
    allowlist = form.getlist("allowlist") if hasattr(form, "getlist") else []
    if not allowlist:
        allowlist = [form.get("allowlist")] if form.get("allowlist") else []
    existing = _raw_for_panel().get("auto_apply", {})
    if not isinstance(existing, dict):
        existing = {}
    existing_imap = existing.get("otp_imap", {}) if isinstance(existing.get("otp_imap"), dict) else {}
    save_auto_apply_config(
        {
            "enabled": form.get("enabled") == "true",
            "allowlist": [a for a in allowlist if a],
            "max_per_day": int(form.get("max_per_day", 5)),
            "headless": form.get("headless") == "true",
            "otp_auto_fetch": form.get("otp_auto_fetch") == "true",
            "otp_imap": {
                **existing_imap,
                "host": form.get("imap_host", existing_imap.get("host", "imap.gmail.com")),
                "port": int(form.get("imap_port", existing_imap.get("port", 993))),
                "username": form.get("imap_username", existing_imap.get("username", "")),
                "use_tls": form.get("imap_use_tls") == "true",
                "poll_timeout_sec": int(form.get("imap_poll_timeout_sec", existing_imap.get("poll_timeout_sec", 90))),
                "poll_interval_sec": int(form.get("imap_poll_interval_sec", existing_imap.get("poll_interval_sec", 5))),
            },
        }
    )
    imap_password = str(form.get("imap_password", "")).strip()
    if imap_password:
        from src.db import save_imap_credential
        from src.tenant import get_tenant_user_id

        uid = get_tenant_user_id() or 1
        save_imap_credential(uid, imap_password)
    return Response(headers={"HX-Trigger": "configSaved"})


@router.post("/settings/test-imap")
async def test_imap_endpoint(request: Request):
    from src.apply.otp_mail import test_imap_connection
    from src.db import get_imap_password, save_imap_credential
    from src.tenant import get_tenant_user_id

    form = await request.form()
    uid = get_tenant_user_id() or 1
    password = str(form.get("imap_password", "")).strip() or get_imap_password(uid)
    if str(form.get("imap_password", "")).strip():
        save_imap_credential(uid, str(form.get("imap_password", "")).strip())
    cfg = get_config()
    applicant = cfg.get("applicant", {})
    imap_config = {
        "host": form.get("imap_host", "imap.gmail.com"),
        "port": int(form.get("imap_port", 993)),
        "username": form.get("imap_username") or applicant.get("email", ""),
        "password": password or "",
        "use_tls": form.get("imap_use_tls") == "true",
    }
    if not imap_config["username"] or not imap_config["password"]:
        return render(
            request,
            "partials/imap_test_result.html",
            {"result": {"ok": False, "message": "IMAP username and password are required"}},
        )
    ok, message = test_imap_connection(imap_config)
    return render(request, "partials/imap_test_result.html", {"result": {"ok": ok, "message": message}})


@router.post("/settings/save/applicant")
async def save_applicant(request: Request):
    form = await request.form()
    save_applicant_config({k: form.get(k, "") for k in [
        "name", "email", "phone", "linkedin", "github", "portfolio",
        "location", "work_authorization", "salary_expectation",
    ]})
    return Response(headers={"HX-Trigger": "configSaved"})


@router.post("/settings/save/cover_letter")
async def save_cover_letter_settings(request: Request):
    form = await request.form()
    save_cover_letter_config(
        {
            "max_words": int(form.get("max_words", 350)),
            "tone": form.get("tone", "concise"),
        }
    )
    return Response(headers={"HX-Trigger": "configSaved"})


@router.get("/settings/preview/search-query")
async def preview_search_query(request: Request):
    mode = request.query_params.get("ever_jobs_search_mode", "auto")
    manual = request.query_params.get("ever_jobs_search_term", "")
    if mode == "manual":
        query = manual.strip()
    else:
        raw = _raw_for_panel().get("ever_jobs", {})
        groups = raw.get("search_groups") or ["stack", "roles", "frameworks"]
        query = build_search_query(groups)
    return render(
        request,
        "partials/search_query_preview.html",
        {"preview_query": query, "preview_mode": mode},
    )


@router.post("/settings/save/integrations")
async def save_integrations(request: Request):
    form = await request.form()
    raw_ej = _raw_for_panel().get("ever_jobs", {})
    raw_sd = _raw_for_panel().get("source_discovery", {})
    save_integrations_config(
        {
            "ever_jobs": {
                "enabled": form.get("ever_jobs_enabled") == "true",
                "api_url": form.get("ever_jobs_api_url", ""),
                "search_mode": form.get("ever_jobs_search_mode", "auto"),
                "search_term": form.get("ever_jobs_search_term", ""),
                "search_groups": raw_ej.get("search_groups", ["stack", "roles", "frameworks"]),
                "is_remote": True,
                "results_wanted": int(form.get("ever_jobs_results_wanted", 30)),
                "site_type": raw_ej.get("site_type", []),
            },
            "scheduler": {
                "enabled": form.get("scheduler_enabled") == "true",
                "crawl_interval_hours": int(form.get("crawl_interval_hours", 8)),
            },
            "source_discovery": {
                "awesome_job_boards_url": raw_sd.get("awesome_job_boards_url", ""),
                "auto_enable_discovered": form.get("auto_enable_discovered") == "true",
                "max_discovered": int(form.get("max_discovered", 40)),
            },
        }
    )
    return Response(headers={"HX-Trigger": "configSaved"})


@router.post("/settings/test-model")
async def test_model_endpoint(request: Request):
    form = await request.form()
    provider = form.get("provider", "ollama")
    api_key = form.get("api_key", "")
    if provider != "ollama" and api_key.strip():
        from src.db import save_llm_credential
        from src.tenant import get_tenant_user_id

        uid = get_tenant_user_id() or 1
        save_llm_credential(uid, provider, api_key.strip())
    result = test_model(
        model=form.get("default_model"),
        host=form.get("ollama_host"),
    )
    return render(request, "partials/model_test_result.html", {"result": result})
