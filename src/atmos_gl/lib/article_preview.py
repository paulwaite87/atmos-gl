#!/usr/bin/env python3
"""Headline + summary extraction for a World Events source article (pure network +
parsing, no DB) -- WorldEventsCollector calls fetch_article_preview() per distinct
source URL and stores the result; see db/world_event_adapter.py.

What's extracted is the publisher's own metadata, not a generated summary: og:title
(falling back to twitter:title, then <title>) and og:description (falling back to
twitter:description, then <meta name="description">). Sampled live against 25 GDELT
source URLs: 22 had og:title/description (typically the article's lede), 2 served a
Cloudflare challenge page (403, whose <title> is "Attention Required! | Cloudflare"),
1 a 429. Hence only a 2xx text/html response is ever parsed -- a block page's title
must never be stored as a headline.

GDELT source URLs are arbitrary third-party links fetched from inside the deployment's
network, so every hop (redirects are followed manually) must resolve only to public
addresses (_assert_public_url), http/https only, with a capped read size and timeout.
DNS is re-resolved by requests after the check, so a rebinding attacker could still
race it; accepted for this use (the response is only ever parsed for two meta tags,
never echoed back raw), not a general-purpose SSRF boundary.
"""
import ipaddress
import re
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

from atmos_gl.lib.text_sanitize import strip_html

_USER_AGENT = "Mozilla/5.0 (compatible; AtmosGL/1.0; +https://github.com/paulwaite87/atmos-gl)"
_TIMEOUT_S = 10
_MAX_BYTES = 512 * 1024  # og:/twitter: metadata lives in <head>; pages run to several MB
_MAX_REDIRECTS = 5
_HEADLINE_MAX = 300
_SUMMARY_MAX = 400  # ~8 lines in the 320px popup; publisher ledes are mostly 150-300

# Interstitials some publishers serve as a 200 (often after redirecting the article URL
# to e.g. /nocookies or /unavailable-location/) -- their metadata describes the block
# page, not the article. Matched case-insensitively against headline and summary.
# Every entry was seen live or is a bot wall's distinctive wording -- deliberately not
# phrases a real headline could contain ("access denied" to Gaza, "just a moment" ...);
# those walls come back as 403/503 anyway, which is already a failure.
_INTERSTITIAL_PHRASES = (
    "no cookies",
    "unavailable in your location",
    "not available in your region",
    "attention required",
    "are you a robot",
    "enable javascript",
    "page not found",
)
_WORD_RE = re.compile(r"[a-z0-9]+")
# A slug this long reliably names the article; shorter ones (ids, "news", "story")
# don't carry enough signal to second-guess og:title with.
_MIN_SLUG_WORDS = 4

STATUS_OK = "ok"
STATUS_FAILED = "failed"  # permanent: don't retry
STATUS_RETRY = "retry"  # transient: retry later


@dataclass
class ArticlePreview:
    status: str
    http_status: int | None = None
    headline: str | None = None
    summary: str | None = None


class UnsafeUrl(Exception):
    """Not http(s), or resolves to a non-public address."""


def _assert_public_url(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise UnsafeUrl(url)
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or None)
    except socket.gaierror as e:
        raise requests.ConnectionError(f"DNS lookup failed for {parts.hostname}") from e
    for info in infos:
        if not ipaddress.ip_address(info[4][0]).is_global:
            raise UnsafeUrl(url)


def _fetch_html(url: str) -> tuple[int, bytes | None]:
    """(final HTTP status, body) -- body is None unless the final response is a 2xx
    text/html one. Raises UnsafeUrl or requests.RequestException."""
    for _ in range(_MAX_REDIRECTS + 1):
        _assert_public_url(url)
        with requests.get(
            url,
            stream=True,
            timeout=_TIMEOUT_S,
            allow_redirects=False,
            headers={"User-Agent": _USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
        ) as r:
            if r.is_redirect:
                url = urljoin(url, r.headers.get("location", ""))
                continue
            if not 200 <= r.status_code < 300:
                return r.status_code, None
            content_type = r.headers.get("content-type", "").lower()
            if "html" not in content_type:
                return r.status_code, None
            body = b""
            for chunk in r.iter_content(chunk_size=64 * 1024):
                body += chunk
                if len(body) >= _MAX_BYTES:
                    break
            return r.status_code, body[:_MAX_BYTES]
    raise requests.TooManyRedirects(url)


def _meta_content(soup: BeautifulSoup, *keys: str) -> str | None:
    for key in keys:
        tag = soup.find("meta", attrs={"property": key}) or soup.find("meta", attrs={"name": key})
        if tag:
            text = strip_html(tag.get("content"))
            if text:
                return text
    return None


def _truncate(text: str | None, limit: int) -> str | None:
    if not text or len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return f"{cut}…"


def _slug_words(url: str | None) -> set[str]:
    """Words of the URL's last descriptive path segment (e.g. "un-expert-calls-for-
    accountability-..."), or an empty set when there's no usable slug."""
    if not url:
        return set()
    segments = [s for s in urlsplit(url).path.split("/") if "-" in s or "_" in s]
    if not segments:
        return set()
    words = {w for w in _WORD_RE.findall(segments[-1].lower()) if len(w) >= 3 and not w.isdigit()}
    return words if len(words) >= _MIN_SLUG_WORDS else set()


def _matches_slug(text: str | None, slug: set[str]) -> bool:
    if not text:
        return False
    overlap = slug & set(_WORD_RE.findall(text.lower()))
    return len(overlap) >= min(3, (len(slug) + 1) // 2)


def extract_preview(html: bytes, url: str | None = None) -> tuple[str | None, str | None]:
    """(headline, summary) from an HTML document's metadata; either may be None. A
    summary that merely repeats the headline is dropped.

    When `url` has a descriptive slug and og:title shares almost none of its words, the
    site is serving one SITE-WIDE og:/twitter: title on every page (seen live: Ariana
    News's og:title is "Afghanistan News Today – Breaking News..." on every article).
    Then the first of twitter:title / <h1> / <title> that does match the slug wins, and
    the summary comes from the plain description tag (og:description is site-wide too
    in that case). If nothing matches the slug -- e.g. a transliterated non-English
    slug -- og:title is kept: this only ever reorders, never discards."""
    soup = BeautifulSoup(html, "lxml")
    title_tag = strip_html(soup.title.get_text()) if soup.title else None
    h1 = soup.find("h1")
    h1_text = strip_html(h1.get_text()) if h1 else None

    og_title = _meta_content(soup, "og:title")
    headline = og_title or _meta_content(soup, "twitter:title") or title_tag
    summary_keys = ("og:description", "twitter:description", "description")

    slug = _slug_words(url)
    if slug and og_title and not _matches_slug(og_title, slug):
        alternative = next(
            (t for t in (_meta_content(soup, "twitter:title"), h1_text, title_tag)
             if _matches_slug(t, slug)),
            None,
        )
        if alternative:
            headline = alternative
            summary_keys = ("description",)

    summary = _meta_content(soup, *summary_keys)
    if summary and headline and summary.casefold() == headline.casefold():
        summary = None
    return _truncate(headline, _HEADLINE_MAX), _truncate(summary, _SUMMARY_MAX)


def _is_interstitial(*texts: str | None) -> bool:
    joined = " ".join(t for t in texts if t).casefold()
    return any(phrase in joined for phrase in _INTERSTITIAL_PHRASES)


def fetch_article_preview(url: str) -> ArticlePreview:
    """Never raises: every outcome maps to ok / failed (permanent) / retry (transient).
    429, 5xx and network errors are transient; other non-2xx, non-HTML, unsafe URLs,
    pages without any headline metadata, and interstitials (_INTERSTITIAL_PHRASES) are
    permanent."""
    try:
        http_status, body = _fetch_html(url)
    except UnsafeUrl:
        return ArticlePreview(STATUS_FAILED)
    except requests.TooManyRedirects:
        return ArticlePreview(STATUS_FAILED)
    except requests.RequestException:
        return ArticlePreview(STATUS_RETRY)

    if body is None:
        transient = http_status == 429 or http_status >= 500
        return ArticlePreview(STATUS_RETRY if transient else STATUS_FAILED, http_status)

    try:
        headline, summary = extract_preview(body, url)
    except Exception:
        return ArticlePreview(STATUS_FAILED, http_status)
    if not headline or _is_interstitial(headline, summary):
        return ArticlePreview(STATUS_FAILED, http_status)
    return ArticlePreview(STATUS_OK, http_status, headline, summary)
