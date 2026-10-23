"""Geometry regressions measured in real Tk, including high-DPI text metrics."""
from __future__ import annotations

import gc
from pathlib import Path
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk
import unittest

from lume.ui import AddDialog, App, ConfirmationDialog, SettingsDialog, UpdateDialog
from tests.test_ui import FixtureManager


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def contrast(foreground, background):
    def luminance(color):
        values = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [part / 12.92 if part <= .04045 else ((part + .055) / 1.055) ** 2.4 for part in values]
        return sum(component * weight for component, weight in zip(linear, (.2126, .7152, .0722)))
    light, dark = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (light + .05) / (dark + .05)


class ResponsiveLayoutTests(unittest.TestCase):
    def test_dialog_actions_stay_outside_the_scrollable_form(self):
        cases = ((AddDialog, "760x590", {"Cancelar", "Baixar agora", "Só adicionar à fila"}),
                 (ConfirmationDialog, "760x590", {"Cancelar", "Baixar agora", "Só adicionar à fila"}),
                 (SettingsDialog, "510x300", {"Cancelar", "Salvar preferências"}),
                 (UpdateDialog, "680x460", {"Fechar", "Verificar e preparar", "Abrir pasta preparada"}))
        for scaling in (120 / 72, 144 / 72):
            for kind, geometry, labels in cases:
                with self.subTest(scaling=scaling, dialog=kind.__name__):
                    root = tk.Tk()
                    root.withdraw()
                    root.tk.call("tk", "scaling", scaling)
                    root.attributes("-alpha", 0.0)
                    app = App(root, FixtureManager(Path.cwd()))
                    root.deiconify()
                    root.update()
                    request = {"id": "layout-request", "url": "https://example.org/video.mp4", "filename": "video.mp4",
                               "directory": str(Path.cwd()), "parts": 4}
                    dialog = kind(app, request) if kind is ConfirmationDialog else kind(app)
                    dialog.attributes("-alpha", 0.0)
                    dialog.geometry(geometry)
                    dialog.update()
                    buttons = [widget for widget in descendants(dialog)
                               if isinstance(widget, ttk.Button) and str(widget.cget("text")) in labels]
                    try:
                        self.assertEqual({str(button.cget("text")) for button in buttons}, labels)
                        for button in buttons:
                            label = str(button.cget("text"))
                            self.assertTrue(button.winfo_ismapped(), label)
                            self.assertGreaterEqual(button.winfo_height(), button.winfo_reqheight(), label)
                            self.assertGreaterEqual(button.winfo_width(), button.winfo_reqwidth(), label)
                            bottom = button.winfo_rooty() - dialog.winfo_rooty() + button.winfo_height()
                            right = button.winfo_rootx() - dialog.winfo_rootx() + button.winfo_width()
                            self.assertLessEqual(bottom, dialog.winfo_height(), label)
                            self.assertLessEqual(right, dialog.winfo_width(), label)
                    finally:
                        dialog.destroy()
                        root.destroy()
                        dialog = None
                        app = None
                        root = None
                        buttons = None
                        gc.collect()

    def test_actions_and_footer_keep_their_requested_text_height(self):
        # Tk scaling is pixels per point: 120 DPI / 72 = 125% and 144 / 72 = 150%.
        for scaling in (120 / 72, 144 / 72):
            for geometry in ("980x620", "1160x740"):
                with self.subTest(scaling=scaling, geometry=geometry):
                    root = tk.Tk()
                    root.withdraw()
                    root.tk.call("tk", "scaling", scaling)
                    root.attributes("-alpha", 0.0)
                    app = App(root, FixtureManager(Path.cwd()))
                    app.manager.add("https://example.org/file.bin", Path.cwd())
                    app.manager.tasks[0].update(status="failed", error="Falha de rede; as partes foram preservadas. " * 12)
                    app.refresh()
                    app.tree.selection_set("1")
                    app.update_details()
                    root.geometry(geometry)
                    root.deiconify()
                    try:
                        root.update()
                        root.update_idletasks()
                        controls = [widget for widget in descendants(root) if isinstance(widget, ttk.Button)]
                        self.assertEqual({str(widget.cget("text")) for widget in controls},
                                         {"＋ Novo download", "Navegadores", "Preferências", "Confirmar…", "Pausar", "Continuar", "Reiniciar", "Cancelar", "Remover", "Abrir pasta", "Detalhes", "Limpar filtros"})
                        for button in controls:
                            text = str(button.cget("text"))
                            label_font = tkfont.Font(root=root, font=ttk.Style(root).lookup(button.cget("style") or "TButton", "font"))
                            self.assertGreaterEqual(button.winfo_height(), label_font.metrics("linespace") + 8, text)
                            self.assertGreaterEqual(button.winfo_height(), button.winfo_reqheight(), text)
                            bottom = button.winfo_rooty() - root.winfo_rooty() + button.winfo_height()
                            right = button.winfo_rootx() - root.winfo_rootx() + button.winfo_width()
                            self.assertLessEqual(bottom, root.winfo_height(), text)
                            self.assertLessEqual(right, root.winfo_width(), text)
                            self.assertTrue(button.winfo_ismapped(), text)
                        self.assertGreaterEqual(app.tree.winfo_height(), label_font.metrics("linespace") * 2)
                        first_row = app.tree.bbox("1")
                        self.assertTrue(first_row, "The table has no visible first row")
                        self.assertLessEqual(first_row[1] + first_row[3], app.tree.winfo_height())
                    finally:
                        root.destroy()
                        app = None
                        root = None
                        controls = None
                        label_font = None
                        gc.collect()

    def test_normal_and_disabled_button_labels_have_readable_contrast(self):
        root = tk.Tk()
        root.withdraw()
        app = App(root, FixtureManager(Path.cwd()))
        try:
            style = ttk.Style(root)
            for name in ("TButton", "Accent.TButton"):
                for state in ((), ("disabled",)):
                    with self.subTest(style=name, state=state):
                        fg = style.lookup(name, "foreground", state)
                        bg = style.lookup(name, "background", state)
                        fg = "#" + "".join(f"{component // 257:02x}" for component in root.winfo_rgb(fg))
                        bg = "#" + "".join(f"{component // 257:02x}" for component in root.winfo_rgb(bg))
                        self.assertGreaterEqual(contrast(fg, bg), 4.5)
        finally:
            root.destroy()
            app = None
            root = None
            gc.collect()


if __name__ == "__main__":
    unittest.main()
