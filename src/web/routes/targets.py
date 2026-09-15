"""Target company list management."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from src.catalog_db import add_target_company, delete_target_company, list_target_companies, toggle_target_company
from src.crawler.ats_detect import detect_ats_type
from src.web.auth.deps import require_user
from src.web.deps import render

router = APIRouter()


@router.get("/targets")
def targets_page(request: Request, user: dict = Depends(require_user)):
    companies = list_target_companies(user["id"])
    return render(request, "targets.html", {"companies": companies})


@router.post("/targets/add")
async def targets_add(
    request: Request,
    user: dict = Depends(require_user),
    company_slug: str = Form(...),
    ats_type: str = Form(""),
    tier: int = Form(2),
    display_name: str = Form(""),
    careers_url: str = Form(""),
):
    slug = company_slug.strip().lower()
    ats = ats_type.strip().lower() or detect_ats_type(careers_url) or "greenhouse"
    if slug:
        add_target_company(
            user["id"],
            slug,
            ats,
            tier=max(1, min(3, tier)),
            display_name=display_name.strip(),
            careers_url=careers_url.strip(),
        )
    return RedirectResponse("/targets", status_code=303)


@router.post("/targets/{company_id}/delete")
def targets_delete(company_id: int, user: dict = Depends(require_user)):
    delete_target_company(company_id, user["id"])
    return RedirectResponse("/targets", status_code=303)


@router.post("/targets/{company_id}/toggle")
def targets_toggle(company_id: int, user: dict = Depends(require_user), enabled: int = Form(1)):
    toggle_target_company(company_id, user["id"], bool(enabled))
    return RedirectResponse("/targets", status_code=303)
