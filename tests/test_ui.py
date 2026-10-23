"""Actual Tk widgets exercised against the manager's public seam."""

from __future__ import annotations

import copy
import gc
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import subprocess
import sys
import tempfile
import threading
import time
import types
import tkinter as tk
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from lume.ui import AddDialog, App, ConfirmationDialog, SettingsDialog, UpdateDialog


class FixtureManager:
    def __init__(self, directory):
        self.directory = directory
        self.data_dir = directory
        self.tasks = []
        self.requests = []
        self.options = {"default_directory": str(directory), "max_active": 2, "speed_limit_kib": 0}
        self.calls = []
        self.close_started = threading.Event()
        self.close_allowed = threading.Event()
        self.close_allowed.set()

    def list_tasks(self):
        return copy.deepcopy(self.tasks)

    def list_requests(self):
        return copy.deepcopy(self.requests)

    def confirm_request(self, request_id, directory, filename=None, parts=4, **values):
        request = next(item for item in self.requests if item["id"] == request_id)
        identity = self.add(request["url"], directory, filename=filename, parts=parts, **values)
        self.requests = [item for item in self.requests if item["id"] != request_id]
        self.calls.append(("confirm_request", request_id, values))
        return identity

    def dismiss_request(self, request_id):
        self.requests = [item for item in self.requests if item["id"] != request_id]
        self.calls.append(("dismiss_request", request_id))

    def preview_destination(self, directory, filename, duplicate_policy="copy"):
        return {"filename": "video (1).mp4", "conflict": True, "existing_path": str(Path(directory) / filename), "reserved": False}

    def get_options(self):
        return dict(self.options)

    def list_queues(self):
        return copy.deepcopy(self.options.get("queues", [{"id": "default", "name": "Principal", "enabled": True}]))

    def suggest_directory(self, filename):
        from lume.organization import filename_category
        return self.options.get("category_directories", {}).get(filename_category(filename))

    def set_options(self, **values):
        self.options.update(values)

    def add(self, url, directory, **values):
        if url == "invalid":
            raise ValueError("Endereço HTTP/HTTPS inválido")
        task_id = str(len(self.tasks) + 1)
        self.tasks.append({"id": task_id, "url": url, "filename": values.get("filename") or "arquivo.bin", "directory": directory, "status": "queued" if values.get("start", True) else "held", "downloaded": 0, "total": 1000, "speed": 0, **values})
        return task_id

    def pause(self, task_id):
        self.calls.append(("pause", task_id))
        self.tasks[int(task_id) - 1]["status"] = "paused"

    def close(self):
        self.close_started.set()
        if not self.close_allowed.wait(4):
            raise RuntimeError("Fixture close timeout")


class DesktopWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.test_root = Path(__file__).resolve().parents[1] / ".cache" / "ui-tests"
        self.test_root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=self.test_root)
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.temp.cleanup()
            self.skipTest(f"Tk desktop unavailable: {error}")
        self.root.withdraw()
        self.manager = FixtureManager(Path(self.temp.name))
        self.app = App(self.root, self.manager)
        self.root.update_idletasks()

    def tearDown(self):
        self.manager.close_allowed.set()
        try:
            if self.app._poll_id is not None:
                self.root.after_cancel(self.app._poll_id)
            self.root.destroy()
        except tk.TclError:
            pass
        self.app = None
        self.root = None
        # Tk objects belong to this thread, including cycles retained by mock
        # call records from a finished dialog. Collect before a worker can.
        gc.collect()
        if Path(self.temp.name).resolve().is_relative_to(self.test_root.resolve()):
            self.temp.cleanup()

    def test_selection_progress_actions_and_credential_free_display(self):
        self.manager.add("https://secret:password@example.org/test.bin?token=hidden", self.manager.directory)
        self.manager.tasks[0].update(status="downloading", downloaded=500, speed=100)
        self.app.refresh()
        self.app.tree.selection_set("1")
        self.app.update_details()
        self.assertEqual(self.app.active_value.get(), "1")
        self.assertEqual(float(self.app.progress["value"]), 50.0)
        self.assertEqual(self.app.selected_info.get(), "https://example.org/test.bin")
        self.app.action_buttons["Pausar"].invoke()
        self.assertEqual(self.manager.calls, [("pause", "1")])
        self.assertEqual(self.app.tree.item("1")["values"][1], "Pausado")

    def test_recording_confirmation_preserves_captured_format_and_hides_network_controls(self):
        request = {"id": "a" * 32, "url": "recording:" + "a" * 32,
                   "filename": "Gravacao.mp4", "parts": 1,
                   "recording": {"ext": "mp4", "bytes": 1234, "sha256": "b" * 64}}
        with patch('lume.media.discover_media', side_effect=AssertionError('Recording cannot discover URLs')):
            dialog = AddDialog(self.app, request)
            self.root.update_idletasks()
            self.assertEqual(dialog.title(), "Salvar gravação • OpenDownloader")
            self.assertEqual(dialog.download_button['text'], 'Salvar agora')
            self.assertEqual(str(dialog.catalog_button['state']), 'disabled')
            self.assertEqual(dialog.authorized_keys_entry.winfo_manager(), '')
            self.assertIn('gravação', dialog.catalog_status.get())
            self.assertEqual(dialog.parts.get(), 1)
            dialog.destroy()

    def test_filter_refresh_preserves_selected_task(self):
        self.manager.add("https://example.org/a", self.manager.directory)
        self.manager.add("https://example.org/b", self.manager.directory)
        self.manager.tasks[0]["status"] = "completed"
        self.manager.tasks[1]["status"] = "paused"
        self.app.refresh()
        self.app.tree.selection_set("2")
        self.app.refresh()
        self.assertEqual(self.app.tree.selection(), ("2",))
        self.app.filter_value.set("Concluídos")
        self.app.refresh()
        self.assertEqual(self.app.tree.get_children(), ("1",))
        self.assertEqual(self.app.done_value.get(), "1")

    def test_batch_keeps_only_rejected_urls_to_prevent_duplicate_retry(self):
        dialog = AddDialog(self.app)
        dialog.withdraw()
        dialog.urls.insert("1.0", "https://example.org/a\ninvalid\nhttps://example.org/b")
        with patch("lume.ui.messagebox.showerror") as error:
            dialog.submit()
        self.assertEqual(len(self.manager.tasks), 2)
        self.assertEqual(dialog.urls.get("1.0", "end").strip(), "invalid")
        self.assertEqual(dialog.result_count, 2)
        error.assert_called_once()
        dialog.destroy()

    def test_settings_save_public_contract(self):
        dialog = SettingsDialog(self.app)
        dialog.withdraw()
        dialog.max_active.set(3)
        dialog.speed_limit.set(512)
        dialog.browser_auto_start.set(True)
        dialog.submit()
        self.assertEqual(self.manager.options["max_active"], 3)
        self.assertEqual(self.manager.options["speed_limit_kib"], 512)
        self.assertTrue(self.manager.options["browser_auto_start"])

    def browser_request(self, identity="request-1"):
        request = {"id": identity, "url": "https://example.org/video.mp4", "display_url": "https://example.org/video.mp4",
                   "filename": "video.mp4", "directory": str(self.manager.directory), "parts": 4,
                   "status": "awaiting_confirmation", "error": ""}
        self.manager.requests.append(request)
        return request

    def test_browser_request_waits_for_confirmation_and_x_keeps_it_visible(self):
        request = self.browser_request()
        self.app.refresh()
        self.assertEqual(self.app.tree.item(request["id"])["values"][1], "Aguardando confirmação")
        self.assertEqual(self.manager.tasks, [])
        self.app._open_next_request()
        dialog = self.app._confirmation
        self.assertIsInstance(dialog, ConfirmationDialog)
        dialog.destroy()
        self.app._open_next_request()
        self.assertIsNone(self.app._confirmation, "X must not reopen the same prompt every poll")
        self.assertEqual(len(self.manager.requests), 1)
        self.app.tree.selection_set(request["id"])
        self.app.confirm_selected()
        self.app._confirmation.submit(start=False)
        self.assertEqual(self.manager.requests, [])
        self.assertEqual(self.manager.tasks[0]["status"], "held")
        self.assertEqual(self.manager.calls[-1][0], "confirm_request")
        self.assertEqual(self.manager.calls[-1][2]["duplicate_policy"], "copy")

    def test_cancel_browser_prompt_dismisses_only_that_request(self):
        request = self.browser_request()
        self.manager.add("https://example.org/existing.zip", self.manager.directory)
        self.app.refresh()
        self.app._open_next_request()
        self.app._confirmation.cancel()
        self.assertEqual(self.manager.requests, [])
        self.assertEqual(len(self.manager.tasks), 1)
        self.assertEqual(self.manager.calls, [("dismiss_request", request["id"])])

    def test_browser_request_error_is_visible_and_task_controls_cannot_start_it(self):
        request = self.browser_request()
        request["error"] = "Já existe esse destino; escolha outra cópia."
        self.app.refresh()
        self.app.tree.selection_set(request["id"])
        self.app.update_details()
        self.assertIn("Já existe", self.app.selected_info.get())
        self.assertTrue(self.app.action_buttons["Continuar"].instate(("disabled",)))
        self.assertTrue(self.app.confirm_button.instate(("!disabled",)))

    def test_manual_queue_later_preserves_selection_and_does_not_start(self):
        dialog = AddDialog(self.app)
        dialog.urls.insert("1.0", "https://example.org/archive.zip")
        dialog.filename.set("another-copy.zip")
        dialog.submit(start=False)
        self.assertEqual(self.manager.tasks[0]["filename"], "another-copy.zip")
        self.assertEqual(self.manager.tasks[0]["status"], "held")
        self.assertEqual(self.manager.tasks[0]["duplicate_policy"], "copy")

    def test_direct_link_format_label_never_promises_mp4_conversion(self):
        dialog = AddDialog(self.app)
        self.assertEqual(dialog.format_value.get(), "Automático — formato padrão do link")
        dialog.urls.insert("1.0", "https://example.org/audio.mp3")
        dialog.load_formats()
        self.assertEqual(dialog.format_value.get(), "Arquivo original — sem conversão")
        self.assertIsNone(dialog._selected_format())
        dialog.destroy()

    def test_duplicate_preview_preserves_typed_name_and_opens_only_parent_folder(self):
        dialog = ConfirmationDialog(self.app, self.browser_request())
        dialog.preview_destination()
        self.assertIn("video (1).mp4", dialog.destination_status.get())
        self.assertEqual(dialog.filename.get(), "video.mp4")
        with patch.object(self.app, "open_directory") as open_directory:
            dialog.open_existing_folder()
            open_directory.assert_called_once_with(self.manager.directory)
        dialog.destroy()

    def test_format_catalogue_selects_audio_without_renaming_user_input(self):
        catalogue = {"video_id": "BaW_jenozKc", "title": "Public video", "filename": "YouTube-BaW_jenozKc.mp4",
                     "formats": [{"id": "audio:140", "label": "Somente áudio M4A", "ext": "m4a", "audio_only": True,
                                  "filename": "YouTube-BaW_jenozKc.m4a", "expected_size": None}]}
        dialog = AddDialog(self.app)
        dialog.urls.insert("1.0", "https://youtu.be/BaW_jenozKc")
        with patch("lume.media.discover_media", return_value=catalogue, create=True):
            dialog.load_formats()
            deadline = time.monotonic() + 3
            while dialog.catalog_busy and time.monotonic() < deadline:
                self.root.update()
                time.sleep(.02)
        self.assertFalse(dialog.catalog_busy)
        self.assertEqual(dialog.filename.get(), "YouTube-BaW_jenozKc.mp4")
        dialog.format_box.current(1)
        dialog._format_changed()
        self.assertEqual(dialog.filename.get(), "YouTube-BaW_jenozKc.m4a")
        dialog.format_box.current(0)
        dialog._format_changed()
        self.assertEqual(dialog.filename.get(), "YouTube-BaW_jenozKc.mp4")
        dialog.format_box.current(1)
        dialog._format_changed()
        dialog.filename.set("my-song.m4a")
        dialog._format_changed()
        self.assertEqual(dialog.filename.get(), "my-song.m4a")
        dialog.submit(start=False)
        self.assertEqual(self.manager.tasks[0]["media_selection"], "audio:140")
        self.assertEqual(self.manager.tasks[0]["filename"], "my-song.m4a")

    def test_extensionless_stream_hint_uses_stream_catalogue_and_preserves_selection(self):
        request = self.browser_request()
        request.update(url='https://example.org/playback?id=fixture', source='hls')
        catalogue={'title':'HLS fixture','filename':'Stream-fixture.mp4','formats':[
            {'id':'variant-720','label':'720p · MP4','filename':'Stream-fixture.mp4','ext':'mp4','audio_only':False,'expected_size':None}]}
        calls=[]
        module=types.ModuleType('lume.streaming')
        def discover(url, stop):
            calls.append((url,stop))
            return catalogue
        module.discover_stream=discover
        with patch.dict(sys.modules,{'lume.streaming':module}):
            dialog=ConfirmationDialog(self.app,request)
            deadline=time.monotonic()+3
            while not dialog.formats and time.monotonic()<deadline:
                self.root.update()
                time.sleep(.02)
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0][0],request['url'])
        self.assertEqual(dialog.format_value.get(),'Automático — formato compatível da fonte')
        self.assertEqual(dialog.filename.get(),'Stream-fixture.mp4')
        dialog.format_box.current(1)
        dialog.submit(start=False)
        self.assertEqual(self.manager.tasks[0]['media_selection'],'variant-720')

    def test_platform_format_catalogue_uses_neutral_default_label(self):
        catalogue={'title':'Vimeo fixture','filename':'Vimeo-76979871.mp4','formats':[]}
        dialog=AddDialog(self.app)
        dialog.urls.insert('1.0','https://vimeo.com/76979871')
        with patch('lume.media.discover_media',return_value=catalogue):
            dialog.load_formats()
            deadline=time.monotonic()+3
            while dialog.catalog_busy and time.monotonic()<deadline:
                self.root.update()
                time.sleep(.02)
        self.assertEqual(dialog.format_value.get(),'Automático — formato compatível da fonte')
        self.assertEqual(dialog.filename.get(),'Vimeo-76979871.mp4')
        dialog.destroy()

    def test_closing_catalogue_cancels_worker_without_retaining_tk(self):
        started = threading.Event()
        stopped = threading.Event()
        def discover(_url, stop):
            started.set()
            if stop.wait(3):
                stopped.set()
            raise ValueError("Consulta cancelada")
        dialog = AddDialog(self.app)
        dialog.urls.insert("1.0", "https://youtu.be/BaW_jenozKc")
        with patch("lume.media.discover_media", side_effect=discover, create=True):
            dialog.load_formats()
            self.assertTrue(started.wait(1))
            dialog.destroy()
            self.assertTrue(stopped.wait(1))
            for worker in self.app.background_jobs:
                worker.join(1)
                self.assertFalse(worker.is_alive())

    def test_closing_waits_for_manager_without_blocking_tk(self):
        self.manager.close_allowed.clear()
        self.app.begin_close()
        self.assertTrue(self.manager.close_started.wait(1))
        self.root.update()
        self.assertTrue(self.app.closing)
        self.assertTrue(self.root.winfo_exists())
        self.assertIn("preservando", self.app.notice.get())
        self.manager.close_allowed.set()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                self.root.update()
                if not self.root.winfo_exists():
                    break
            except tk.TclError:
                break
            time.sleep(0.02)
        else:
            self.fail("Tk did not finish closing after manager cleanup")

    def test_update_dialog_blocks_without_trusted_key(self):
        dialog = UpdateDialog(self.app)
        dialog.withdraw()
        dialog.manifest.set("manifest.json")
        dialog.package.set("package.zip")
        with patch("lume.ui.messagebox.showerror") as error:
            dialog.prepare()
        error.assert_called_once()
        self.assertFalse(dialog.busy)
        self.assertEqual(self.app.background_jobs, [])
        dialog.destroy()

    @unittest.skipUnless(importlib.util.find_spec("cryptography"), "cryptography required for the signed-update UI workflow")
    def test_update_dialog_authenticates_and_stages_without_execution(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from lume import VERSION
        from lume.updates import canonical_payload
        package = Path(self.temp.name) / "release.zip"
        with zipfile.ZipFile(package, "w") as archive:
            archive.writestr("README.txt", b"Signed fixture; no executable")
        major, minor, patch_version = (int(piece) for piece in VERSION.split("."))
        next_version = f"{major}.{minor}.{patch_version + 1}"
        payload = {"app": "lume-downloads", "version": next_version, "filename": package.name,
                   "sha256": hashlib.sha256(package.read_bytes()).hexdigest(), "size": package.stat().st_size}
        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        manifest = Path(self.temp.name) / "manifest.json"
        manifest.write_text(json.dumps({"payload": payload, "signature": base64.b64encode(private_key.sign(canonical_payload(payload))).decode("ascii")}), encoding="utf-8")
        dialog = UpdateDialog(self.app)
        dialog.withdraw()
        dialog.manifest.set(str(manifest))
        dialog.package.set(str(package))
        dialog.key.set(base64.b64encode(public_key).decode("ascii"))
        with patch("lume.ui.messagebox.showerror") as error:
            dialog.prepare()
            deadline = time.monotonic() + 5
            while dialog.busy and time.monotonic() < deadline:
                self.root.update()
                time.sleep(0.02)
        self.assertFalse(dialog.busy, "Update dialog never delivered its background result")
        error.assert_not_called()
        self.assertIsNotNone(dialog.prepared)
        self.assertEqual((dialog.prepared / "payload" / "README.txt").read_bytes(), b"Signed fixture; no executable")
        self.assertIn("nenhum programa foi executado", dialog.status.get().lower())
        dialog.destroy()


class RealDesktopWorkflowTests(unittest.TestCase):
    def test_real_browser_confirmation_holds_then_downloads_duplicate_safely(self):
        from lume.browser import enqueue_link
        from lume.core import Manager
        test_root = Path(__file__).resolve().parents[1] / ".cache" / "ui-tests"
        test_root.mkdir(parents=True, exist_ok=True)
        payload = b"Confirmed browser download with safe duplicate handling" * 1024
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.path)
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                try:
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            def log_message(self, *_args):
                pass
        with tempfile.TemporaryDirectory(dir=test_root) as temporary:
            root = tk.Tk()
            root.withdraw()
            root.attributes("-alpha", 0.0)
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            server.daemon_threads = True
            threading.Thread(target=server.serve_forever, daemon=True).start()
            directory = Path(temporary) / "downloads"
            directory.mkdir()
            original = directory / "fixture.bin"
            original.write_bytes(b"existing user file")
            manager = Manager(Path(temporary) / "data", directory)
            app = App(root, manager)
            try:
                enqueue_link(manager.data_dir, f"http://127.0.0.1:{server.server_port}/fixture.bin")
                deadline = time.monotonic() + 3
                while not manager.list_requests() and time.monotonic() < deadline:
                    root.update()
                    time.sleep(.02)
                self.assertEqual(len(manager.list_requests()), 1)
                self.assertEqual(manager.list_tasks(), [])
                self.assertEqual(requests, [], "Ingress must not transfer before confirmation")
                app.refresh()
                app.confirm_selected()
                self.assertIsInstance(app._confirmation, ConfirmationDialog)
                app._confirmation.submit(start=False)
                task = manager.list_tasks()[0]
                self.assertEqual(task["status"], "held")
                self.assertNotEqual(task["filename"], "fixture.bin")
                self.assertEqual(requests, [])
                app.tree.selection_set(task["id"])
                app.apply_action("resume")
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    root.update()
                    task = manager.list_tasks()[0]
                    if task["status"] in {"completed", "failed"}:
                        break
                    time.sleep(.02)
                self.assertEqual(task["status"], "completed", task.get("error"))
                self.assertEqual((directory / task["filename"]).read_bytes(), payload)
                self.assertEqual(original.read_bytes(), b"existing user file")
                self.assertTrue(requests)
            finally:
                manager.close()
                root.destroy()
                app = None
                root = None
                gc.collect()
                server.shutdown()
                server.server_close()

    def test_real_manager_downloads_and_restores_queue_through_tk(self):
        from lume.core import Manager
        test_root = Path(__file__).resolve().parents[1] / ".cache" / "ui-tests"
        test_root.mkdir(parents=True, exist_ok=True)
        temp = tempfile.TemporaryDirectory(dir=test_root)
        root = None
        manager = None
        server = None
        payload = b"Lume interface and real download engine\n" * 2048

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                try:
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *_args):
                pass

        try:
            try:
                root = tk.Tk()
            except tk.TclError as error:
                self.skipTest(f"Tk desktop unavailable: {error}")
            root.withdraw()
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            server.daemon_threads = True
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            data = Path(temp.name) / "data"
            downloads = Path(temp.name) / "downloads"
            manager = Manager(data, downloads)
            app = App(root, manager)
            dialog = AddDialog(app)
            dialog.withdraw()
            dialog.urls.insert("1.0", f"http://127.0.0.1:{server.server_port}/fixture.bin")
            dialog.sha.set(hashlib.sha256(payload).hexdigest())
            dialog.submit()
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                root.update()
                tasks = manager.list_tasks()
                if tasks and tasks[0]["status"] in {"completed", "failed"}:
                    break
                time.sleep(0.02)
            else:
                self.fail("The real desktop workflow did not finish")
            self.assertEqual(tasks[0]["status"], "completed", tasks[0].get("error"))
            self.assertEqual((downloads / "fixture.bin").read_bytes(), payload)
            app.refresh()
            self.assertEqual(app.done_value.get(), "1")
            self.assertEqual(app.tree.item(tasks[0]["id"])["values"][1], "Concluído")
            app.begin_close()
            deadline = time.monotonic() + 5
            while not manager.closed and time.monotonic() < deadline:
                root.update()
                time.sleep(0.02)
            self.assertTrue(manager.closed)
            with_reopened = Manager(data, downloads)
            try:
                self.assertEqual(with_reopened.list_tasks()[0]["status"], "completed")
            finally:
                with_reopened.close()
        finally:
            if manager is not None and not manager.closed:
                manager.close()
            if root is not None:
                try:
                    root.destroy()
                except tk.TclError:
                    pass
            app = None
            root = None
            gc.collect()
            if server is not None:
                server.shutdown()
                server.server_close()
            if Path(temp.name).resolve().is_relative_to(test_root.resolve()):
                temp.cleanup()


class TkOwnershipRegressionTests(unittest.TestCase):
    def test_destroyed_ui_can_be_collected_by_unrelated_worker_without_tcl_calls(self):
        """Crash isolation: the former defect aborts Tcl, not just a test case."""
        script = r'''
import gc
from pathlib import Path
import sys
import threading
import tkinter as tk
from lume.ui import AddDialog, App, ConfirmationDialog, SettingsDialog, UpdateDialog
from tests.test_ui import FixtureManager
from lume.setup_ui import IntegrationDialog

errors = []
sys.unraisablehook = lambda event: errors.append(str(event.exc_value))
# Only the regression harness defers cyclic collection, to deterministically
# make an unrelated worker be the next collector. Production never disables GC.
gc.disable()
for _ in range(5):
    root = tk.Tk()
    root.withdraw()
    manager = FixtureManager(Path.cwd())
    app = App(root, manager)
    for kind in (AddDialog, SettingsDialog, UpdateDialog, ConfirmationDialog, IntegrationDialog):
        request = {"id":"request-gc", "url":"https://example.org/file.bin", "filename":"file.bin", "directory":str(Path.cwd()), "parts":4}
        dialog = (kind(app, request) if kind is ConfirmationDialog else
                  kind(root, bundle=Path.cwd()) if kind is IntegrationDialog else kind(app))
        dialog.withdraw()
        dialog.destroy()
        del dialog
    root.destroy()
    assert app.root is None, "UI owner retained a destroyed Tcl interpreter"
    del app, manager, root
    worker = threading.Thread(target=gc.collect)
    worker.start()
    worker.join()
assert not errors, errors
print("worker-gc-safe")
'''
        result = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("worker-gc-safe", result.stdout)
        self.assertNotIn("main thread is not in main loop", result.stderr)


if __name__ == "__main__":
    unittest.main()
