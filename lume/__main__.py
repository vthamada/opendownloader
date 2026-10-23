"""Desktop, command-line and browser-host entry points."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

from . import VERSION, APP_NAME
from .core import Manager, default_data_dir, default_download_dir
from .storage import InstanceAlreadyRunning


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    key_input_requested = any(arg.split("=", 1)[0] == "--keys-stdin" for arg in argv)
    # Maintenance is an explicit command-line operation. Browser frames are
    # handled only after this branch and cannot select these operations.
    from .maintenance import MODES
    if argv and argv[0] in MODES:
        if key_input_requested:
            if sys.stderr:
                print("OpenDownloader: --keys-stdin exige somente --download.", file=sys.stderr)
            return 1
        from .maintenance import main as maintenance_main
        return maintenance_main(argv)
    native = (Path(sys.executable).stem.lower() == "lumenativehost" or "--native-host" in argv or
              any(re.fullmatch(r"chrome-extension://[a-p]{32}/", arg) for arg in argv))
    if native:
        if key_input_requested:
            return 1
        from .browser import run_native_host
        from .desktop_start import request_desktop
        # Explicit diagnostics keep the durable inbox-only contract. Real browser
        # invocations additionally request startup of the fixed desktop companion.
        return run_native_host(default_data_dir(), on_queued=None if "--native-host" in argv else request_desktop)
    class InputParser(argparse.ArgumentParser):
        def error(self, message):
            # A misplaced secret beside this opt-in flag must not be echoed by
            # argparse as an unknown argument or an unexpected explicit value.
            if key_input_requested:
                message = "Argumentos inválidos para --keys-stdin. Envie as chaves somente pela entrada padrão com --download."
            super().error(message)
    parser = InputParser(prog="OpenDownloader", description="Gerenciador local de downloads.")
    parser.add_argument("--version", action="version", version=VERSION)
    parser.add_argument("--data-dir", type=Path, default=default_data_dir())
    parser.add_argument("--download-dir", type=Path, default=default_download_dir())
    parser.add_argument("--add", metavar="URL", help="Adiciona uma URL à entrada persistente do aplicativo.")
    parser.add_argument("--download", metavar="URL", help="Baixa pelo terminal e aguarda o resultado.")
    parser.add_argument("--save-recording", metavar="ID", help="Confere e salva uma gravação local pronta, pelo identificador de seu recibo.")
    parser.add_argument("--name", help="Nome do arquivo para --download.")
    parser.add_argument("--parts", type=int, default=4)
    parser.add_argument("--media", action="store_true", help="Exige mídia de áudio/vídeo em --download e aplica a análise antecipada de proteção.")
    parser.add_argument("--format-id", help="ID explícito do catálogo de formatos de --download; preservado na retomada.")
    parser.add_argument("--sha256", help="SHA-256 esperado, obtido de uma fonte confiável.")
    parser.add_argument("--keys-stdin", action="store_true", help="Lê da entrada padrão até 16 pares KID:CHAVE autorizados; somente --download. Não salva chaves.")
    parser.add_argument("--self-test", action="store_true", help="Confere runtime e abre/fecha a interface sem downloads.")
    parser.add_argument("--from-browser", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--integration-center", action="store_true", help="Abre a configuração dos navegadores.")
    parser.add_argument("--installation-root", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        if (args.media or args.format_id is not None) and (not args.download or args.add or args.save_recording or args.self_test or args.integration_center or args.from_browser):
            from .network import DownloadError
            raise DownloadError('options','--media e --format-id exigem somente --download.')
        if args.save_recording and (args.download or args.add or args.self_test or args.from_browser
                                   or args.integration_center or args.sha256):
            from .network import DownloadError
            raise DownloadError("recording", "Use --save-recording isoladamente, com nome e pasta opcionais.")
        authorized_keys = None
        if args.keys_stdin:
            from .crypto_keys import MAX_KEY_TEXT, parse_authorized_keys
            from .network import DownloadError
            if not args.download or args.add or args.save_recording or args.self_test or args.integration_center or args.from_browser:
                raise DownloadError("authorized_keys", "--keys-stdin exige somente --download.")
            stream = getattr(sys.stdin, "buffer", sys.stdin)
            if stream is None:
                raise DownloadError("authorized_keys", "A entrada padrão de chaves não está disponível.")
            raw_keys = stream.read(MAX_KEY_TEXT + 1)
            try:
                if len(raw_keys) > MAX_KEY_TEXT:
                    raise ValueError()
                key_text = raw_keys.decode("ascii") if isinstance(raw_keys, bytes) else raw_keys
                authorized_keys = parse_authorized_keys(key_text)
                if not authorized_keys:
                    raise ValueError()
            except (UnicodeError, ValueError):
                raise DownloadError("authorized_keys", "A entrada deve conter até 16 pares KID:CHAVE autorizados de 32 caracteres hexadecimais cada.") from None
            finally:
                raw_keys = key_text = None
        if args.integration_center:
            from .setup_ui import run_integration_center
            return run_integration_center(args.installation_root)
        if args.add:
            from .browser import enqueue_link
            item_id = enqueue_link(args.data_dir, args.add)
            print(json.dumps({"ok": True, "id": item_id, "state": "queued-inbox"}))
            return 0
        try:
            manager = Manager(args.data_dir, args.download_dir)
        except InstanceAlreadyRunning:
            if args.from_browser:
                return 0  # Existing desktop owns this profile and consumes the durable inbox.
            raise
        if args.download or args.save_recording:
            try:
                key_options = {"authorized_keys": authorized_keys} if authorized_keys else {}
                if args.save_recording:
                    task = manager.save_recording(args.save_recording, args.download_dir, args.name)
                else:
                    task = manager.add(args.download, args.download_dir, args.name, args.parts, args.sha256,
                                       _expected_media=args.media,media_selection=args.format_id,**key_options)
                authorized_keys = None
                result = manager.wait(task, 24 * 60 * 60)
                print(json.dumps({key: result[key] for key in ("id", "filename", "status", "total", "sha256", "error")}))
                return 0 if result["status"] == "completed" else 1
            except KeyboardInterrupt:
                return 130
            finally:
                manager.close()
        if args.self_test:
            import tkinter as tk
            from tkinter import ttk
            from .ui import App, AddDialog, ConfirmationDialog, SettingsDialog, UpdateDialog
            root = tk.Tk()
            root.withdraw()
            root.attributes("-alpha", 0.0)
            app = App(root, manager)
            root.deiconify()
            measurements = []
            def descendants(widget):
                for child in widget.winfo_children():
                    yield child
                    yield from descendants(child)
            def measure(window, labels=None, *, record=True):
                window.update()
                buttons = [button for button in descendants(window) if isinstance(button, ttk.Button)
                           and button.winfo_ismapped()
                           and (labels is None or str(button.cget("text")) in labels)]
                if not buttons:
                    raise RuntimeError("A interface não expôs seus controles.")
                if labels is not None and not labels <= {str(button.cget('text')) for button in buttons}:
                    raise RuntimeError("Um controle obrigatório não está visível.")
                for button in buttons:
                    x = button.winfo_rootx() - window.winfo_rootx()
                    y = button.winfo_rooty() - window.winfo_rooty()
                    if (not button.winfo_ismapped() or button.winfo_height() < button.winfo_reqheight()
                        or button.winfo_width() < button.winfo_reqwidth()
                        or x + button.winfo_width() > window.winfo_width()
                        or y + button.winfo_height() > window.winfo_height()):
                        raise RuntimeError("Controle cortado: " + str(button.cget("text")))
                values=[str(b.cget("text")) for b in buttons]
                if record:measurements.append({"window": window.title(), "buttons": values})
                return values
            try:
                if root.title()!=APP_NAME:
                    raise RuntimeError("O título do aplicativo diverge do nome do produto.")
                measure(root)
                root.geometry('980x620')
                measure(root, record=False)
                for kind, labels in ((AddDialog, {"Cancelar", "Baixar agora", "Só adicionar à fila"}),
                                     (SettingsDialog, {"Cancelar", "Salvar preferências"}),
                                     (UpdateDialog, {"Fechar", "Verificar e preparar", "Abrir pasta preparada"})):
                    dialog = kind(app)
                    dialog.attributes("-alpha", 0.0)
                    try:
                        measure(dialog, labels)
                    finally:
                        dialog.destroy()
                dialog = ConfirmationDialog(app, {"id": "0"*32, "url": "https://example.org/file.bin",
                                                    "filename": "file.bin", "directory": str(args.download_dir),
                                                    "status": "awaiting_confirmation", "error": ""})
                dialog.attributes("-alpha", 0.0)
                try:
                    measure(dialog, {"Cancelar", "Baixar agora", "Só adicionar à fila"})
                finally:
                    dialog.destroy()
                from .setup_ui import IntegrationDialog
                dialog = ConfirmationDialog(app, {"id": "1"*32, "url": "recording:" + "1"*32,
                                                  "filename": "Gravacao.webm", "parts": 1,
                                                  "recording": {"ext": "webm"}})
                dialog.attributes("-alpha", 0.0)
                try:
                    recording_buttons = measure(dialog, {"Cancelar", "Salvar agora", "Só adicionar à fila"}, record=False)
                finally:
                    dialog.destroy()
                dialog = IntegrationDialog(root)
                dialog.attributes('-alpha', 0.0)
                try:
                    browser_buttons=set()
                    for tab in dialog.browser_tabs.tabs():
                        dialog.browser_tabs.select(tab)
                        browser_buttons.update(measure(dialog, record=False))
                    measurements.append({'window':dialog.title(),'buttons':sorted(browser_buttons)})
                finally:
                    dialog.destroy()
                from yt_dlp_ejs.yt import solver
                if not solver.core() or not solver.lib():
                    raise RuntimeError("Recursos JavaScript de mídia ausentes.")
            finally:
                manager.close()
                root.destroy()
                app.dispose()
            receipt = {"app": "lume-downloads", "app_name": APP_NAME, "version": VERSION, "tk": "ok", "queue": "ok",
                       "button_layout": measurements, "recording_buttons": recording_buttons, "media_scripts": "ok"}
            from .runtime_paths import private_runtime_roots
            if any(path.is_dir() for path in private_runtime_roots()):
                from .media import locate_node_runtime
                from .mux import find_media_tools
                ffmpeg, ffprobe = find_media_tools()
                receipt['private_runtime_paths'] = {'node': str(locate_node_runtime()),
                                                    'ffmpeg': str(ffmpeg), 'ffprobe': str(ffprobe)}
            (args.data_dir / "self-test.json").write_text(json.dumps(receipt), encoding="utf-8")
            if sys.stdout:
                print(json.dumps(receipt))
            return 0
        from .ui import run_gui
        run_gui(manager)
        return 0
    except Exception as error:
        if getattr(sys, "frozen", False) and sys.stdout is None:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("OpenDownloader", str(error), parent=root)
            root.destroy()
        elif sys.stderr:
            print("OpenDownloader: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
