"""Compact desktop organization through actual Tk widgets and existing actions."""
from types import SimpleNamespace
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

from tests import test_ui


class CompactDesktopTests(unittest.TestCase):
    setUp = test_ui.DesktopWorkflowTests.setUp
    tearDown = test_ui.DesktopWorkflowTests.tearDown

    def seed(self):
        for name, state, size in (("Zebra.mp4", "completed", 300),
                                  ("Audio.m4a", "paused", 100),
                                  ("Notas.pdf", "downloading", 200)):
            self.manager.add("https://example.org/" + name, self.manager.directory, filename=name)
            self.manager.tasks[-1].update(status=state, total=size, downloaded=50)
        self.app.refresh()

    def test_sidebar_uses_typed_filters_counts_and_shared_category_owner(self):
        self.seed()
        category_id = next(identity for identity, value in self.app.navigation_filters.items()
                           if value == ("category", "Áudio"))
        self.assertEqual(self.app.navigation.item(category_id, "values"), ("1",))
        self.app.navigation.selection_set(category_id)
        self.app.select_navigation()
        self.assertEqual(self.app.category_value.get(), "Áudio")
        self.assertEqual(self.app.tree.get_children(), ("2",))
        status_id = next(identity for identity, value in self.app.navigation_filters.items()
                         if value == ("status", "Pausados"))
        self.app.navigation.selection_set(status_id)
        self.app.select_navigation()
        self.assertEqual(self.app.filter_value.get(), "Pausados")
        self.assertEqual(self.app.tree.get_children(), ("2",))
        self.app.search_value.set("missing")
        self.assertEqual(self.app.navigation.item(category_id, "values"), ("0",))
        self.assertEqual(self.manager.calls, [], "navigation must never start or change a task")
        self.app.clear_filters()
        self.assertEqual(len(self.app.tree.get_children()), 3)
        self.assertEqual(self.app.queue_filter.get(), "Todas as filas")

    def test_heading_sort_preserves_selection_and_queue_order(self):
        self.seed()
        self.app.tree.selection_set("1")
        self.root.tk.call(self.app.tree.heading("name", "command"))
        self.assertEqual(self.app.tree.get_children(), ("2", "3", "1"))
        self.assertEqual(self.app.tree.selection(), ("1",))
        self.root.tk.call(self.app.tree.heading("size", "command"))
        self.assertEqual(self.app.tree.get_children(), ("1", "3", "2"))
        self.root.tk.call(self.app.tree.heading("size", "command"))
        self.assertEqual(self.app.tree.get_children(), ("2", "3", "1"))
        self.assertEqual(self.manager.calls, [])
        self.assertEqual([task["id"] for task in self.manager.tasks], ["1", "2", "3"])

    def test_context_menu_targets_clicked_row_and_reuses_pause_action(self):
        self.seed()
        self.root.deiconify(); self.root.update()
        self.app.tree.selection_set("1")
        box = self.app.tree.bbox("3")
        event = SimpleNamespace(x=box[0]+10, y=box[1]+10,
                                x_root=self.app.tree.winfo_rootx()+box[0]+10,
                                y_root=self.app.tree.winfo_rooty()+box[1]+10)
        with patch.object(self.app.context_menu, "tk_popup") as popup:
            self.app.show_context_menu(event)
        popup.assert_called_once()
        self.assertEqual(self.app.tree.selection(), ("3",))
        entry = next(index for index in range(self.app.context_menu.index("end")+1)
                     if self.app.context_menu.type(index) == "command"
                     and self.app.context_menu.entrycget(index, "label") == "Pausar")
        self.app.context_menu.invoke(entry)
        self.assertEqual(self.manager.calls, [("pause", "3")])

    def test_controls_fit_compact_window_and_enlarged_font_metrics(self):
        self.root.deiconify()
        for multiplier in (1, 1.25):
            with self.subTest(font_scale=multiplier):
                baseline = float(self.root.tk.call("tk", "scaling"))
                try:
                    self.root.tk.call("tk", "scaling", baseline * multiplier)
                    self.app._styles()
                    self.root.geometry("980x620"); self.root.update()
                    controls = [self.app.add_button, self.app.confirm_button, self.app.folder_button,
                                self.app.browser_button, self.app.settings_button,
                                *self.app.action_buttons.values(), self.app.queue_box,
                                self.app.category_box, self.app.filter_box, self.app.sort_box,
                                self.app.search_entry]
                    for widget in controls:
                        self.assertTrue(widget.winfo_ismapped(), str(widget))
                        self.assertGreaterEqual(widget.winfo_width(), widget.winfo_reqwidth(), str(widget))
                        self.assertGreaterEqual(widget.winfo_height(), widget.winfo_reqheight(), str(widget))
                        x = widget.winfo_rootx() - self.root.winfo_rootx()
                        y = widget.winfo_rooty() - self.root.winfo_rooty()
                        self.assertGreaterEqual(x, 0)
                        self.assertGreaterEqual(y, 0)
                        self.assertLessEqual(x+widget.winfo_width(), self.root.winfo_width(), str(widget))
                        self.assertLessEqual(y+widget.winfo_height(), self.root.winfo_height(), str(widget))
                    self.assertGreater(self.app.tree.winfo_height(), 180)
                    self.assertLess(self.app.add_button.winfo_rooty(), self.app.tree.winfo_rooty())
                    self.assertLess(self.app.navigation.winfo_rootx(), self.app.tree.winfo_rootx())
                    self.assertNotEqual(ttk.Style(self.root).lookup("TButton", "foreground", ("disabled",)), "white")
                finally:
                    self.root.tk.call("tk", "scaling", baseline)


if __name__ == "__main__":
    unittest.main()
