"""Parse trusted release metadata for store distribution.

``published`` is a release declaration, not evidence that a listing is live,
approved, installed, or enabled. This module performs no network or registry I/O.
Consumers use these parsed facts instead of interpreting store URLs themselves.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from urllib.parse import urlsplit


CHROME_UPDATE_URL = 'https://clients2.google.com/service/update2/crx'
EDGE_UPDATE_URL = 'https://edge.microsoft.com/extensionwebstorebase/v1/crx'
_STORES = {
    'chrome': ('chromewebstore.google.com', '/detail/', CHROME_UPDATE_URL),
    'edge': ('microsoftedge.microsoft.com', '/addons/detail/', EDGE_UPDATE_URL),
}


@dataclass(frozen=True)
class PublishedExtension:
    """Store facts declared in a validated release configuration."""

    browser: str
    extension_id: str
    store_url: str
    update_url: str


@dataclass(frozen=True)
class BrowserDistribution:
    entries: tuple[PublishedExtension, ...] = ()

    @property
    def published_entries(self) -> tuple[PublishedExtension, ...]:
        return self.entries

    @property
    def extension_ids(self) -> tuple[str, ...]:
        return tuple(entry.extension_id for entry in self.entries)

    @property
    def links(self) -> dict[str, str | None]:
        result = dict.fromkeys(_STORES)
        result.update((entry.browser, entry.store_url) for entry in self.entries)
        return result


def _store_entry(browser: str, entry: dict) -> PublishedExtension:
    extension_id = entry.get('extension_id')
    if not isinstance(extension_id, str) or re.fullmatch(r'[a-p]{32}', extension_id) is None:
        raise ValueError(f'{browser}.extension_id must contain exactly 32 lowercase letters a-p')
    url = entry.get('url')
    if not isinstance(url, str) or not url or any(ord(char) <= 32 or ord(char) == 127 for char in url):
        raise ValueError(f'{browser}.url must be an official HTTPS store URL without whitespace')
    host, prefix, update_url = _STORES[browser]
    # Reject empty query/fragment delimiters too: urlsplit alone loses their presence.
    if '?' in url or '#' in url or '\\' in url:
        raise ValueError(f'{browser}.url must not contain queries, fragments, or backslashes')
    try:
        parsed = urlsplit(url)
        valid_authority = (parsed.scheme == 'https' and parsed.hostname == host
                           and parsed.netloc in (host, host + ':443')
                           and parsed.username is None and parsed.password is None
                           and parsed.port in (None, 443))
    except ValueError as error:
        raise ValueError(f'{browser}.url has an invalid authority') from error
    # Store paths contain an optional single display slug and the authoritative ID.
    path_pattern = re.escape(prefix) + r'(?:[A-Za-z0-9_-]+/)?' + re.escape(extension_id)
    if not valid_authority or re.fullmatch(path_pattern, parsed.path) is None:
        raise ValueError(f'{browser}.url must use {host}{prefix}[slug/]{extension_id}')
    return PublishedExtension(browser, extension_id, url, update_url)


def parse_distribution(raw: object) -> BrowserDistribution:
    """Parse the complete catalog or raise; malformed entries never yield a partial catalog.

    Omitted browsers are unpublished. A present unpublished entry keeps the legacy
    ``published:false,url:null`` shape and may include ``extension_id:null``.
    """
    if not isinstance(raw, dict):
        raise ValueError('Browser distribution must be an object')
    unknown = set(raw) - _STORES.keys()
    if unknown:
        raise ValueError('Unknown browser distribution keys: ' + ', '.join(map(str, unknown)))
    entries = []
    for browser in _STORES:
        if browser not in raw:
            continue
        entry = raw[browser]
        if not isinstance(entry, dict):
            raise ValueError(f'{browser} distribution must be an object')
        unknown_fields = set(entry) - {'published', 'url', 'extension_id'}
        if unknown_fields:
            raise ValueError(f'{browser} has unknown fields: ' + ', '.join(map(str, unknown_fields)))
        if type(entry.get('published')) is not bool:
            raise ValueError(f'{browser}.published must be a boolean')
        if entry['published']:
            entries.append(_store_entry(browser, entry))
        elif 'url' not in entry or entry['url'] is not None or entry.get('extension_id') is not None:
            raise ValueError(f'{browser} unpublished metadata requires url:null and no extension_id')
    return BrowserDistribution(tuple(entries))


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'Duplicate browser distribution key: {key}')
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError(f'Non-JSON browser distribution value: {value}')


def load_distribution(path: Path, *, allow_missing: bool = False) -> BrowserDistribution:
    """Load one UTF-8 catalog; an absent legacy file is allowed only explicitly.

    Missing files otherwise retain FileNotFoundError; other filesystem failures
    retain OSError. Invalid encoding, JSON, and declared metadata raise ValueError.
    """
    path = Path(path)
    try:
        text = path.read_text(encoding='utf-8')
    except FileNotFoundError:
        if allow_missing:
            return BrowserDistribution()
        raise
    except UnicodeError as error:
        raise ValueError(f'{path}: browser distribution must be UTF-8') from error
    try:
        raw = json.loads(text, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
        return parse_distribution(raw)
    except ValueError as error:
        raise ValueError(f'{path}: {error}') from error
