#!/usr/bin/env python3
"""Tests for lib/article_preview.py: metadata extraction, ok/failed/retry
classification, and the public-address guard applied to every redirect hop."""
import socket
from unittest.mock import patch

import pytest
import requests

from atmos_gl.lib import article_preview
from atmos_gl.lib.article_preview import (
    STATUS_FAILED,
    STATUS_OK,
    STATUS_RETRY,
    UnsafeUrl,
    _assert_public_url,
    extract_preview,
    fetch_article_preview,
)

_PUBLIC_IP = "93.184.216.34"


def _page(head: str) -> bytes:
    return f"<html><head>{head}</head><body><p>body</p></body></html>".encode()


# ---- extract_preview ----------------------------------------------------------------

def test_extract_prefers_og_tags():
    html = _page(
        '<title>Site | Headline</title>'
        '<meta property="og:title" content="Strikes hit Kabul">'
        '<meta name="twitter:title" content="ignored">'
        '<meta property="og:description" content="Pakistani airstrikes killed 22.">'
        '<meta name="description" content="ignored">'
    )
    assert extract_preview(html) == ("Strikes hit Kabul", "Pakistani airstrikes killed 22.")


def test_extract_falls_back_to_twitter_then_title_and_plain_description():
    html = _page(
        '<title>Fallback title</title>'
        '<meta name="twitter:description" content="From twitter.">'
    )
    assert extract_preview(html) == ("Fallback title", "From twitter.")
    html = _page('<meta name="twitter:title" content="TT"><meta name="description" content="D">')
    assert extract_preview(html) == ("TT", "D")


def test_extract_strips_markup_collapses_whitespace_and_decodes_entities():
    html = _page(
        '<meta property="og:title" content="  Police &amp; army\n   clash ">'
        '<meta property="og:description" content="Photo\n        KANSAS CITY &lt;b&gt;x&lt;/b&gt;">'
    )
    assert extract_preview(html) == ("Police & army clash", "Photo KANSAS CITY x")


def test_extract_drops_a_summary_that_just_repeats_the_headline():
    html = _page(
        '<meta property="og:title" content="Same text">'
        '<meta property="og:description" content="same TEXT">'
    )
    assert extract_preview(html) == ("Same text", None)


def test_extract_truncates_long_text_at_a_word_boundary():
    long_summary = "word " * 300
    html = _page(f'<meta property="og:title" content="T"><meta property="og:description" content="{long_summary}">')
    _, summary = extract_preview(html)
    assert len(summary) <= article_preview._SUMMARY_MAX + 1
    assert summary.endswith("word…")


def test_extract_returns_none_when_no_metadata():
    assert extract_preview(_page("")) == (None, None)


# ---- _assert_public_url -------------------------------------------------------------

def _resolves_to(ip):
    return patch.object(socket, "getaddrinfo", return_value=[(None, None, None, None, (ip, 0))])


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.5", "172.18.0.3", "192.168.1.1",
                                "169.254.169.254", "::1", "fd00::1"])
def test_non_public_addresses_are_refused(ip):
    with _resolves_to(ip), pytest.raises(UnsafeUrl):
        _assert_public_url("http://example.com/a")


@pytest.mark.parametrize("url", ["ftp://example.com/a", "file:///etc/passwd", "http:///nohost"])
def test_non_http_urls_are_refused(url):
    with pytest.raises(UnsafeUrl):
        _assert_public_url(url)


def test_public_address_is_allowed():
    with _resolves_to(_PUBLIC_IP):
        _assert_public_url("https://example.com/a")


# ---- fetch_article_preview ----------------------------------------------------------

class _Resp:
    def __init__(self, status=200, body=b"", content_type="text/html; charset=utf-8", location=None):
        self.status_code = status
        self._body = body
        self.headers = {"content-type": content_type}
        if location:
            self.headers["location"] = location
        self.is_redirect = location is not None

    def iter_content(self, chunk_size):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _serve(*responses):
    return patch.object(article_preview.requests, "get", side_effect=list(responses))


_GOOD = _page('<meta property="og:title" content="Headline"><meta property="og:description" content="Lede.">')


def test_fetch_ok():
    with _resolves_to(_PUBLIC_IP), _serve(_Resp(body=_GOOD)):
        p = fetch_article_preview("https://news.example/a")
    assert (p.status, p.http_status, p.headline, p.summary) == (STATUS_OK, 200, "Headline", "Lede.")


def test_fetch_never_stores_a_block_pages_title():
    cloudflare = _page("<title>Attention Required! | Cloudflare</title>")
    with _resolves_to(_PUBLIC_IP), _serve(_Resp(status=403, body=cloudflare)):
        p = fetch_article_preview("https://news.example/a")
    assert (p.status, p.http_status, p.headline) == (STATUS_FAILED, 403, None)


@pytest.mark.parametrize("status", [429, 500, 503])
def test_fetch_rate_limit_and_server_errors_are_retried(status):
    with _resolves_to(_PUBLIC_IP), _serve(_Resp(status=status)):
        assert fetch_article_preview("https://news.example/a").status == STATUS_RETRY


def test_fetch_network_error_is_retried():
    with _resolves_to(_PUBLIC_IP), \
         patch.object(article_preview.requests, "get", side_effect=requests.Timeout("slow")):
        assert fetch_article_preview("https://news.example/a").status == STATUS_RETRY


def test_fetch_non_html_and_metadata_less_pages_fail_permanently():
    with _resolves_to(_PUBLIC_IP), _serve(_Resp(body=b"%PDF", content_type="application/pdf")):
        assert fetch_article_preview("https://news.example/a.pdf").status == STATUS_FAILED
    with _resolves_to(_PUBLIC_IP), _serve(_Resp(body=_page(""))):
        assert fetch_article_preview("https://news.example/a").status == STATUS_FAILED


def test_fetch_follows_a_redirect_to_a_public_host():
    with _resolves_to(_PUBLIC_IP), _serve(
        _Resp(status=301, location="https://www.news.example/a"), _Resp(body=_GOOD)
    ) as mock_get:
        p = fetch_article_preview("http://news.example/a")
    assert p.status == STATUS_OK
    assert mock_get.call_args_list[1].args[0] == "https://www.news.example/a"


def test_fetch_refuses_a_redirect_into_the_private_network():
    def resolve(host, port):
        ip = "10.0.0.7" if host == "internal.example" else _PUBLIC_IP
        return [(None, None, None, None, (ip, 0))]

    with patch.object(socket, "getaddrinfo", side_effect=resolve), _serve(
        _Resp(status=302, location="http://internal.example/admin")
    ) as mock_get:
        p = fetch_article_preview("https://news.example/a")
    assert p.status == STATUS_FAILED
    assert mock_get.call_count == 1  # the private hop is never requested


def test_fetch_reads_no_more_than_the_byte_cap():
    huge = _GOOD + b"x" * (5 * 1024 * 1024)
    with _resolves_to(_PUBLIC_IP), _serve(_Resp(body=huge)), \
         patch.object(article_preview, "extract_preview", wraps=extract_preview) as spy:
        fetch_article_preview("https://news.example/a")
    assert len(spy.call_args.args[0]) <= article_preview._MAX_BYTES


# ---- site-wide og tags and interstitials (both seen live) ---------------------------

_ARIANA_URL = ("https://www.ariananews.af/un-expert-calls-for-accountability-after-"
               "pakistani-airstrikes-kill-civilians-in-afghanistan/")
_ARIANA = (
    '<html><head>'
    '<title>UN expert calls for accountability after Pakistani airstrikes kill civilians '
    'in Afghanistan | Ariana News | Afghanistan</title>'
    '<meta property="og:title" content="Afghanistan News Today – Breaking News, Politics, '
    'Sports &amp; Live Updates | Ariana News">'
    '<meta name="twitter:title" content="Afghanistan News Today | Ariana News">'
    '<meta property="og:description" content="Stay informed with the latest breaking news '
    'from Afghanistan.">'
    '<meta name="description" content="UN Special Rapporteur Richard Bennett has called for '
    'accountability.">'
    '</head><body><h1>UN expert calls for accountability after Pakistani airstrikes kill '
    'civilians in Afghanistan</h1><h1>Other story</h1></body></html>'
).encode()


def test_site_wide_og_title_is_replaced_by_the_slug_matching_h1_and_plain_description():
    assert extract_preview(_ARIANA, _ARIANA_URL) == (
        "UN expert calls for accountability after Pakistani airstrikes kill civilians in Afghanistan",
        "UN Special Rapporteur Richard Bennett has called for accountability.",
    )


def test_without_a_url_og_title_is_used_as_before():
    assert extract_preview(_ARIANA)[0].startswith("Afghanistan News Today")


def test_og_title_is_kept_when_nothing_matches_the_slug():
    # e.g. a transliterated slug on a non-English site: no candidate matches, so og:title
    # stays -- the slug check only ever reorders candidates.
    html = _page('<title>Заголовок</title><meta property="og:title" content="Заголовок статьи">'
                 '<meta property="og:description" content="Описание">')
    url = "https://news.example/ru/zagolovok-stati-o-sobytiyah-v-kieve/"
    assert extract_preview(html, url) == ("Заголовок статьи", "Описание")


def test_og_title_matching_the_slug_is_kept():
    html = _page('<meta property="og:title" content="Police track homicide suspect to Kansas City apartment">'
                 '<meta property="og:description" content="Lede.">')
    url = "https://news.example/2026/10/02/police-track-homicide-suspect-kansas-city/"
    assert extract_preview(html, url) == (
        "Police track homicide suspect to Kansas City apartment", "Lede.")


@pytest.mark.parametrize("head", [
    '<meta property="og:title" content="No Cookies | Geelong Advertiser">',
    '<title>This website is unavailable in your location. – KIRO 7 News Seattle</title>',
    '<meta property="og:title" content="Oops! Page not found">',
])
def test_interstitial_pages_served_as_200_fail_permanently(head):
    with _resolves_to(_PUBLIC_IP), _serve(_Resp(body=_page(head))):
        assert fetch_article_preview("https://news.example/a").status == STATUS_FAILED


def test_a_headline_merely_mentioning_denied_access_is_not_an_interstitial():
    html = _page('<meta property="og:title" content="Journalists access denied to Gaza for a year">')
    with _resolves_to(_PUBLIC_IP), _serve(_Resp(body=html)):
        assert fetch_article_preview("https://news.example/a").status == STATUS_OK
