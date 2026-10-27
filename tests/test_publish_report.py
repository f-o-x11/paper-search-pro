"""Offline tests for the opt-in deed.page publish helper (no network)."""

import hashlib
import json
import stat

import pytest

from scripts import publish_report as pr


class _Resp:
    def __init__(self, status, body=None):
        self.status_code = status
        self._body = body
        self.text = json.dumps(body) if body is not None else ""

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class _FakeSession:
    """Records calls and answers like deed.page's manifest upload flow."""

    def __init__(self, finalize_body=None, missing=True):
        self.calls = []
        self.finalize_body = finalize_body or {
            "ok": True, "url": "https://psp-x.deed.page/", "slug": "psp-x", "token": "deed_tok_new",
        }
        self.missing = missing

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append(("POST", url, json, dict(headers or {})))
        if url.endswith("/v1/uploads"):
            f = json["files"][0]
            missing = [{"sha256": f["sha256"], "upload": {"method": "PUT", "url": "https://blob.example/put", "headers": {"x-up": "1"}}}]
            return _Resp(201, {"ok": True, "missing": missing if self.missing else [], "present": [],
                               "finalize_url": "https://deed.page/v1/uploads/upl_1/finalize"})
        return _Resp(201, self.finalize_body)

    def request(self, method, url, data=None, headers=None, timeout=None):
        self.calls.append((method, url, data, dict(headers or {})))
        return _Resp(200, {})


@pytest.fixture
def report(tmp_path):
    d = tmp_path / "paper-search-results" / "20260521_143052_prospect-theory-decision"
    d.mkdir(parents=True)
    p = d / "report.html"
    p.write_text("<!doctype html><title>r</title><p>report</p>", encoding="utf-8")
    return p


def test_default_slug_uses_topic_not_timestamp(report):
    slug = pr.default_slug(report)
    assert slug.startswith("psp-prospect-theory-decision-")
    assert "20260521" not in slug
    assert pr._SLUG_RE.match(slug)


def test_publish_manifest_blob_finalize(report):
    s = _FakeSession()
    out = pr.publish(report, slug="psp-x", ttl=600, token=None, session=s)
    assert out["url"] == "https://psp-x.deed.page/"
    (m, url, manifest, headers), (bm, burl, bdata, bheaders), (fm, furl, _, fheaders) = s.calls
    data = report.read_bytes()
    assert url == "https://deed.page/v1/uploads"
    assert manifest["files"] == [{"path": "index.html", "sha256": hashlib.sha256(data).hexdigest(),
                                  "size": len(data), "content_type": "text/html"}]
    assert headers["X-Ttl"] == "600" and headers["X-Client"] == "paper-search-pro"
    assert "Authorization" not in headers
    assert (bm, burl, bdata, bheaders) == ("PUT", "https://blob.example/put", data, {"x-up": "1"})
    assert furl.endswith("/finalize") and fheaders["X-Ttl"] == "600"


def test_existing_token_is_sent_and_present_blob_skipped(report):
    s = _FakeSession(missing=False)
    pr.publish(report, slug="psp-x", token="deed_tok_old", session=s)
    assert [c[0] for c in s.calls] == ["POST", "POST"]
    assert all(c[3]["Authorization"] == "Bearer deed_tok_old" for c in s.calls)
    assert "X-Ttl" not in s.calls[0][3]


def test_limit_error_surfaces_user_message(report):
    class _Limit(_FakeSession):
        def post(self, url, json=None, headers=None, timeout=None):
            return _Resp(413, {"ok": False, "error": "quota_bytes",
                               "user_message": "Hobby $3/mo lifts this. Upgrade: https://deed.page/upgrade/x"})
    with pytest.raises(pr.PublishError, match="Upgrade: https://deed.page/upgrade/x"):
        pr.publish(report, slug="psp-x", session=_Limit())


def test_invalid_slug_rejected_before_any_call(report):
    s = _FakeSession()
    with pytest.raises(pr.PublishError):
        pr.publish(report, slug="Bad_Slug", session=s)
    assert s.calls == []


def test_main_saves_token_privately_and_never_prints_it(report, tmp_path, monkeypatch, capsys):
    s = _FakeSession()
    monkeypatch.delenv("DEEDPAGE_TOKEN", raising=False)
    real_publish = pr.publish
    monkeypatch.setattr(pr, "publish", lambda *a, **k: real_publish(*a, session=s, **k))
    token_file = tmp_path / "cfg" / "deed-page" / "token"
    rc = pr.main(["--report", str(report), "--slug", "psp-x", "--token-file", str(token_file)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "deed_tok_new" not in out
    assert json.loads(out)["url"] == "https://psp-x.deed.page/"
    assert token_file.read_text() == "deed_tok_new"
    assert stat.S_IMODE(token_file.stat().st_mode) == 0o600
    assert pr.load_token(token_file) == "deed_tok_new"
