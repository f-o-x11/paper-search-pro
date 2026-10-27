"""Opt-in: publish a finished report.html to a public deed.page URL.

Never part of the default flow (R-19). Only run when the user explicitly asks
for a shareable / hosted link to the report. The report goes to a third-party
host (https://deed.page, docs: https://deed.page/docs) and anyone with the URL
can open it, so the agent must say so before running this.

The rendered report is a single self-contained HTML file that is often larger
than the 4 MB single-request cap, so this always uses deed.page's manifest
upload (POST /v1/uploads -> PUT the missing blob -> POST finalize). That is
one code path for every report size. No account or API key is needed.

Token handling: the first publish returns an owner token. It is saved to
``--token-file`` (default ``~/.config/deed-page/token``, mode 600) and reused
for later publishes so updates and deletes keep working. The token is never
printed, written into the search directory, or embedded in the report.

Uses only ``requests`` (already a runtime dependency).
"""

import hashlib
import json
import os
import random
import re
import string
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import requests

API = "https://deed.page"
CLIENT = "paper-search-pro"
DEFAULT_TOKEN_FILE = Path("~/.config/deed-page/token")
_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_TIMEOUT = 60


class PublishError(RuntimeError):
    """A deed.page call failed. ``message`` is safe to show to the user."""


def default_slug(report: Path) -> str:
    """``psp-<topic words from the search_id>-<6 random chars>``.

    search_id is ``YYYYMMDD_HHMMSS_<slug>``; only the trailing slug is used so
    the URL does not leak a timestamp.
    """
    stem = report.resolve().parent.name
    topic = stem.split("_", 2)[-1] if stem.count("_") >= 2 else stem
    topic = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")[:40].strip("-")
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return "-".join(p for p in ("psp", topic, suffix) if p)


def load_token(token_file: Path) -> Optional[str]:
    env = os.environ.get("DEEDPAGE_TOKEN", "").strip()
    if env:
        return env
    path = token_file.expanduser()
    if path.is_file():
        value = path.read_text(encoding="utf-8").strip()
        return value or None
    return None


def save_token(token_file: Path, token: str) -> Path:
    path = token_file.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(token)
    os.chmod(path, 0o600)
    return path


def _check(resp: requests.Response, step: str) -> Dict[str, Any]:
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if resp.status_code >= 400 or body.get("ok") is False:
        # Plan-limit errors carry user_message with an upgrade link; deed.page
        # asks agents to show it verbatim and not retry.
        msg = body.get("user_message") or body.get("message") or body.get("error")
        raise PublishError(f"{step} failed (HTTP {resp.status_code}): {msg or resp.text[:300]}")
    return body


def publish(
    report: Path,
    *,
    slug: str,
    ttl: Optional[int] = None,
    token: Optional[str] = None,
    session: Any = requests,
) -> Dict[str, Any]:
    """Publish ``report`` as ``index.html`` of ``https://<slug>.deed.page/``.

    Returns the finalize response (it contains ``token`` only on a first,
    token-less publish).
    """
    if not _SLUG_RE.match(slug):
        raise PublishError(f"invalid slug {slug!r}: use 1-63 lowercase letters, digits and hyphens")
    data = report.read_bytes()
    headers = {"X-Client": CLIENT}
    if ttl:
        headers["X-Ttl"] = str(int(ttl))
    if token:
        headers["Authorization"] = f"Bearer {token}"
    manifest = {
        "slug": slug,
        "files": [{
            "path": "index.html",
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
            "content_type": "text/html",
        }],
    }
    plan = _check(
        session.post(f"{API}/v1/uploads", json=manifest, headers=headers, timeout=_TIMEOUT),
        "upload session",
    )
    for blob in plan.get("missing", []):
        up = blob["upload"]
        resp = session.request(up.get("method", "PUT"), up["url"], data=data,
                               headers=up.get("headers", {}), timeout=_TIMEOUT)
        if resp.status_code >= 400:
            raise PublishError(f"blob upload failed (HTTP {resp.status_code})")
    return _check(
        session.post(plan["finalize_url"], headers=headers, timeout=_TIMEOUT),
        "finalize",
    )


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Opt-in: publish report.html to a public deed.page URL (only when the user asks for a link).",
    )
    parser.add_argument("--report", required=True, type=Path, help="Path to $SEARCH_DIR/report.html")
    parser.add_argument("--slug", help="Subdomain to use (default: psp-<topic>-<random>)")
    parser.add_argument("--ttl", type=int, help="Seconds until the page expires (default: permanent)")
    parser.add_argument("--token-file", type=Path, default=DEFAULT_TOKEN_FILE,
                        help="Where the deed.page owner token is read/saved (default: ~/.config/deed-page/token)")
    args = parser.parse_args(argv)

    if not args.report.is_file():
        print(json.dumps({"ok": False, "error": f"report not found: {args.report}"}))
        return 1
    slug = args.slug or default_slug(args.report)
    try:
        result = publish(args.report, slug=slug, ttl=args.ttl, token=load_token(args.token_file))
    except (PublishError, requests.RequestException) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1

    out = {
        "ok": True,
        "url": result.get("url"),
        "slug": result.get("slug", slug),
        "expires_at": result.get("expires_at"),
    }
    new_token = result.get("token")
    if new_token:
        out["token_saved_to"] = str(save_token(args.token_file, new_token))
    if isinstance(result.get("badge"), dict) and result["badge"].get("shown"):
        out["badge_note"] = "Free-plan sites show a small 'Published with deed.page' link."
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
