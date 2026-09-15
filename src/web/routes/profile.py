"""Profile upload routes."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from fastapi.responses import RedirectResponse

from src.db import get_profile
from src.profile import evaluate_stored_profile
from src.settings import user_profile_dir
from src.tenant import resolve_user_id
from src.web.deps import render
from src.web.services.profile_uploader import (
    consume_profile_upload_result,
    is_profile_uploading,
    start_profile_upload,
)

router = APIRouter()


@router.get("/profile")
def profile_page(request: Request):
    profile = get_profile()
    return render(
        request,
        "profile.html",
        {
            "profile": profile,
            "show_onboarding": profile is None,
            "uploading": is_profile_uploading(resolve_user_id()),
        },
    )


@router.post("/profile/evaluate")
def profile_evaluate():
    try:
        evaluate_stored_profile()
    except Exception as e:
        return Response(f"Evaluation failed: {e}", status_code=500)
    return RedirectResponse("/profile", status_code=303)


@router.post("/profile/upload")
async def profile_upload(request: Request):
    form = await request.form()
    file = form.get("file")
    if not file or not hasattr(file, "filename"):
        return RedirectResponse("/profile", status_code=303)

    uid = resolve_user_id()
    profile_dir = user_profile_dir(uid)
    dest = profile_dir / "upload_temp.pdf"
    profile_dir.mkdir(parents=True, exist_ok=True)
    content = await file.read()
    dest.write_bytes(content)
    start_profile_upload(uid, str(dest))
    return RedirectResponse("/profile", status_code=303)


@router.get("/partials/profile-upload-status")
def profile_upload_status_partial(request: Request):
    uid = resolve_user_id()
    if is_profile_uploading(uid):
        return render(
            request, "partials/profile_upload_status.html", {"status": "uploading", "error": None, "warnings": []}
        )

    result = consume_profile_upload_result(uid)
    if result and result.get("error"):
        return render(
            request,
            "partials/profile_upload_status.html",
            {"status": "error", "error": result["error"], "warnings": []},
        )
    if result and result.get("warnings"):
        return render(
            request,
            "partials/profile_upload_status.html",
            {"status": "warnings", "error": None, "warnings": result["warnings"]},
        )
    return Response(status_code=200, headers={"HX-Refresh": "true"})
