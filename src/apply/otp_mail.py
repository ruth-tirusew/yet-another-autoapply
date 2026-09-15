"""IMAP polling to fetch Greenhouse email verification codes."""

from __future__ import annotations

import imaplib
import re
import time
from datetime import datetime, timedelta, timezone
from email import message_from_bytes
from email.header import decode_header
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Any

_GREENHOUSE_SENDER = re.compile(r"greenhouse", re.I)
_OTP_CODE_RE = re.compile(r"\b(\d{6})\b")
_VERIFICATION_KEYWORDS = (
    "verification code",
    "verify your email",
    "one-time",
    "security code",
    "enter the code",
    "confirm your email",
    "check your email",
)


def _decode_header_value(value: str | None) -> str:
    if not value:
        return ""
    parts: list[str] = []
    for chunk, encoding in decode_header(value):
        if isinstance(chunk, bytes):
            parts.append(chunk.decode(encoding or "utf-8", errors="replace"))
        else:
            parts.append(str(chunk))
    return "".join(parts)


def _strip_html(html: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return unescape(re.sub(r"\s+", " ", text)).strip()


def _extract_body(msg) -> str:
    chunks: list[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            content_type = (part.get_content_type() or "").lower()
            if content_type not in ("text/plain", "text/html"):
                continue
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            charset = part.get_content_charset() or "utf-8"
            text = payload.decode(charset, errors="replace")
            chunks.append(_strip_html(text) if content_type == "text/html" else text)
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            text = payload.decode(charset, errors="replace")
            content_type = (msg.get_content_type() or "").lower()
            chunks.append(_strip_html(text) if content_type == "text/html" else text)
    return "\n".join(chunks)


def _is_greenhouse_sender(from_addr: str) -> bool:
    return bool(_GREENHOUSE_SENDER.search(from_addr or ""))


def _has_verification_context(text: str) -> bool:
    lowered = (text or "").lower()
    return any(keyword in lowered for keyword in _VERIFICATION_KEYWORDS)


def _pick_otp_code(text: str) -> str | None:
    if not text:
        return None
    lowered = text.lower()
    matches = list(_OTP_CODE_RE.finditer(text))
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0].group(1)

    best: tuple[int, str] | None = None
    for match in matches:
        start = max(0, match.start() - 80)
        window = lowered[start : match.end() + 20]
        score = sum(1 for keyword in _VERIFICATION_KEYWORDS if keyword in window)
        if best is None or score > best[0]:
            best = (score, match.group(1))
    return best[1] if best and best[0] > 0 else matches[-1].group(1)


def parse_greenhouse_otp(subject: str, body: str, from_addr: str) -> str | None:
    if not _is_greenhouse_sender(from_addr):
        return None
    combined = f"{subject}\n{body}"
    if not _has_verification_context(combined) and not _has_verification_context(subject):
        return None
    return _pick_otp_code(combined)


def extract_otp_from_message(msg) -> str | None:
    from_addr = _decode_header_value(msg.get("From"))
    subject = _decode_header_value(msg.get("Subject"))
    body = _extract_body(msg)
    return parse_greenhouse_otp(subject, body, from_addr)


def _message_datetime(msg) -> datetime | None:
    raw = msg.get("Date")
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _imap_since_clause(since: datetime) -> str:
    day = since.astimezone(timezone.utc)
    return day.strftime("%d-%b-%Y")


def _connect_imap(config: dict[str, Any]) -> imaplib.IMAP4_SSL | imaplib.IMAP4:
    host = config["host"]
    port = int(config.get("port", 993))
    use_tls = bool(config.get("use_tls", True))
    if use_tls:
        client: imaplib.IMAP4_SSL | imaplib.IMAP4 = imaplib.IMAP4_SSL(host, port)
    else:
        client = imaplib.IMAP4(host, port)
    client.login(config["username"], config["password"])
    return client


def _search_inbox_once(client: imaplib.IMAP4_SSL | imaplib.IMAP4, since: datetime) -> str | None:
    client.select("INBOX")
    since_clause = _imap_since_clause(since - timedelta(seconds=30))
    status, data = client.search(None, f'(SINCE "{since_clause}")')
    if status != "OK" or not data or not data[0]:
        return None

    ids = data[0].split()
    for msg_id in reversed(ids[-30:]):
        status, fetched = client.fetch(msg_id, "(RFC822)")
        if status != "OK" or not fetched:
            continue
        for item in fetched:
            if not isinstance(item, tuple) or len(item) < 2:
                continue
            msg = message_from_bytes(item[1])
            msg_at = _message_datetime(msg)
            if msg_at and msg_at < since - timedelta(seconds=30):
                continue
            code = extract_otp_from_message(msg)
            if code:
                return code
    return None


def fetch_greenhouse_otp(
    config: dict[str, Any],
    *,
    since: datetime,
    timeout: int | None = None,
    interval: int | None = None,
) -> str | None:
    timeout_sec = int(timeout if timeout is not None else config.get("poll_timeout_sec", 90))
    interval_sec = int(interval if interval is not None else config.get("poll_interval_sec", 5))
    deadline = time.time() + timeout_sec
    last_error: Exception | None = None

    while time.time() < deadline:
        client = None
        try:
            client = _connect_imap(config)
            code = _search_inbox_once(client, since)
            if code:
                return code
        except Exception as exc:
            last_error = exc
        finally:
            if client is not None:
                try:
                    client.logout()
                except Exception:
                    pass
        if time.time() >= deadline:
            break
        time.sleep(interval_sec)

    if last_error:
        raise RuntimeError(f"IMAP poll failed: {last_error}") from last_error
    return None


def test_imap_connection(config: dict[str, Any]) -> tuple[bool, str]:
    client = None
    try:
        client = _connect_imap(config)
        status, _ = client.select("INBOX", readonly=True)
        if status != "OK":
            return False, "Could not open INBOX"
        return True, "Connected and INBOX opened successfully"
    except Exception as exc:
        return False, str(exc)
    finally:
        if client is not None:
            try:
                client.logout()
            except Exception:
                pass


def build_imap_config(cfg: dict[str, Any], password: str | None) -> dict[str, Any] | None:
    if not cfg.get("otp_auto_fetch"):
        return None
    if not password:
        return None
    imap = cfg.get("otp_imap") or {}
    applicant = cfg.get("applicant") or {}
    username = (imap.get("username") or applicant.get("email") or "").strip()
    if not username:
        return None
    host = (imap.get("host") or "imap.gmail.com").strip()
    if not host:
        return None
    return {
        "host": host,
        "port": int(imap.get("port", 993)),
        "username": username,
        "password": password,
        "use_tls": bool(imap.get("use_tls", True)),
        "poll_timeout_sec": int(imap.get("poll_timeout_sec", 90)),
        "poll_interval_sec": int(imap.get("poll_interval_sec", 5)),
    }
