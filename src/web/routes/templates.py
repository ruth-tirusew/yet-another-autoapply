"""CV Template Studio routes."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from src.cv_templates.compiler import compile_preset
from src.cv_templates.preview import preview_resume
from src.cv_templates.store import (
    delete_template,
    duplicate_template,
    ensure_user_templates,
    get_template_by_slug,
    list_templates,
    preset_from_form,
    save_template_preset,
    set_default_template,
)
from src.render_cv import render_resume_html
from src.profile_format import normalize_skills
from jinja2 import Template
from src.web.auth.deps import require_user
from src.web.deps import render

router = APIRouter()


@router.get("/templates")
def templates_gallery(request: Request, user: dict = Depends(require_user)):
    templates = ensure_user_templates(user["id"])
    return render(
        request,
        "templates/gallery.html",
        {"templates": templates},
    )


@router.get("/templates/{slug}/edit")
def template_editor(request: Request, slug: str, user: dict = Depends(require_user)):
    tpl = get_template_by_slug(slug, user["id"])
    if not tpl:
        return HTMLResponse("Template not found", status_code=404)
    return render(
        request,
        "templates/editor.html",
        {
            "template": tpl,
            "preset_json": tpl.preset.model_dump_json(indent=2),
            "sections_json": json.dumps([s.model_dump() for s in tpl.preset.sections]),
        },
    )


@router.get("/templates/{slug}/preview")
def template_preview(
    slug: str,
    sample: str = Query("profile"),
    job_id: int | None = Query(None),
    user: dict = Depends(require_user),
):
    tpl = get_template_by_slug(slug, user["id"])
    if not tpl:
        return HTMLResponse("Template not found", status_code=404)
    resume = preview_resume(sample=sample, user_id=user["id"], job_id=job_id)
    html = render_resume_html(resume, template=tpl, user_id=user["id"])
    return HTMLResponse(html)


@router.post("/templates/{slug}/preview-live")
async def template_preview_live(request: Request, slug: str, user: dict = Depends(require_user)):
    tpl = get_template_by_slug(slug, user["id"])
    if not tpl:
        return HTMLResponse("Template not found", status_code=404)
    form = await request.form()
    preset = preset_from_form(dict(form))
    resume = preview_resume(sample="profile", user_id=user["id"])
    skills = normalize_skills(resume.get("skills"))
    if skills:
        resume = dict(resume)
        resume["skills"] = skills
    html_source = compile_preset(preset)
    html = Template(html_source).render(resume=resume)
    return HTMLResponse(html)


@router.post("/templates/{slug}/save")
async def template_save(request: Request, slug: str, user: dict = Depends(require_user)):
    form = await request.form()
    form_dict = dict(form)
    preset = preset_from_form(form_dict)
    try:
        save_template_preset(slug, preset, user_id=user["id"], name=form_dict.get("name"))
    except ValueError as e:
        return HTMLResponse(str(e), status_code=400)
    return RedirectResponse(f"/templates/{slug}/edit?saved=1", status_code=303)


@router.post("/templates/{slug}/default")
def template_set_default(slug: str, user: dict = Depends(require_user)):
    tpl = get_template_by_slug(slug, user["id"])
    if not tpl:
        return HTMLResponse("Template not found", status_code=404)
    set_default_template(tpl.id, user["id"])
    return RedirectResponse("/templates", status_code=303)


@router.post("/templates/{slug}/duplicate")
def template_duplicate(
    slug: str,
    name: str = Form(...),
    user: dict = Depends(require_user),
):
    try:
        new_tpl = duplicate_template(slug, name.strip(), user_id=user["id"])
    except ValueError as e:
        return HTMLResponse(str(e), status_code=400)
    return RedirectResponse(f"/templates/{new_tpl.slug}/edit", status_code=303)


@router.post("/templates/{slug}/delete")
def template_delete(slug: str, user: dict = Depends(require_user)):
    try:
        delete_template(slug, user_id=user["id"])
    except ValueError as e:
        return HTMLResponse(str(e), status_code=400)
    return RedirectResponse("/templates", status_code=303)
