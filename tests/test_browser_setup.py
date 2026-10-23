"""Read-only browser discovery and bounded argument-list launch contract."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lume.browser_setup import find_browser, open_extensions


class BrowserSetupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def browser(self, name='chrome.exe'):
        path = self.root / 'pasta com espaços' / name
        path.parent.mkdir(exist_ok=True)
        path.touch()
        return path

    def test_registry_executable_is_literal_and_must_be_regular_expected_file(self):
        browser = self.browser()
        candidates = [str(browser) + ' --unsafe', str(browser.parent), 'chrome.exe',
                      str(self.root / 'missing' / 'chrome.exe'), str(self.browser('other.exe')),
                      '"' + str(browser) + '"']
        with patch('lume.browser_setup._registry_paths', return_value=candidates), \
             patch('lume.browser_setup._known_paths', return_value=[]):
            self.assertEqual(find_browser('chrome'), browser.resolve())

    def test_known_paths_fallback_and_browser_identity_are_distinct(self):
        edge = self.browser('msedge.exe')
        with patch('lume.browser_setup._registry_paths', return_value=[]), \
             patch('lume.browser_setup._known_paths', return_value=[edge]):
            self.assertIsNone(find_browser('chrome'))
            self.assertEqual(find_browser('edge'), edge.resolve())

    def test_launch_uses_exact_browser_and_fixed_internal_url_without_shell(self):
        for browser, executable, url in (
                ('chrome', 'chrome.exe', 'chrome://extensions/'),
                ('edge', 'msedge.exe', 'edge://extensions/')):
            path = self.browser(executable)
            with self.subTest(browser=browser), \
                 patch('lume.browser_setup.find_browser', return_value=path), \
                 patch('lume.browser_setup.subprocess.Popen') as launch:
                result = open_extensions(browser)
                self.assertTrue(result.requested)
                self.assertEqual(result.url, url)
                self.assertEqual(result.executable, path)
                self.assertEqual(launch.call_args.args, ([str(path), url],))
                self.assertIs(launch.call_args.kwargs['shell'], False)

    def test_missing_or_failed_browser_returns_manual_reason_without_launch_claim(self):
        with patch('lume.browser_setup.find_browser', return_value=None), \
             patch('lume.browser_setup.subprocess.Popen') as launch:
            result = open_extensions('chrome')
            self.assertFalse(result.requested)
            self.assertIn('não encontrado', result.reason)
            launch.assert_not_called()
        with patch('lume.browser_setup.find_browser', return_value=self.browser()), \
             patch('lume.browser_setup.subprocess.Popen', side_effect=OSError('denied')):
            result = open_extensions('chrome')
            self.assertFalse(result.requested)
            self.assertIn('denied', result.reason)

    def test_unknown_browser_cannot_select_executable_or_url(self):
        for value in ('brave', 'chrome --args', None, [], {}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                find_browser(value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                open_extensions(value)


if __name__ == '__main__':
    unittest.main()
