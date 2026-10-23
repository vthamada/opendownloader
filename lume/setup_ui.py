"""One local browser-integration assistant; never modifies browser profiles/policies."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tkinter as tk
from tkinter import ttk
import webbrowser

from . import VERSION
from .maintenance import DEFAULT_EXTENSION_ID
from .ui import OwnedWindow
from .distribution import BrowserDistribution, load_distribution
from .browser_setup import extensions_url, open_extensions


def application_bundle() -> Path:
    from .runtime_paths import application_directory
    root = application_directory()
    return root if getattr(sys, 'frozen', False) else root / 'dist' / 'LumeDownloads'


class IntegrationDialog(OwnedWindow):
    def __init__(self, parent, *, bundle: Path | None = None, installation_root: Path | None = None):
        super().__init__(parent)
        self.bundle = Path(bundle or application_bundle()).resolve()
        self.host = self.bundle / 'LumeNativeHost.exe'
        self.extension = self.bundle / 'browser-extension'
        root = installation_root
        if root is None and (self.bundle.parent / 'installation.json').is_file():
            root = self.bundle.parent
        if root is not None:
            from .deployment import installation_paths
            self.manifest_directory = installation_paths(Path(root))['native_directory']
        else:
            self.manifest_directory = self.bundle / 'BrowserHost'
        self.catalog_error = None
        try:
            self.distribution = load_distribution(self.bundle/'browser-distribution.json', allow_missing=True)
        except (OSError, ValueError) as error:
            self.catalog_error = str(error)
            self.distribution = BrowserDistribution(())
        self.links = self.distribution.links
        self.title('Conectar navegadores • OpenDownloader ' + VERSION)
        self.geometry('820x640')
        self.minsize(760, 600)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.protocol('WM_DELETE_WINDOW', self.destroy)
        self.bind('<Escape>', lambda _event: self.destroy())
        body = ttk.Frame(self, padding=18)
        body.grid(sticky='nsew')
        body.columnconfigure(0, weight=1)
        body.rowconfigure(3, weight=1)
        ttk.Label(body, text='Conecte seus navegadores.', font=('Segoe UI Semibold', 18)).grid(sticky='w')
        ttk.Label(body, text='Faça a configuração local uma vez em cada navegador, seguindo os passos abaixo.',
                  wraplength=740).grid(sticky='w', pady=(5, 10))
        bridge = ttk.LabelFrame(body, text='Conexão com o aplicativo', padding=10)
        bridge.grid(sticky='ew', pady=(0, 10))
        bridge.columnconfigure(0, weight=1)
        self.bridge_status = tk.StringVar(value='Conferindo a configuração local…')
        ttk.Label(bridge, textvariable=self.bridge_status, wraplength=560).grid(row=0, column=0, sticky='w')
        self.configure_button = ttk.Button(bridge, text='Reparar conexão', command=self.configure_bridge)
        self.configure_button.grid(row=0, column=1, padx=(10, 0))
        self.browser_tabs = ttk.Notebook(body)
        self.browser_tabs.grid(sticky='nsew', pady=(0, 10))
        self.store_buttons = {}
        self.browser_frames = {}
        self.browser_by_tab = {}
        self.browser_progress = {}
        self.local_progress = {browser: {'copied': False, 'requested': False} for browser in ('chrome', 'edge')}
        self.extension_path = tk.StringVar(value=str(self.extension))
        for key, name in (('chrome', 'Google Chrome'), ('edge', 'Microsoft Edge')):
            card = ttk.Frame(self.browser_tabs, padding=14)
            card.columnconfigure(0, weight=1)
            self.browser_tabs.add(card, text=name)
            self.browser_frames[key] = card
            self.browser_by_tab[str(card)] = key
            ttk.Label(card, text='1. Prepare o navegador', font=('Segoe UI Semibold', 11)).grid(sticky='w')
            ttk.Label(card, text='O botão copia a pasta da extensão e solicita a abertura do gerenciador no ' + name + '.',
                      wraplength=700).grid(sticky='w', pady=(4, 7))
            actions = ttk.Frame(card)
            actions.grid(sticky='ew', pady=(0, 10))
            ttk.Button(actions, text='Preparar ' + ('Chrome' if key == 'chrome' else 'Edge'),
                       command=lambda b=key: self.prepare_browser(b)).pack(side='left')
            ttk.Button(actions, text='Abrir extensões do ' + ('Chrome' if key == 'chrome' else 'Edge'),
                       command=lambda b=key: self.open_browser_extensions(b)).pack(side='left', padx=8)
            ttk.Button(actions, text='Copiar endereço',
                       command=lambda b=key: self.copy_extensions_url(b)).pack(side='left')
            ttk.Label(card, text='2. Carregue a extensão uma vez', font=('Segoe UI Semibold', 11)).grid(sticky='w')
            ttk.Label(card, text='Ative “Modo do desenvolvedor”, escolha “Carregar sem compactação” '
                      'e cole a pasta abaixo no seletor. Se já carregou, use Recarregar na extensão.',
                      wraplength=700).grid(sticky='w', pady=(4, 7))
            path_row = ttk.Frame(card)
            path_row.grid(sticky='ew', pady=(0, 8))
            path_row.columnconfigure(0, weight=1)
            ttk.Entry(path_row, textvariable=self.extension_path, state='readonly').grid(row=0, column=0, sticky='ew')
            ttk.Button(path_row, text='Copiar pasta', command=self.copy_extension).grid(row=0, column=1, padx=(8, 0))
            ttk.Button(path_row, text='Abrir pasta', command=self.open_extension).grid(row=0, column=2, padx=(8, 0))
            ttk.Label(card, text='3. Verifique no navegador', font=('Segoe UI Semibold', 11)).grid(sticky='w')
            ttk.Label(card, text='Abra o ícone do OpenDownloader e use “Verificar conexão com o aplicativo”. '
                      'Depois envie um link e confirme o download no aplicativo.',
                      wraplength=700).grid(sticky='w', pady=(4, 8))
            progress = tk.StringVar(value='Aguardando sua configuração neste navegador.')
            self.browser_progress[key] = progress
            ttk.Label(card, textvariable=progress, wraplength=700).grid(sticky='w')
            button = ttk.Button(card, text='Adicionar pela loja', command=lambda b=key: self.open_store(b))
            if self.links[key] is None:
                button.state(['disabled'])
            else:
                button.grid(sticky='w', pady=(8, 0))
            self.store_buttons[key] = button
        self.notice = tk.StringVar(value='A ponte configurada não comprova que a extensão está instalada ou ativa.')
        ttk.Label(body, textvariable=self.notice, wraplength=750).grid(sticky='w', pady=(0, 8))
        ttk.Button(body, text='Fechar', command=self.destroy).grid(sticky='e')
        self.refresh_bridge()

    def _plan(self):
        if self.catalog_error:
            raise ValueError('Metadados das lojas inválidos: '+self.catalog_error)
        from .registration import plan_registration, discover_owned_manifest_directory
        previous = discover_owned_manifest_directory()
        return plan_registration(self.host, DEFAULT_EXTENSION_ID, self.manifest_directory,
                                 previous if previous != self.manifest_directory else None,
                                 additional_extension_ids=self.distribution.extension_ids)

    def refresh_bridge(self):
        try:
            plan = self._plan()
            self.bridge_status.set('Ponte local pronta para Chrome e Edge.' if not plan.changed else 'A ponte local precisa ser configurada para esta pasta do OpenDownloader.')
            self.configure_button.state(['!disabled'] if plan.changed else ['disabled'])
        except (OSError, ValueError, RuntimeError) as error:
            self.bridge_status.set('Não foi possível conferir a ponte: ' + str(error))
            self.configure_button.state(['disabled'])

    def configure_bridge(self):
        from .registration import apply_registration
        try:
            apply_registration(self._plan())
            self.notice.set('Configuração salva para seu usuário. Confira a conexão no popup da extensão.')
            self.refresh_bridge()
        except (OSError, ValueError, RuntimeError) as error:
            self.notice.set('A conexão não foi alterada completamente: ' + str(error))

    def open_store(self, browser):
        if self.links.get(browser):
            webbrowser.open(self.links[browser])

    def copy_extension(self):
        self.clipboard_clear()
        self.clipboard_append(str(self.extension))
        browser = self.browser_by_tab[self.browser_tabs.select()]
        self.local_progress[browser]['copied'] = True
        self._refresh_progress(browser)
        self.notice.set('Pasta copiada. Cole no seletor Carregar sem compactação do navegador.')

    def _refresh_progress(self, browser):
        state = self.local_progress[browser]
        facts = []
        if state['copied']:
            facts.append('Pasta copiada')
        if state['requested']:
            facts.append('abertura do gerenciador solicitada')
        summary = '; '.join(facts) if facts else 'Abertura do gerenciador não solicitada'
        self.browser_progress[browser].set(summary + '. Instalação e conexão ainda precisam ser verificadas no navegador.')

    def prepare_browser(self, browser):
        self.browser_tabs.select(self.browser_frames[browser])
        self.copy_extension()
        self.open_browser_extensions(browser)

    def open_browser_extensions(self, browser):
        result = open_extensions(browser)
        self.local_progress[browser]['requested'] = result.requested
        self._refresh_progress(browser)
        if result.requested:
            self.notice.set('Abertura solicitada. Se o gerenciador não aparecer, copie o endereço e cole na barra do ' +
                            ('Chrome' if browser == 'chrome' else 'Edge') + ': ' + result.url)
        else:
            self.notice.set((result.reason or 'Não foi possível solicitar a abertura.') +
                            ' Abra o navegador e cole na barra: ' + result.url)

    def copy_extensions_url(self, browser):
        url = extensions_url(browser)
        self.clipboard_clear()
        self.clipboard_append(url)
        self.notice.set('Endereço copiado: ' + url + ' — cole na barra do navegador correspondente.')

    def open_extension(self):
        if not self.extension.is_dir():
            self.notice.set('A pasta da extensão não foi encontrada no pacote instalado.')
            return
        try:
            os.startfile(self.extension)
        except OSError as error:
            self.notice.set('Não foi possível abrir a pasta: ' + str(error))


def run_integration_center(installation_root: Path | None = None) -> int:
    root = tk.Tk()
    root.withdraw()
    ttk.Style(root).theme_use('clam')
    window = IntegrationDialog(root, installation_root=installation_root)
    window.bind('<Destroy>', lambda event: root.quit() if event.widget is window else None, add='+')
    try:
        root.mainloop()
    finally:
        root.destroy()
    return 0
