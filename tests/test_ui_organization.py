"""Queue organization changes presentation without starting transfers."""
import unittest
from unittest.mock import patch

from lume.ui import AddDialog
from tests import test_ui


class OrganizationTests(unittest.TestCase):
    setUp = test_ui.DesktopWorkflowTests.setUp
    tearDown = test_ui.DesktopWorkflowTests.tearDown
    def seed(self):
        for name, url in (("Zebra.mp4", "https://media.example/video?id=1"),
                          ("Audio.M4A", "https://music.example/track"),
                          ("Notas.pdf", "https://docs.example/report")):
            self.manager.add(url, self.manager.directory, filename=name, start=False)
        self.app.refresh()

    def test_search_and_category_intersection_does_not_start_transfers(self):
        self.seed()
        self.app.search_value.set("EXAMPLE")
        self.app.category_value.set("Áudio")
        self.app.refresh()
        self.assertEqual(self.app.tree.get_children(), ("2",))
        self.app.search_value.set("report")
        self.app.category_value.set("Todos os tipos")
        self.app.refresh()
        self.assertEqual(self.app.tree.get_children(), ("3",))
        self.assertEqual(self.manager.calls, [])
        self.assertTrue(all(task["status"] == "held" for task in self.manager.tasks))

    def test_sort_updates_existing_rows_and_preserves_selection(self):
        self.seed()
        self.app.tree.selection_set("1")
        self.app.sort_value.set("Nome A–Z")
        self.app.refresh()
        self.assertEqual(self.app.tree.get_children(), ("2", "3", "1"))
        self.assertEqual(self.app.tree.selection(), ("1",))
        self.app.sort_value.set("Ordem da fila")
        self.app.refresh()
        self.assertEqual(self.app.tree.get_children(), ("1", "2", "3"))
        self.assertEqual(self.manager.calls, [])

    def test_import_opens_confirmation_and_preserves_duplicate_requests(self):
        source = self.manager.directory / "links.txt"
        source.write_text("\ufeffhttps://example.org/a?token=abc\n\nhttps://example.org/a?token=abc\n", encoding="utf-8")
        with patch("lume.ui.filedialog.askopenfilename", return_value=str(source)):
            self.app.import_urls()
        dialogs = [widget for widget in self.root.winfo_children() if isinstance(widget, AddDialog)]
        self.assertEqual(len(dialogs), 1)
        self.assertEqual(dialogs[0]._urls(), ["https://example.org/a?token=abc"] * 2)
        self.assertEqual(self.manager.tasks, [])
        dialogs[0].destroy()

    def test_invalid_import_has_no_partial_side_effect(self):
        source = self.manager.directory / "bad.txt"
        source.write_text("https://example.org/good\nfile:///private\n", encoding="utf-8")
        with patch("lume.ui.filedialog.askopenfilename", return_value=str(source)), \
                patch("lume.ui.messagebox.showerror") as error:
            self.app.import_urls()
        error.assert_called_once()
        self.assertEqual(self.manager.tasks, [])
        self.assertFalse(any(isinstance(widget, AddDialog) for widget in self.root.winfo_children()))

    def test_export_selected_preserves_source_but_removes_transport_userinfo(self):
        self.manager.add("https://user:pass@example.org/a?token=chosen#part", self.manager.directory)
        self.manager.add("https://example.org/b", self.manager.directory)
        self.app.refresh()
        self.app.tree.selection_set("1")
        target = self.manager.directory / "export.txt"
        with patch("lume.ui.filedialog.asksaveasfilename", return_value=str(target)):
            self.app.export_urls()
        self.assertEqual(target.read_text(encoding="utf-8"), "https://example.org/a?token=chosen#part\n")

    def test_delete_in_search_field_does_not_remove_download(self):
        self.seed()
        self.app.tree.selection_set("1")
        self.assertEqual(self.root.bind("<Delete>"), "")
        self.assertTrue(self.app.tree.bind("<Delete>"))

    def test_queue_move_uses_manager_order_without_resuming(self):
        self.seed()
        self.app.tree.selection_set("2")
        self.app.sort_value.set("Nome A–Z")
        with patch.object(self.manager, "move", create=True) as move:
            self.app.move_selected(-1)
        move.assert_called_once_with("2", -1)
        self.assertEqual(self.app.sort_value.get(), "Ordem da fila")
        self.assertEqual(self.manager.calls, [])

    def test_replace_url_requires_explicit_partial_data_confirmation(self):
        self.seed()
        self.app.tree.selection_set("1")
        with patch("lume.ui.simpledialog.askstring", return_value="https://new.example/a.mp4"), \
                patch("lume.ui.messagebox.askyesno", return_value=False), \
                patch.object(self.manager, "replace_source", create=True) as replace:
            self.app.replace_source_selected()
        replace.assert_not_called()
        with patch("lume.ui.simpledialog.askstring", return_value="https://new.example/a.mp4"), \
                patch("lume.ui.messagebox.askyesno", return_value=True), \
                patch.object(self.manager, "replace_source", create=True) as replace:
            self.app.replace_source_selected()
        replace.assert_called_once_with("1", "https://new.example/a.mp4")
        self.assertEqual(self.manager.tasks[0]["status"], "held")
        self.assertEqual(self.manager.calls, [])

    def test_import_rejects_transport_credentials_without_opening_dialog(self):
        source = self.manager.directory / "private.txt"
        source.write_text("https://user:password@example.org/file\n", encoding="utf-8")
        with patch("lume.ui.filedialog.askopenfilename", return_value=str(source)), \
                patch("lume.ui.messagebox.showerror") as error:
            self.app.import_urls()
        error.assert_called_once()
        self.assertNotIn("password", str(error.call_args))
        self.assertEqual(self.manager.tasks, [])


if __name__ == "__main__":
    unittest.main()
