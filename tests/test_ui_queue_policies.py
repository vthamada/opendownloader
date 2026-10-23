"""Explicit destination and queue selection using actual Tk widgets."""
import unittest
from unittest.mock import patch
from lume.ui import AddDialog, SettingsDialog
from tests import test_ui

class QueuePolicyUiTests(unittest.TestCase):
    setUp = test_ui.DesktopWorkflowTests.setUp
    tearDown = test_ui.DesktopWorkflowTests.tearDown

    def test_category_button_changes_only_visible_destination_until_confirmed(self):
        chosen=str(self.manager.directory/'videos')
        self.manager.options['category_directories']={'Vídeo':chosen}
        dialog=AddDialog(self.app)
        dialog.urls.insert('1.0','https://example.org/video.mp4')
        dialog.use_category_directory()
        self.assertEqual(dialog.directory.get(),chosen)
        self.assertEqual(self.manager.tasks,[])
        manual=str(self.manager.directory/'manual')
        dialog.directory.set(manual)
        dialog.submit(start=False)
        self.assertEqual(self.manager.tasks[0]['directory'],manual)
        self.assertEqual(self.manager.tasks[0]['queue_id'],'default')
        self.assertEqual(self.manager.tasks[0]['status'],'held')

    def test_queue_filter_and_space_are_local_to_tree(self):
        self.manager.options['queues']=[{'id':'default','name':'Principal','enabled':True},{'id':'videos','name':'Videos','enabled':False}]
        self.manager.add('https://example.org/a',self.manager.directory,start=False)
        self.manager.add('https://example.org/b',self.manager.directory,start=False,queue_id='videos')
        self.app.refresh()
        self.app.queue_filter.set('Videos')
        self.app.refresh()
        self.assertEqual(self.app.tree.get_children(),('2',))
        self.assertEqual(self.root.bind('<space>'),'')
        self.assertTrue(self.app.tree.bind('<space>'))
        self.app.tree.selection_set('2')
        with patch.object(self.manager,'resume',create=True) as resume:
            self.assertEqual(self.app.toggle_selected(),'break')
        resume.assert_called_once_with('2')

    def test_settings_saves_rules_with_preferences_and_cancel_does_not_change_them(self):
        dialog=SettingsDialog(self.app)
        dialog.category_directories['Vídeo'].set(str(self.manager.directory/'video'))
        dialog.destroy()
        self.assertNotIn('category_directories',self.manager.options)
        dialog=SettingsDialog(self.app)
        dialog.category_directories['Vídeo'].set(str(self.manager.directory/'video'))
        dialog.submit()
        self.assertEqual(self.manager.options['category_directories'],{'Vídeo':str(self.manager.directory/'video')})

    def test_invalid_url_folder_lookup_keeps_destination_and_keyboard_activation_is_local(self):
        dialog=AddDialog(self.app)
        dialog.urls.insert('1.0','https://[')
        original=dialog.directory.get()
        dialog.use_category_directory()
        self.assertEqual(dialog.directory.get(),original)
        self.assertIn('Confira',dialog.destination_status.get())
        self.assertTrue(dialog.bind('<Control-l>'))
        dialog.destroy()
        with patch.object(self.app,'show_details') as details:
            self.assertEqual(self.app.activate_selected(),'break')
        details.assert_called_once()
