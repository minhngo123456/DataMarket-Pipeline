"""Source fetches must never reach non-public destinations."""

import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from marketdata.contracts import InvalidRecord
from services.ingestion import api_worker, safe_http, web_worker

FETCHERS = [api_worker.default_fetcher, web_worker.default_fetcher]


@pytest.fixture
def server():
    """Local server: /redirect?<url> answers 302, anything else answers 200 and records the request."""
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.path, dict(self.headers)))
            if self.path.startswith("/redirect?"):
                self.send_response(302)
                self.send_header("Location", self.path.split("?", 1)[1])
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args):
            pass

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    httpd.seen = seen
    httpd.base = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield httpd
    httpd.shutdown()
    httpd.server_close()


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "100.64.0.1",
        "0.0.0.0",
        "224.0.0.1",
        "240.0.0.1",
        "255.255.255.255",
        "::1",
        "fe80::1",
        "fc00::1",
        "ff02::1",
        "::ffff:127.0.0.1",
        "::ffff:169.254.169.254",
    ],
)
def test_non_public_addresses_are_blocked(ip):
    assert safe_http.is_blocked_ip(ip)


@pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"])
def test_public_addresses_are_allowed(ip):
    assert not safe_http.is_blocked_ip(ip)


@pytest.mark.parametrize("fetch", FETCHERS)
def test_loopback_url_is_refused_without_connecting(fetch, server):
    with pytest.raises(InvalidRecord, match="source_destination_blocked"):
        fetch(server.base + "/secret")
    assert server.seen == []


@pytest.mark.parametrize("fetch", FETCHERS)
def test_https_url_is_checked_before_tls(fetch, server):
    with pytest.raises(InvalidRecord, match="source_destination_blocked"):
        fetch(server.base.replace("http://", "https://") + "/secret")
    assert server.seen == []


@pytest.mark.parametrize("fetch", FETCHERS)
@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/x"])
def test_other_schemes_are_refused(fetch, url):
    with pytest.raises(InvalidRecord, match="source_url_scheme_not_allowed"):
        fetch(url)


@pytest.mark.parametrize("fetch", FETCHERS)
def test_hostname_resolving_to_metadata_address_is_refused(fetch, monkeypatch):
    monkeypatch.setattr(
        safe_http.socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 80))],
    )
    with pytest.raises(InvalidRecord, match="source_destination_blocked"):
        fetch("http://prices.example.com/feed")


def test_any_blocked_answer_refuses_the_whole_lookup(monkeypatch):
    monkeypatch.setattr(
        safe_http.socket,
        "getaddrinfo",
        lambda *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 80)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 80)),
        ],
    )
    with pytest.raises(InvalidRecord, match="source_destination_blocked"):
        safe_http.connect_public(("mixed.example.com", 80), 1)


@pytest.mark.parametrize("fetch", FETCHERS)
def test_redirect_to_blocked_address_is_refused(fetch, server, monkeypatch):
    # Treat this test server as public so only the redirect target is blocked.
    monkeypatch.setattr(safe_http, "is_blocked_ip", lambda ip: ip != "127.0.0.1")
    target = f"http://169.254.169.254:{server.server_address[1]}/latest/meta-data"
    with pytest.raises(InvalidRecord, match="source_destination_blocked"):
        fetch(f"{server.base}/redirect?{target}")
    assert [path for path, _ in server.seen] == [f"/redirect?{target}"]


def test_redirect_to_other_scheme_is_refused(server, monkeypatch):
    monkeypatch.setattr(safe_http, "is_blocked_ip", lambda ip: False)
    with pytest.raises(InvalidRecord, match="source_url_scheme_not_allowed"):
        api_worker.default_fetcher(f"{server.base}/redirect?ftp://example.com/x")


def test_allowed_fetch_returns_body_and_sends_headers(server, monkeypatch):
    monkeypatch.setattr(safe_http, "is_blocked_ip", lambda ip: False)
    assert api_worker.default_fetcher(server.base + "/feed", headers={"Authorization": "Bearer k"}) == b"ok"
    assert server.seen[0][1]["Authorization"] == "Bearer k"


def test_redirect_to_other_host_drops_source_headers(server, monkeypatch):
    monkeypatch.setattr(safe_http, "is_blocked_ip", lambda ip: False)
    port = server.server_address[1]
    data = api_worker.default_fetcher(
        f"{server.base}/redirect?http://localhost:{port}/next",
        headers={"Authorization": "Bearer k", "User-Agent": "Test"},
    )
    assert data == b"ok"
    first, second = (headers for _, headers in server.seen)
    assert first["Authorization"] == "Bearer k"
    assert "Authorization" not in second
    assert second["User-Agent"] == "Test"
