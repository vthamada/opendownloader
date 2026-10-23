"""Actual Tk assistant with simulated native registration; not browser UI proof."""
import json
import gc
from pathlib import Path
from types import SimpleNamespace
import tempfile
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch
from lume.setup_ui import IntegrationDialog
from lume.distribution import load_distribution
from lume.browser_setup import BrowserLaunchResult


class IntegrationAssistantTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.bundle=Path(self.temp.name)
        self.root=tk.Tk();self.root.withdraw()
        ttk.Style(self.root).theme_use('clam')
        self.preview=patch('lume.registration.plan_registration',return_value=SimpleNamespace(changed=False))
        self.discovery=patch('lume.registration.discover_owned_manifest_directory',return_value=None)
        self.preview.start();self.discovery.start()
    def tearDown(self):
        self.root.destroy();self.preview.stop();self.discovery.stop();self.temp.cleanup()
        # Tk reference cycles must be collected on the owning GUI thread before
        # later transfer-worker tests can trigger cyclic GC from another thread.
        self.root = None
        gc.collect()
    def test_unpublished_stores_are_explicit_and_never_open_invented_urls(self):
        dialog=IntegrationDialog(self.root,bundle=self.bundle)
        self.assertTrue(all(button.instate(['disabled']) for button in dialog.store_buttons.values()))
        self.assertIn('pronta',dialog.bridge_status.get())
        self.assertIn('não comprova',dialog.notice.get())
        with patch('lume.setup_ui.webbrowser.open') as opened:
            dialog.open_store('chrome');opened.assert_not_called()
        dialog.copy_extension()
        self.assertEqual(dialog.clipboard_get(),str(self.bundle/'browser-extension'))
    def test_controls_fit_and_connection_action_is_explicit(self):
        with patch('lume.registration.plan_registration',return_value=SimpleNamespace(changed=True)), \
             patch('lume.registration.apply_registration') as apply:
            dialog=IntegrationDialog(self.root,bundle=self.bundle)
            self.root.update()
            apply.assert_not_called()
            dialog.configure_bridge();apply.assert_called_once()
            self.assertIn('salva',dialog.notice.get())
            def children(widget):
                for child in widget.winfo_children():yield child;yield from children(child)
            for geometry in ('820x640', '760x600'):
                dialog.geometry(geometry)
                for index in (0, 1):
                    dialog.browser_tabs.select(index)
                    self.root.update()
                    for button in (w for w in children(dialog) if isinstance(w,ttk.Button) and w.winfo_ismapped()):
                        self.assertGreaterEqual(button.winfo_height(),button.winfo_reqheight())
                        self.assertGreaterEqual(button.winfo_width(),button.winfo_reqwidth())
                        self.assertLessEqual(button.winfo_rooty()-dialog.winfo_rooty()+button.winfo_height(),dialog.winfo_height())
                        self.assertGreaterEqual(button.winfo_rootx(),dialog.winfo_rootx())
                        self.assertLessEqual(button.winfo_rootx()-dialog.winfo_rootx()+button.winfo_width(),dialog.winfo_width())

    def test_prepare_copies_path_and_requests_only_selected_browser_without_claiming_connection(self):
        dialog = IntegrationDialog(self.root, bundle=self.bundle)
        result = BrowserLaunchResult('edge', 'edge://extensions/', Path('C:/test/msedge.exe'), True)
        with patch('lume.setup_ui.open_extensions', return_value=result) as opened, \
             patch('lume.registration.apply_registration') as apply:
            dialog.prepare_browser('edge')
            opened.assert_called_once_with('edge')
            apply.assert_not_called()
        self.assertEqual(dialog.browser_tabs.select(), str(dialog.browser_frames['edge']))
        self.assertEqual(dialog.clipboard_get(), str(self.bundle / 'browser-extension'))
        self.assertIn('Pasta copiada', dialog.browser_progress['edge'].get())
        self.assertIn('solicitada', dialog.browser_progress['edge'].get())
        self.assertIn('ainda precisam', dialog.browser_progress['edge'].get())
        self.assertIn('Aguardando', dialog.browser_progress['chrome'].get())
        self.assertTrue(dialog.configure_button.instate(['disabled']))

    def test_browser_identity_survives_reordered_tabs(self):
        dialog = IntegrationDialog(self.root, bundle=self.bundle)
        dialog.browser_tabs.insert(0, dialog.browser_frames['edge'])
        result = BrowserLaunchResult('edge', 'edge://extensions/', Path('C:/test/msedge.exe'), True)
        with patch('lume.setup_ui.open_extensions', return_value=result) as opened:
            dialog.prepare_browser('edge')
            opened.assert_called_once_with('edge')
        self.assertEqual(dialog.browser_tabs.select(), str(dialog.browser_frames['edge']))
        self.assertTrue(dialog.local_progress['edge']['copied'])
        self.assertFalse(dialog.local_progress['chrome']['copied'])

    def test_failed_launch_offers_exact_manual_url_and_copy_action(self):
        dialog = IntegrationDialog(self.root, bundle=self.bundle)
        result = BrowserLaunchResult('chrome', 'chrome://extensions/', None, False, 'Navegador não encontrado.')
        with patch('lume.setup_ui.open_extensions', return_value=result):
            dialog.open_browser_extensions('chrome')
        self.assertIn('cole na barra: chrome://extensions/', dialog.notice.get())
        self.assertFalse(dialog.local_progress['chrome']['requested'])
        dialog.copy_extensions_url('chrome')
        self.assertEqual(dialog.clipboard_get(), 'chrome://extensions/')
        self.assertIn('Endereço copiado', dialog.notice.get())

    def test_readonly_path_and_steps_exist_for_each_browser(self):
        dialog = IntegrationDialog(self.root, bundle=self.bundle)
        self.assertEqual(dialog.extension_path.get(), str(self.bundle / 'browser-extension'))
        for tab in dialog.browser_tabs.tabs():
            card = dialog.nametowidget(tab)
            labels = [str(widget.cget('text')) for widget in card.winfo_children() if isinstance(widget, ttk.Label)]
            self.assertTrue(any('Modo do desenvolvedor' in label for label in labels))
            self.assertTrue(any('Verificar conexão' in label for label in labels))
            rows = [widget for widget in card.winfo_children() if isinstance(widget, ttk.Frame)]
            entries = [entry for row in rows for entry in row.winfo_children() if isinstance(entry, ttk.Entry)]
            self.assertEqual(len(entries), 1)
            self.assertTrue(entries[0].instate(['readonly']))
    def test_only_published_https_store_links_can_enable_actions(self):
        path=self.bundle/'browser-distribution.json'
        for url in ('http://chromewebstore.google.com/detail/test','https://evil.test/detail/test',
                    'https://user:pass@chromewebstore.google.com/detail/test'):
            path.write_text(json.dumps({'chrome':{'published':True,'url':url}}))
            with self.assertRaises(ValueError):load_distribution(path)
        url='https://chromewebstore.google.com/detail/lume/abcdefghijklmnopabcdefghijklmnop'
        path.write_text(json.dumps({'chrome':{'published':True,'url':url,'extension_id':'abcdefghijklmnopabcdefghijklmnop'}}))
        self.assertEqual(load_distribution(path).links['chrome'],url)
        path.write_text(json.dumps({'chrome':{'published':True,'url':url,'extension_id':'a'*32}}))
        with self.assertRaises(ValueError):load_distribution(path)
