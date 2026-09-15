"""Job source management routes."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from src.source_discovery import fetch_awesome_list
from src.web.deps import render
from src.web.services.config_service import get_sources_list, toggle_source

router = APIRouter()


@router.get("/sources")
def sources_page(request: Request):
    return render(
        request,
        "sources.html",
        {
            "sources": get_sources_list(),
            "discovered": fetch_awesome_list()[:50],
        },
    )


@router.post("/sources/toggle")
async def toggle_source_endpoint(request: Request):
    form = await request.form()
    source_id = str(form.get("source_id", ""))
    enabled = form.get("enabled") == "true"
    toggle_source(source_id, enabled)
    sources = get_sources_list()
    source = next((s for s in sources if s.get("id") == source_id), None)
    if not source:
        return HTMLResponse("Source not found", status_code=404)
    row = f"""<tr class="border-b border-slate-100 dark:border-slate-800">
        <td class="p-3">
          <form hx-post="/sources/toggle" hx-target="closest tr" hx-swap="outerHTML">
            <input type="hidden" name="source_id" value="{source['id']}">
            <input type="hidden" name="enabled" value="{'false' if source.get('enabled') else 'true'}">
            <button type="submit" class="w-10 h-6 rounded-full relative transition-colors {'bg-brand' if source.get('enabled') else 'bg-slate-300 dark:bg-slate-600'}">
              <span class="absolute top-0.5 {'left-5' if source.get('enabled') else 'left-0.5'} w-5 h-5 bg-white rounded-full shadow transition-all"></span>
            </button>
          </form>
        </td>
        <td class="p-3 font-medium text-slate-900 dark:text-slate-100">{source.get('name') or source.get('id')}</td>
        <td class="p-3 text-slate-500 dark:text-slate-400">{source.get('id')}</td>
        <td class="p-3"><span class="px-2 py-0.5 rounded text-xs bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300">{source.get('adapter') or '?'}</span></td>
      </tr>"""
    return HTMLResponse(row)
