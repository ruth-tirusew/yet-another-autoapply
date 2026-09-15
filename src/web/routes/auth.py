"""Authentication routes."""

from __future__ import annotations

from urllib.parse import unquote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from src.db import get_password_reset_token, get_profile, unlink_oauth_account
from src.web.auth.deps import get_current_user, require_user
from src.web.auth.oauth import oauth, oauth_enabled, redirect_uri
from src.web.auth.sessions import get_csrf_token, login_user, logout_user, validate_csrf
from src.web.auth.reset import request_password_reset, reset_password_with_token
from src.web.auth.users import AuthError, authenticate_user, change_password, get_or_create_oauth_user, register_user, set_password
from src.web.deps import render

router = APIRouter()


def _safe_next(next_url: str | None) -> str:
    if not next_url:
        return "/"
    path = unquote(next_url)
    if not path.startswith("/") or path.startswith("//"):
        return "/"
    return path


@router.post("/logout")
def logout(request: Request):
    logout_user(request)
    return RedirectResponse("/login", status_code=303)


@router.get("/login")
def login_page(request: Request, next: str = "/", reset: str = ""):
    if get_current_user(request):
        return RedirectResponse(_safe_next(next), status_code=303)
    ctx = {
        "next": _safe_next(next),
        "csrf_token": get_csrf_token(request),
        "github_enabled": oauth_enabled("github"),
        "google_enabled": oauth_enabled("google"),
        "error": None,
        "success": reset == "ok",
    }
    return render(request, "auth/login.html", ctx, guest=True)


@router.get("/forgot-password")
def forgot_password_page(request: Request):
    if get_current_user(request):
        return RedirectResponse("/", status_code=303)
    return render(
        request,
        "auth/forgot_password.html",
        {"csrf_token": get_csrf_token(request), "error": None, "success": False, "dev_link": None},
        guest=True,
    )


@router.post("/forgot-password")
async def forgot_password_submit(
    request: Request,
    email: str = Form(""),
    csrf_token: str = Form(""),
):
    if get_current_user(request):
        return RedirectResponse("/", status_code=303)
    if not validate_csrf(request, csrf_token):
        return render(
            request,
            "auth/forgot_password.html",
            {
                "csrf_token": get_csrf_token(request),
                "error": "Invalid form submission",
                "success": False,
                "dev_link": None,
                "email": email,
            },
            guest=True,
            status_code=400,
        )
    try:
        result = request_password_reset(email)
    except Exception:
        return render(
            request,
            "auth/forgot_password.html",
            {
                "csrf_token": get_csrf_token(request),
                "error": "Could not send reset email. Check server logs.",
                "success": False,
                "dev_link": None,
                "email": email,
            },
            guest=True,
            status_code=500,
        )
    return render(
        request,
        "auth/forgot_password.html",
        {
            "csrf_token": get_csrf_token(request),
            "error": None,
            "success": True,
            "dev_link": result.get("dev_link"),
            "email": email,
        },
        guest=True,
    )


@router.get("/reset-password")
def reset_password_page(request: Request, token: str = ""):
    if get_current_user(request):
        return RedirectResponse("/", status_code=303)
    if not token or not get_password_reset_token(token):
        return render(
            request,
            "auth/reset_password.html",
            {"csrf_token": get_csrf_token(request), "error": None, "invalid": True, "token": ""},
            guest=True,
        )
    return render(
        request,
        "auth/reset_password.html",
        {"csrf_token": get_csrf_token(request), "error": None, "invalid": False, "token": token},
        guest=True,
    )


@router.post("/reset-password")
async def reset_password_submit(
    request: Request,
    token: str = Form(""),
    password: str = Form(""),
    password_confirm: str = Form(""),
    csrf_token: str = Form(""),
):
    if get_current_user(request):
        return RedirectResponse("/", status_code=303)
    if not validate_csrf(request, csrf_token):
        return render(
            request,
            "auth/reset_password.html",
            {
                "csrf_token": get_csrf_token(request),
                "error": "Invalid form submission",
                "invalid": False,
                "token": token,
            },
            guest=True,
            status_code=400,
        )
    if password != password_confirm:
        return render(
            request,
            "auth/reset_password.html",
            {
                "csrf_token": get_csrf_token(request),
                "error": "Passwords do not match",
                "invalid": False,
                "token": token,
            },
            guest=True,
            status_code=400,
        )
    try:
        reset_password_with_token(token, password)
    except AuthError as e:
        invalid = "invalid or has expired" in str(e).lower()
        return render(
            request,
            "auth/reset_password.html",
            {
                "csrf_token": get_csrf_token(request),
                "error": str(e),
                "invalid": invalid,
                "token": "" if invalid else token,
            },
            guest=True,
            status_code=400,
        )
    return RedirectResponse("/login?reset=ok", status_code=303)


@router.post("/login")
async def login_submit(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    next: str = Form("/"),
    csrf_token: str = Form(""),
):
    if not validate_csrf(request, csrf_token):
        return render(
            request,
            "auth/login.html",
            {
                "next": _safe_next(next),
                "csrf_token": get_csrf_token(request),
                "github_enabled": oauth_enabled("github"),
                "google_enabled": oauth_enabled("google"),
                "error": "Invalid form submission",
            },
            guest=True,
            status_code=400,
        )
    user = authenticate_user(email, password)
    if not user:
        return render(
            request,
            "auth/login.html",
            {
                "next": _safe_next(next),
                "csrf_token": get_csrf_token(request),
                "github_enabled": oauth_enabled("github"),
                "google_enabled": oauth_enabled("google"),
                "error": "Invalid email or password",
            },
            guest=True,
            status_code=401,
        )
    login_user(request, user["id"])
    dest = _safe_next(next)
    if not get_profile(user["id"]):
        dest = "/profile"
    return RedirectResponse(dest, status_code=303)


@router.get("/register")
def register_page(request: Request, next: str = "/"):
    if get_current_user(request):
        return RedirectResponse("/", status_code=303)
    return render(
        request,
        "auth/register.html",
        {
            "next": _safe_next(next),
            "csrf_token": get_csrf_token(request),
            "github_enabled": oauth_enabled("github"),
            "google_enabled": oauth_enabled("google"),
            "error": None,
        },
        guest=True,
    )


@router.post("/register")
async def register_submit(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    password_confirm: str = Form(""),
    display_name: str = Form(""),
    next: str = Form("/"),
    csrf_token: str = Form(""),
):
    if not validate_csrf(request, csrf_token):
        return render(
            request,
            "auth/register.html",
            {
                "next": _safe_next(next),
                "csrf_token": get_csrf_token(request),
                "github_enabled": oauth_enabled("github"),
                "google_enabled": oauth_enabled("google"),
                "error": "Invalid form submission",
            },
            guest=True,
            status_code=400,
        )
    if password != password_confirm:
        return render(
            request,
            "auth/register.html",
            {
                "next": _safe_next(next),
                "csrf_token": get_csrf_token(request),
                "github_enabled": oauth_enabled("github"),
                "google_enabled": oauth_enabled("google"),
                "error": "Passwords do not match",
            },
            guest=True,
            status_code=400,
        )
    try:
        user = register_user(email, password, display_name)
    except AuthError as e:
        return render(
            request,
            "auth/register.html",
            {
                "next": _safe_next(next),
                "csrf_token": get_csrf_token(request),
                "github_enabled": oauth_enabled("github"),
                "google_enabled": oauth_enabled("google"),
                "error": str(e),
            },
            guest=True,
            status_code=400,
        )
    login_user(request, user["id"])
    return RedirectResponse("/profile", status_code=303)


@router.get("/auth/github")
async def github_login(request: Request, next: str = "/"):
    if not oauth_enabled("github"):
        return HTMLResponse("GitHub OAuth is not configured", status_code=503)
    request.session["oauth_next"] = _safe_next(next)
    return await oauth.github.authorize_redirect(request, redirect_uri("github"))


@router.get("/auth/github/callback")
async def github_callback(request: Request):
    if not oauth_enabled("github"):
        return HTMLResponse("GitHub OAuth is not configured", status_code=503)
    token = await oauth.github.authorize_access_token(request)
    resp = await oauth.github.get("user", token=token)
    profile = resp.json()
    emails_resp = await oauth.github.get("user/emails", token=token)
    emails = emails_resp.json()
    primary = next((e["email"] for e in emails if e.get("primary")), None)
    email = primary or profile.get("email") or ""
    user = get_or_create_oauth_user(
        "github",
        str(profile.get("id")),
        email,
        profile.get("name") or profile.get("login") or "",
    )
    login_user(request, user["id"])
    dest = request.session.pop("oauth_next", "/")
    if not get_profile(user["id"]):
        dest = "/profile"
    return RedirectResponse(_safe_next(dest), status_code=303)


@router.get("/auth/google")
async def google_login(request: Request, next: str = "/"):
    if not oauth_enabled("google"):
        return HTMLResponse("Google OAuth is not configured", status_code=503)
    request.session["oauth_next"] = _safe_next(next)
    return await oauth.google.authorize_redirect(request, redirect_uri("google"))


@router.get("/auth/google/callback")
async def google_callback(request: Request):
    if not oauth_enabled("google"):
        return HTMLResponse("Google OAuth is not configured", status_code=503)
    token = await oauth.google.authorize_access_token(request)
    userinfo = token.get("userinfo") or {}
    email = userinfo.get("email") or ""
    user = get_or_create_oauth_user(
        "google",
        str(userinfo.get("sub")),
        email,
        userinfo.get("name") or "",
    )
    login_user(request, user["id"])
    dest = request.session.pop("oauth_next", "/")
    if not get_profile(user["id"]):
        dest = "/profile"
    return RedirectResponse(_safe_next(dest), status_code=303)


@router.post("/settings/save/account")
async def save_account(
    request: Request,
    user: dict = Depends(require_user),
    display_name: str = Form(""),
    current_password: str = Form(""),
    new_password: str = Form(""),
    csrf_token: str = Form(""),
):
    if not validate_csrf(request, csrf_token):
        return HTMLResponse("Invalid form submission", status_code=400)
    from src.db import update_user

    update_user(user["id"], display_name=display_name.strip() or None)
    if new_password:
        try:
            if user.get("password_hash"):
                change_password(user["id"], current_password, new_password)
            else:
                set_password(user["id"], new_password)
        except AuthError as e:
            return HTMLResponse(str(e), status_code=400)
    from fastapi.responses import Response

    return Response(headers={"HX-Trigger": "configSaved"})


@router.post("/settings/unlink-oauth")
async def unlink_oauth(
    request: Request,
    user: dict = Depends(require_user),
    provider: str = Form(...),
    csrf_token: str = Form(""),
):
    if not validate_csrf(request, csrf_token):
        return HTMLResponse("Invalid form submission", status_code=400)
    unlink_oauth_account(user["id"], provider)
    from fastapi.responses import Response

    return Response(headers={"HX-Trigger": "configSaved"})
