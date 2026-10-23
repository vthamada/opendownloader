"""Trusted store metadata is parsed once, without claiming live publication."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from lume.distribution import load_distribution, parse_distribution


CHROME_ID = 'abcdefghijklmnopabcdefghijklmnop'
EDGE_ID = 'ponmlkjihgfedcbaponmlkjihgfedcba'


def published(browser='chrome', *, slug=True):
    extension_id = CHROME_ID if browser == 'chrome' else EDGE_ID
    base = ('https://chromewebstore.google.com/detail/' if browser == 'chrome'
            else 'https://microsoftedge.microsoft.com/addons/detail/')
    return {'published': True, 'extension_id': extension_id,
            'url': base + ('open-downloader/' if slug else '') + extension_id}


class DistributionTests(unittest.TestCase):
    def test_current_unpublished_shape_and_omissions_are_empty(self):
        for raw in ({}, {'chrome': {'published': False, 'url': None}},
                    {'chrome': {'published': False, 'url': None},
                     'edge': {'published': False, 'url': None, 'extension_id': None}}):
            with self.subTest(raw=raw):
                catalog = parse_distribution(raw)
                self.assertEqual(catalog.published_entries, ())
                self.assertEqual(catalog.extension_ids, ())
                self.assertEqual(catalog.links, {'chrome': None, 'edge': None})

    def test_both_store_paths_and_fixed_update_urls(self):
        for slug in (True, False):
            raw = {'chrome': published(slug=slug), 'edge': published('edge', slug=slug)}
            catalog = parse_distribution(raw)
            self.assertEqual(catalog.extension_ids, (CHROME_ID, EDGE_ID))
            chrome, edge = catalog.published_entries
            self.assertEqual(chrome.browser, 'chrome')
            self.assertEqual(chrome.store_url, raw['chrome']['url'])
            self.assertEqual(chrome.update_url, 'https://clients2.google.com/service/update2/crx')
            self.assertEqual(edge.update_url, 'https://edge.microsoft.com/extensionwebstorebase/v1/crx')
            self.assertEqual(catalog.links, {key: value['url'] for key, value in raw.items()})

    def test_raw_and_entry_types_are_strict(self):
        for raw in (None, [], 'chrome', 0, True, {'chrome': None}, {'chrome': []},
                    {'chrome': {'url': None}}, {'chrome': {'published': 1}},
                    {'chrome': {'published': 'true'}}, {'chrome': {'published': None}},
                    {'chrome': {'published': False}}, {'firefox': {}},
                    {'chrome': {'published': False, 'url': None, 'extra': True}}):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_distribution(raw)

    def test_unpublished_metadata_cannot_hide_conflicting_ids_or_urls(self):
        for field, value in (('url', ''), ('url', published()['url']),
                             ('extension_id', CHROME_ID), ('extension_id', 0)):
            raw = {'published': False, 'url': None}
            raw[field] = value
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_distribution({'chrome': raw})

    def test_published_requires_valid_id_and_url(self):
        for field, value in (('extension_id', None), ('extension_id', True),
                             ('extension_id', 'a' * 31), ('extension_id', 'q' * 32),
                             ('extension_id', 'A' * 32), ('url', None), ('url', []),
                             ('url', ''), ('url', 2)):
            entry = published()
            entry[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                parse_distribution({'chrome': entry})
        for field in ('url', 'extension_id'):
            entry = published()
            del entry[field]
            with self.subTest(missing=field), self.assertRaises(ValueError):
                parse_distribution({'chrome': entry})

    def test_store_url_rejects_redirects_credentials_nonstore_paths_and_ambiguity(self):
        good = published()['url']
        cases = [good.replace('https:', 'http:'), good.replace('https:', 'javascript:'),
                 good.replace('chromewebstore.google.com', 'chromewebstore.google.com.evil.test'),
                 good.replace('chromewebstore.google.com', 'chromewebstore.google.com.'),
                 good.replace('https://', 'https://user@'),
                 good.replace('https://', 'https://user:password@'),
                 good.replace('.com/', '.com:444/'), good.replace('.com/', '.com:bad/'),
                 good + '?', good + '#', good + '?next=https://evil.test', good + '#id',
                 good + '/', good.replace('/detail/', '/other/'),
                 good.replace('/open-downloader/', '/extra/open-downloader/'),
                 good.replace('/open-downloader/', '//'),
                 good.replace(CHROME_ID, EDGE_ID),
                 good.replace('/open-downloader/', '/%2f/'),
                 good.replace('/open-downloader/', '/../'),
                 good.replace('https://', ' https://'), good + '\n',
                 good.replace('/open-downloader/', '/open\\downloader/'),
                 published('edge')['url']]
        for url in cases:
            entry = published()
            entry['url'] = url
            with self.subTest(url=url), self.assertRaises(ValueError):
                parse_distribution({'chrome': entry})

    def test_explicit_tls_port_is_allowed_and_input_not_modified(self):
        raw = {'chrome': published()}
        raw['chrome']['url'] = raw['chrome']['url'].replace('.com/', '.com:443/')
        previous = copy.deepcopy(raw)
        catalog = parse_distribution(raw)
        self.assertEqual(raw, previous)
        self.assertEqual(catalog.links['chrome'], raw['chrome']['url'])
        links = catalog.links
        links['chrome'] = 'modified only this copy'
        self.assertEqual(catalog.links['chrome'], raw['chrome']['url'])

    def test_invalid_second_browser_never_returns_partial_publication(self):
        with self.assertRaisesRegex(ValueError, 'edge'):
            parse_distribution({'chrome': published(), 'edge': {'published': True, 'url': None}})

    def test_loader_missing_is_only_optional_by_explicit_choice(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'browser-distribution.json'
            with self.assertRaises(FileNotFoundError):
                load_distribution(path)
            self.assertEqual(load_distribution(path, allow_missing=True).extension_ids, ())
            path.mkdir()
            with self.assertRaises(OSError):
                load_distribution(path, allow_missing=True)

    def test_loader_checks_utf8_json_and_duplicate_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'browser-distribution.json'
            path.write_text(json.dumps({'chrome': published()}), encoding='utf-8')
            self.assertEqual(load_distribution(path).extension_ids, (CHROME_ID,))
            cases = [b'not json', b'\xff', b'{"chrome":{},"chrome":{}}',
                     b'{"chrome":{"published":false,"published":true,"url":null}}',
                     b'{"chrome":{"published":false,"url":null},"other":NaN}']
            for content in cases:
                path.write_bytes(content)
                with self.subTest(content=content), self.assertRaises(ValueError):
                    load_distribution(path)


if __name__ == '__main__':
    unittest.main()
