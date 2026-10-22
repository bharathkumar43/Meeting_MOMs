"""GraphClient request shapes, paging, retries and cookie handling (no network)."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
import requests
from requests.adapters import BaseAdapter

from app import graph_client
from app.graph_client import GraphClient


class FakeGraph(BaseAdapter):
    """Transport stand-in: records every request and answers from a route table."""

    def __init__(self, routes):
        super().__init__()
        self.routes, self.calls = routes, []

    def send(self, request, **kwargs):
        self.calls.append(request)
        for needle, (status, body, headers) in self.routes.items():
            if needle in request.url:
                resp = requests.Response()
                resp.status_code, resp.url, resp.request = status, request.url, request
                resp.headers.update(headers or {})
                resp._content = body if isinstance(body, bytes) else json.dumps(body).encode()
                resp.encoding = "utf-8"
                return resp
        raise AssertionError(f"unexpected request {request.url}")

    def close(self):
        pass


@pytest.fixture
def fake(monkeypatch):
    def install(routes):
        adapter = FakeGraph(routes)
        monkeypatch.setitem(graph_client._HTTP.adapters, "https://", adapter)
        return adapter
    return install


def test_calendar_view_follows_next_link(fake):
    adapter = fake({
        "page=2": (200, {"value": [{"id": "b", "isOnlineMeeting": False}]}, None),
        "calendarView": (200, {"value": [{"id": "a", "isOnlineMeeting": True}],
                               "@odata.nextLink": "https://graph.microsoft.com/v1.0/x/calendarView?page=2"}, None),
    })
    events = GraphClient("tok").list_user_calendar_events("u1", "2026-10-01", "2026-10-02", online_only=False)
    assert [e["id"] for e in events] == ["a", "b"]
    first = adapter.calls[0]
    assert first.headers["Authorization"] == "Bearer tok"
    assert first.headers["Prefer"] == 'outlook.timezone="UTC"'
    assert "startDateTime=2026-10-01T00%3A00%3A00Z" in first.url


def test_resolve_user_id_is_cached_per_client(fake):
    adapter = fake({"/users/": (200, {"id": "uid-1"}, None)})
    client = GraphClient("tok")
    assert client._resolve_user_id_safe("A@cloudfuze.com") == "uid-1"
    assert client._resolve_user_id_safe("a@cloudfuze.com") == "uid-1"
    assert len(adapter.calls) == 1


def test_list_transcripts_returns_empty_on_error(fake):
    fake({"/transcripts": (403, {"error": "forbidden"}, None)})
    assert GraphClient("tok").list_transcripts("m1", user_id="u1") == []


def test_send_email_payload(fake):
    adapter = fake({"/me/sendMail": (202, b"", None)})
    GraphClient("tok").send_email("a@x.com", "Subj", "<p>b</p>",
                                  [{"bytes": b"abc", "filename": "f.docx", "content_type": "x/y"}],
                                  cc_emails="c@x.com, d@x.com")
    body = json.loads(adapter.calls[0].body)
    assert body["message"]["toRecipients"] == [{"emailAddress": {"address": "a@x.com"}}]
    assert [r["emailAddress"]["address"] for r in body["message"]["ccRecipients"]] == ["c@x.com", "d@x.com"]
    assert body["message"]["attachments"][0]["contentBytes"] == "YWJj"


def test_cookies_work_inside_a_redirect_but_never_carry_to_the_next_call():
    """Same cookie behaviour as the one-shot requests.get() the client used before."""
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            seen.append((self.path, self.headers.get("Cookie")))
            if self.path.startswith("/v1.0/"):
                self.send_response(302)
                self.send_header("Location", "/download")
                self.send_header("Set-Cookie", "dl=abc; Path=/")
                self.send_header("Content-Length", "0")
                self.end_headers()
            else:
                self.send_response(200)
                self.send_header("Content-Length", "4")
                self.end_headers()
                self.wfile.write(b"DATA")

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        client = GraphClient("tok")
        client.base_url = f"http://127.0.0.1:{server.server_address[1]}/v1.0"
        assert client.get_onedrive_file_content("u1", "i1") == b"DATA"
        assert client.get_onedrive_file_content("u1", "i2") == b"DATA"
    finally:
        server.shutdown()
    assert seen == [("/v1.0/users/u1/drive/items/i1/content", None), ("/download", "dl=abc"),
                    ("/v1.0/users/u1/drive/items/i2/content", None), ("/download", "dl=abc")]
    assert len(graph_client._HTTP.cookies) == 0


def test_organizer_id_from_join_url():
    url = ("https://teams.microsoft.com/l/meetup-join/19%3ameeting_x/0?context=%7b%22Tid%22%3a%22t%22"
           "%2c%22Oid%22%3a%2212345678-1234-1234-1234-123456789abc%22%7d")
    assert GraphClient.organizer_id_from_join_url(url) == "12345678-1234-1234-1234-123456789abc"
    assert GraphClient.organizer_id_from_join_url("https://teams.microsoft.com/l/x") is None
