"""HTTP client for tenant-configured URLs that only connects to public addresses.

Addresses are checked when each connection opens, on the same lookup used to connect,
so redirects and DNS rebinding cannot reach loopback, private or metadata endpoints.
"""

import http.client
import ipaddress
import socket
import urllib.request
from urllib.parse import urlsplit

from marketdata.contracts import InvalidRecord

ALLOWED_SCHEMES = ("http", "https")


def is_blocked_ip(value):
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return True
    ip = getattr(ip, "ipv4_mapped", None) or ip
    return not ip.is_global or ip.is_multicast or ip.is_reserved


def connect_public(address, timeout=None, source_address=None):
    host, port = address
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if any(is_blocked_ip(sockaddr[0]) for *_, sockaddr in infos):
        raise InvalidRecord("source_destination_blocked")
    error = OSError(f"no address for {host}")
    for family, kind, proto, _, sockaddr in infos:
        sock = socket.socket(family, kind, proto)
        try:
            if isinstance(timeout, int | float):
                sock.settimeout(timeout)
            sock.connect(sockaddr)
            return sock
        except OSError as exc:
            sock.close()
            error = exc
    raise error


class PublicHTTPConnection(http.client.HTTPConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = connect_public


class PublicHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = connect_public


class PublicHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(PublicHTTPConnection, req)


class PublicHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(PublicHTTPSConnection, req, context=self._context)


class PublicRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlsplit(newurl).scheme not in ALLOWED_SCHEMES:
            raise InvalidRecord("source_url_scheme_not_allowed")
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        # Source headers can hold API keys; they only belong to the configured host.
        if redirected and urlsplit(newurl).netloc != urlsplit(req.full_url).netloc:
            redirected.headers = {k: v for k, v in redirected.headers.items() if k == "User-agent"}
        return redirected


def build_opener():
    # Built by hand: urllib.request.build_opener adds file, ftp and proxy handlers.
    opener = urllib.request.OpenerDirector()
    for handler in (
        urllib.request.UnknownHandler(),
        urllib.request.HTTPDefaultErrorHandler(),
        urllib.request.HTTPErrorProcessor(),
        PublicRedirectHandler(),
        PublicHTTPHandler(),
        PublicHTTPSHandler(),
    ):
        opener.add_handler(handler)
    return opener


def open_public(request, timeout):
    if urlsplit(request.full_url).scheme not in ALLOWED_SCHEMES:
        raise InvalidRecord("source_url_scheme_not_allowed")
    return build_opener().open(request, timeout=timeout)
