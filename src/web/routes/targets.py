"""Target company list management."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from src.catalog_db import (
    add_target_company,
    delete_target_company,
    list_target_companies,
    pin_target_company,
    toggle_target_company,
)
from src.crawler.ats_detect import detect_ats_type
from src.db import connect
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
    with connect() as conn:
        row = conn.execute(
            "SELECT origin FROM target_companies WHERE id = ? AND user_id = ?",
            (company_id, user["id"]),
        ).fetchone()
    # Removing a company the watchlist auto-added is treated as a "don't
    # want this" signal and stops it being auto-added again; removing one
    # you added yourself is just... removing it.
    blocklist = bool(row and row["origin"] == "auto")
    delete_target_company(company_id, user["id"], blocklist=blocklist)
    return RedirectResponse("/targets", status_code=303)


@router.post("/targets/{company_id}/toggle")
def targets_toggle(company_id: int, user: dict = Depends(require_user), enabled: int = Form(1)):
    toggle_target_company(company_id, user["id"], bool(enabled))
    return RedirectResponse("/targets", status_code=303)


@router.post("/targets/{company_id}/pin")
def targets_pin(company_id: int, user: dict = Depends(require_user)):
    """Turn an auto-added company into a manual one, so it's never auto-pruned."""
    pin_target_company(company_id, user["id"])
    return RedirectResponse("/targets", status_code=303)
