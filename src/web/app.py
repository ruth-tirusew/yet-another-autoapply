"""FastAPI control pane application."""

from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.responses import RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from src.db import init_db
from src.settings import SESSION_SECRET
from src.web.auth.deps import RequiresLogin, require_user
from src.web.routes import (
    applications,
    auth,
    coaching,
    dashboard,
    jobs,
    partials,
    pipeline,
    profile,
    settings,
    sources,
    targets,
    templates,
)

app = FastAPI(title="Job Crawler Control Pane")
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, https_only=False)


@app.exception_handler(RequiresLogin)
async def requires_login_handler(request, exc: RequiresLogin):
    return RedirectResponse(f"/login?next={exc.next_url}", status_code=303)


@app.on_event("startup")
def startup():
    init_db()


_auth_dep = [Depends(require_user)]

app.include_router(auth.router)
app.include_router(dashboard.router, dependencies=_auth_dep)
app.include_router(pipeline.router, dependencies=_auth_dep)
app.include_router(partials.router, dependencies=_auth_dep)
app.include_router(applications.router, dependencies=_auth_dep)
app.include_router(jobs.router, dependencies=_auth_dep)
app.include_router(profile.router, dependencies=_auth_dep)
app.include_router(settings.router, dependencies=_auth_dep)
app.include_router(sources.router, dependencies=_auth_dep)
app.include_router(targets.router, dependencies=_auth_dep)
app.include_router(coaching.router, dependencies=_auth_dep)
app.include_router(templates.router, dependencies=_auth_dep)
