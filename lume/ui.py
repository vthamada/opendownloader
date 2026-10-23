"""Portuguese desktop UI. Network and persistence belong to ``core.Manager``."""

from __future__ import annotations

import datetime as dt
import gc
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinter import font as tkfont
from typing import TYPE_CHECKING
from urllib.parse import urlsplit, urlunsplit
from .organization import FILE_CATEGORIES, task_category

if TYPE_CHECKING:
    from .core import Manager


STATUS = {
    "awaiting_confirmation": "Aguardando confirmação",
    "held": "Aguardando início",
    "queued": "Na fila",
    "connecting": "Conectando",
    "downloading": "Baixando",
    "paused": "Pausado",
    "completed": "Concluído",
    "cancelled": "Cancelado",
    "failed": "Falhou",
    "scheduled": "Agendado",
    "verifying": "Verificando",
}
ACTIVE = {"connecting", "downloading", "verifying"}
NAVY = "#183c33"
TEAL = "#2d765f"
PAPER = "#f0f3f5"
INK = "#1d3038"
MUTED = "#546673"
STATUS_FILTERS = ("Todos", "Aguardando", "Em andamento", "Pausados", "Agendados", "Concluídos", "Falhas")
DEFAULT_FORMAT_LABEL = "Automático — formato padrão do link"
YOUTUBE_FORMAT_LABEL = "Automático — melhor MP4 disponível"
MEDIA_FORMAT_LABEL = "Automático — formato compatível da fonte"
ORIGINAL_FORMAT_LABEL = "Arquivo original — sem conversão"

def export_url(value: str) -> str:
    """Keep chosen path/query/fragment, omitting HTTP authentication userinfo."""
    from .network import validate_url
    parsed = urlsplit(value)
    cleaned = urlunsplit((parsed.scheme, parsed.netloc.rsplit("@", 1)[-1], parsed.path, parsed.query, parsed.fragment))
    validate_url(cleaned)
    return cleaned


def _prepare_update_job(manifest: Path, package: Path, key: str, staging_parent: Path,
                        current_version: str,
                        results: queue.SimpleQueue[tuple[Path | None, str | None]]) -> None:
    """Worker arguments deliberately exclude Tk objects and UI closures."""
    try:
        from .updates import stage_update, verify_update
        verified = verify_update(manifest, package, key, current_version=current_version)
        results.put((stage_update(verified, staging_parent), None))
    except Exception as error:
        results.put((None, str(error)))


def _discover_media_job(url: str, stop: threading.Event,
                        results: queue.SimpleQueue, source: str | None = None) -> None:
    """Only plain metadata crosses from the catalogue worker to its Tk owner."""
    try:
        from .media import discover_media, media_source_kind
        if (source or media_source_kind(url)) in {'hls', 'dash'}:
            from .streaming import discover_stream
            catalogue = discover_stream(url, stop)
        else:
            catalogue = discover_media(url, stop)
        results.put((url, catalogue, None))
    except Exception as error:
        results.put((url, None, str(error)))


def _close_manager_job(manager: Manager, jobs: tuple[threading.Thread, ...],
                       results: queue.SimpleQueue[str | None]) -> None:
    """Close non-UI owners, returning data instead of traceback/UI references."""
    try:
        manager.close()
        for worker in jobs:
            worker.join()
        results.put(None)
    except Exception as error:
        results.put(str(error))


def _release_tk_state(owner: object) -> None:
    """Drop owned Tk references on the Tk thread, after widget destruction.

    Tk variables retain their interpreter and have a Tcl-calling destructor.
    Keeping them in obsolete Python widget cycles lets an unrelated worker's
    cyclic GC finalize Tcl on the wrong thread. Widgets are already destroyed;
    these references now have no UI purpose.
    """
    for name, value in list(vars(owner).items()):
        if isinstance(value, (tk.Variable, tk.Misc)):
            setattr(owner, name, None)
        elif isinstance(value, (list, dict)):
            values = value.values() if isinstance(value, dict) else value
            if any(isinstance(item, (tk.Variable, tk.Misc)) for item in values):
                value.clear()


class OwnedWindow(tk.Toplevel):
    """A dialog's variables/interpreter are released by its Tk-thread owner."""
    def destroy(self) -> None:
        if getattr(self, "_disposed", False):
            return
        self._disposed = True
        app = getattr(self, "app", None)
        super().destroy()
        if app is not None and not app.closing and app.tree.winfo_exists():
            app.tree.focus_set()
        _release_tk_state(self)
        # Toplevel subclasses also point back to App. Worker jobs never do.
        self.app = None
        self.tk = None


def format_bytes(value: int | float | None) -> str:
    if value is None:
        return "—"
    number = max(0.0, float(value))
    for suffix in ("B", "KB", "MB", "GB", "TB"):
        if number < 1024 or suffix == "TB":
            return f"{number:.0f} {suffix}" if suffix == "B" else f"{number:.1f} {suffix}"
        number /= 1024
    return "—"


def format_duration(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "—"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}min {seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}min"


def visible_url(value: str) -> str:
    """Display deliberately excludes userinfo, query and fragment."""
    try:
        parts = urlsplit(value)
        host = parts.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        if parts.port is not None:
            host += f":{parts.port}"
        return urlunsplit((parts.scheme, host, parts.path, "", ""))
    except (ValueError, TypeError):
        return "Endereço indisponível"


def _scrollable_form(parent: tk.Misc) -> ttk.Frame:
    """A form may grow with DPI; its fixed action row is owned by the dialog.

    The caller packs title/actions first, then this flexible viewport. Scroll
    and focus callbacks are registered on this dialog and destroyed with it.
    """
    viewport = ttk.Frame(parent)
    viewport.pack(fill="both", expand=True)
    viewport.columnconfigure(0, weight=1)
    viewport.rowconfigure(0, weight=1)
    canvas = tk.Canvas(viewport, background=PAPER, borderwidth=0, highlightthickness=0)
    canvas.grid(row=0, column=0, sticky="nsew")
    scrollbar = ttk.Scrollbar(viewport, orient="vertical", command=canvas.yview)
    scrollbar.grid(row=0, column=1, sticky="ns")
    canvas.configure(yscrollcommand=scrollbar.set)
    content = ttk.Frame(canvas, padding=(0, 0, 8, 0))
    window = canvas.create_window(0, 0, window=content, anchor="nw")
    content.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))

    def wheel(event) -> str | None:
        if isinstance(event.widget, (tk.Text, ttk.Spinbox, ttk.Combobox)):
            return None
        if canvas.winfo_exists() and canvas.winfo_ismapped() and event.delta:
            canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
            return "break"
        return None

    def reveal_focus(event) -> None:
        widget = event.widget
        if (not isinstance(widget, tk.Misc) or not canvas.winfo_exists()
                or not canvas.winfo_ismapped() or not str(widget).startswith(str(content) + ".")):
            return
        top = canvas.canvasy(0)
        y = widget.winfo_rooty() - content.winfo_rooty()
        height = canvas.winfo_height()
        if y < top:
            canvas.yview_moveto(y / max(1, content.winfo_height()))
        elif y + widget.winfo_height() > top + height:
            canvas.yview_moveto((y + widget.winfo_height() - height) / max(1, content.winfo_height()))

    dialog = parent.winfo_toplevel()
    dialog.bind("<MouseWheel>", wheel, add="+")
    dialog.bind("<FocusIn>", reveal_focus, add="+")
    return content


class AddDialog(OwnedWindow):
    """A user-owned selection before the manager is allowed to transfer bytes."""

    def __init__(self, app: App, request: dict | None = None):
        super().__init__(app.root)
        self.app = app
        self.request = dict(request) if request is not None else None
        self.recording_request = bool(request and request.get("recording"))
        self._catalog_stop = threading.Event()
        self._catalog_results = queue.SimpleQueue()
        self._catalog_poll_id = None
        self._catalog_start_id = None
        self.catalog_busy = False
        self.catalog_url = None
        self.catalog_default_name = None
        self.formats: list[dict] = []
        self.title(("Salvar gravação" if self.recording_request else "Confirmar download" if request else "Novo download") + " • OpenDownloader")
        self.configure(bg=PAPER)
        self.resizable(True, True)
        self.geometry("820x690")
        self.minsize(760, 590)
        self.transient(app.root)
        self.grab_set()
        self.result_count = 0
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        outer = ttk.Frame(self, padding=24)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Salve sua gravação" if self.recording_request else "Confira antes de baixar", style="Title.TLabel").pack(anchor="w")
        ttk.Label(outer, text=("A reprodução foi gravada. Escolha nome e pasta para conferir e salvar o arquivo." if self.recording_request else "Escolha nome, pasta e formato. A mídia só começa após sua confirmação."),
                  style="Muted.TLabel", wraplength=700).pack(anchor="w", pady=(3, 10))
        actions = ttk.Frame(outer)
        actions.pack(side="bottom", fill="x", pady=(12, 0))
        ttk.Button(actions, text="Cancelar", command=self.cancel).pack(side="right")
        self.download_button = ttk.Button(actions, text="Salvar agora" if self.recording_request else "Baixar agora", style="Accent.TButton", command=self.submit)
        self.download_button.pack(side="right", padx=(0, 8))
        self.later_button = ttk.Button(actions, text="Só adicionar à fila", command=lambda: self.submit(start=False))
        self.later_button.pack(side="right", padx=(0, 8))
        form = _scrollable_form(outer)
        self.urls = tk.Text(form, height=3, wrap="word", relief="solid", bd=1,
                            font=("Segoe UI", 10), padx=10, pady=8, undo=True)
        self.urls.pack(fill="x")
        if request:
            self.urls.insert("1.0", request["url"])
            self.urls.configure(state="disabled")
            ttk.Label(form, text="Fechar esta janela mantém o pedido pendente. Cancelar remove o pedido.",
                      style="Muted.TLabel", wraplength=680).pack(anchor="w", pady=(5, 0))
        else:
            self.urls.focus_set()
        row = ttk.Frame(form)
        row.pack(fill="x", pady=(12, 0))
        ttk.Label(row, text="Pasta de destino").pack(anchor="w")
        options = app.manager.get_options()
        self.directory = tk.StringVar(value=(request or {}).get("directory") or options.get("default_directory", str(Path.home() / "Downloads")))
        entry_row = ttk.Frame(row)
        entry_row.pack(fill="x", pady=(4, 0))
        ttk.Entry(entry_row, textvariable=self.directory).pack(side="left", fill="x", expand=True)
        ttk.Button(entry_row, text="Escolher…", command=self.choose_directory).pack(side="left", padx=(8, 0))
        queue_row = ttk.Frame(form)
        queue_row.pack(fill="x", pady=(8, 0))
        ttk.Label(queue_row, text="Fila").pack(side="left")
        self.queue_choices = {row["name"]: row["id"] for row in app.manager.list_queues()}
        self.queue_value = tk.StringVar(value=next(name for name, identity in self.queue_choices.items() if identity == "default"))
        ttk.Combobox(queue_row, textvariable=self.queue_value, values=tuple(self.queue_choices), state="readonly").pack(side="left", padx=(8, 0))
        ttk.Label(queue_row, text="Filas suspensas aguardam liberação.", style="Muted.TLabel").pack(side="left", padx=(8, 0))
        grid = ttk.Frame(form)
        grid.pack(fill="x", pady=(12, 0))
        grid.columnconfigure(0, weight=3)
        grid.columnconfigure(1, weight=1)
        ttk.Label(grid, text="Nome do arquivo (opcional para links manuais)").grid(row=0, column=0, sticky="w")
        ttk.Label(grid, text="Conexões por arquivo").grid(row=0, column=1, sticky="w", padx=(18, 0))
        self.filename = tk.StringVar(value=(request or {}).get("filename", ""))
        self.filename_entry = ttk.Entry(grid, textvariable=self.filename)
        self.filename_entry.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self.parts = tk.IntVar(value=(request or {}).get("parts", 4))
        ttk.Spinbox(grid, from_=1, to=8, textvariable=self.parts, width=6,
                    state="disabled" if self.recording_request else "normal").grid(row=1, column=1, sticky="w", padx=(18, 0), pady=(4, 0))
        self._suggested_name = self.filename.get()
        self.duplicate_policy = tk.StringVar(value="Salvar nova cópia")
        ttk.Label(form, text="Se o nome já existir").pack(anchor="w", pady=(12, 4))
        ttk.Combobox(form, textvariable=self.duplicate_policy, values=("Salvar nova cópia", "Manter nome e avisar"), state="readonly").pack(fill="x")
        ttk.Label(form, text="Nova cópia usa um nome livre, como arquivo (1).zip. O anterior é preservado.",
                  style="Muted.TLabel", wraplength=680).pack(anchor="w", pady=(4, 8))
        preview_row = ttk.Frame(form)
        preview_row.pack(fill="x", pady=(0, 4))
        ttk.Button(preview_row, text="Verificar destino", command=self.preview_destination).pack(side="left")
        ttk.Button(preview_row, text="Usar pasta da categoria", command=self.use_category_directory).pack(side="left", padx=(8, 0))
        self.existing_button = ttk.Button(preview_row, text="Abrir pasta do existente", command=self.open_existing_folder, state="disabled")
        self.existing_button.pack(side="left", padx=(8, 0))
        self.destination_status = tk.StringVar(value="A verificação mostra o nome disponível e preserva sua escolha no campo.")
        self._existing_path = None
        ttk.Label(form, textvariable=self.destination_status, style="Muted.TLabel", wraplength=680).pack(anchor="w", pady=(0, 8))
        format_row = ttk.Frame(form)
        format_row.pack(fill="x", pady=(4, 0))
        ttk.Label(format_row, text="Qualidade, áudio e legendas").pack(side="left")
        self.catalog_button = ttk.Button(format_row, text="Consultar formatos", command=self.load_formats)
        self.catalog_button.pack(side="right")
        self.format_value = tk.StringVar(value=DEFAULT_FORMAT_LABEL)
        self.format_box = ttk.Combobox(form, textvariable=self.format_value, values=(self.format_value.get(),), state="readonly")
        self.format_box.pack(fill="x", pady=(4, 0))
        self.format_box.bind("<<ComboboxSelected>>", self._format_changed)
        self.catalog_status = tk.StringVar(value="Fontes públicas compatíveis: consulte formatos e qualidades. Links diretos mantêm o arquivo original.")
        ttk.Label(form, textvariable=self.catalog_status, style="Muted.TLabel", wraplength=680).pack(anchor="w", pady=(5, 8))
        self.sha = tk.StringVar()
        if request is None:
            ttk.Label(form, text="SHA-256 esperado (opcional; somente um endereço)").pack(anchor="w", pady=(8, 4))
            ttk.Entry(form, textvariable=self.sha, font=("Consolas", 10)).pack(fill="x")
        self.authorized_keys = tk.StringVar()
        keys_label = ttk.Label(form, text="Chaves de conteúdo autorizadas (opcional; somente um endereço)")
        self.authorized_keys_entry = ttk.Entry(form, textvariable=self.authorized_keys, show="●", font=("Consolas", 10))
        if not self.recording_request:
            keys_label.pack(anchor="w", pady=(8, 4))
            self.authorized_keys_entry.pack(fill="x")
            ttk.Label(form, text="KID:CHAVE, com 32 caracteres hexadecimais em cada lado; separe pares por ponto e vírgula. Até 16 pares. As chaves ficam somente na memória até fechar o aplicativo; não são extraídas do navegador.",
                      style="Muted.TLabel", wraplength=680).pack(anchor="w", pady=(4, 8))
        schedule_row = ttk.Frame(form)
        schedule_row.pack(fill="x", pady=(12, 0))
        self.schedule_enabled = tk.BooleanVar(value=False)
        ttk.Checkbutton(schedule_row, text="Agendar para", variable=self.schedule_enabled,
                        command=self._toggle_schedule).grid(row=0, column=0, sticky="w")
        self.schedule_time = tk.StringVar(value=(dt.datetime.now() + dt.timedelta(hours=1)).strftime("%Y-%m-%d %H:%M"))
        self.schedule_entry = ttk.Entry(schedule_row, textvariable=self.schedule_time, width=19, state="disabled")
        self.schedule_entry.grid(row=0, column=1, sticky="w", padx=(8, 0))
        ttk.Label(schedule_row, text="AAAA-MM-DD HH:MM • horário local", style="Muted.TLabel").grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))
        if request:
            # Browser confirmation supports immediate or held queue actions. Manual
            # creation retains scheduling; the manager owns the persisted request.
            for widget in schedule_row.winfo_children():
                widget.configure(state="disabled")
            if request.get("error"):
                self.catalog_status.set(str(request["error"]))
                if not request.get("url"):
                    self.download_button.configure(state="disabled")
                    self.later_button.configure(state="disabled")
        self.bind("<Escape>", lambda _event: self.destroy())
        self.bind("<Control-l>", self.focus_urls)
        if request:
            if self._source_kind(request["url"]) is not None:
                self._catalog_start_id = self.after(100, self.load_formats)
            else:
                self.catalog_button.configure(state="disabled")
                self.format_value.set(ORIGINAL_FORMAT_LABEL)
                self.format_box.configure(values=(self.format_value.get(),))
                if not request.get("error"):
                    self.catalog_status.set("Formato original da gravação. A qualidade e o áudio foram escolhidos no navegador; o aplicativo não converte o vídeo." if self.recording_request else "Este endereço será transferido como arquivo original. Outras qualidades exigem um link de mídia compatível.")

    def focus_urls(self, _event=None):
        self.urls.focus_set()
        return "break"

    def use_category_directory(self):
        filename = self.filename.get().strip()
        if not filename:
            urls = self._urls()
            if len(urls) == 1:
                try:
                    filename = urlsplit(urls[0]).path.rsplit("/", 1)[-1]
                except ValueError:
                    self.destination_status.set("Confira o endereço antes de consultar a pasta da categoria.")
                    return
        directory = self.app.manager.suggest_directory(filename)
        if directory:
            self.directory.set(directory)
            self.destination_status.set("Pasta da categoria aplicada. Confira o destino antes de confirmar.")
        else:
            self.destination_status.set("Sem regra para esse nome. Configure as pastas nas Preferências ou escolha o destino.")

    def _urls(self) -> list[str]:
        return [line.strip() for line in self.urls.get("1.0", "end").splitlines() if line.strip()]

    def _source_kind(self, url: str) -> str | None:
        from .media import media_source_kind
        if self.recording_request:
            return None
        if self.request and self.request.get('url') == url and self.request.get('source') in {'hls', 'dash'}:
            return self.request['source']
        return media_source_kind(url)

    def _toggle_schedule(self) -> None:
        self.schedule_entry.configure(state="normal" if self.schedule_enabled.get() else "disabled")

    def choose_directory(self) -> None:
        selected = filedialog.askdirectory(parent=self, title="Escolha a pasta dos downloads", initialdir=self.directory.get() or str(Path.home()))
        if selected:
            self.directory.set(selected)

    def preview_destination(self) -> None:
        directory = self.directory.get().strip()
        filename = self.filename.get().strip()
        self._existing_path = None
        self.existing_button.configure(state="disabled")
        if not directory or not filename:
            self.destination_status.set("Informe pasta e nome, ou consulte os formatos, para verificar o destino.")
            return
        try:
            policy = "copy" if self.duplicate_policy.get() == "Salvar nova cópia" else "error"
            preview = self.app.manager.preview_destination(directory, filename, duplicate_policy=policy)
        except Exception as error:
            self.destination_status.set(str(error))
            return
        self._existing_path = preview.get("existing_path")
        self.existing_button.configure(state="normal" if self._existing_path else "disabled")
        detail = "Destino já reservado na fila. " if preview.get("reserved") else ("Já existe um arquivo. " if preview.get("conflict") else "Nome disponível. ")
        self.destination_status.set(detail + "Nome previsto: " + preview["filename"] + ". Será conferido novamente ao confirmar.")

    def open_existing_folder(self) -> None:
        # Always refresh the owner-provided preview: names/folders can be edited
        # while the dialog is open, and no stale path should be acted upon.
        self.preview_destination()
        if self._existing_path:
            self.app.open_directory(Path(self._existing_path).parent)

    def load_formats(self) -> None:
        self._catalog_start_id = None
        if self.catalog_busy:
            return
        urls = self._urls()
        if len(urls) != 1 or self._source_kind(urls[0]) is None:
            self.formats = []
            self.catalog_url = None
            self.catalog_default_name = None
            self.format_value.set(ORIGINAL_FORMAT_LABEL)
            self.format_box.configure(values=(ORIGINAL_FORMAT_LABEL,))
            self.catalog_status.set("Consulte uma página pública de plataforma compatível ou um manifest HLS/DASH. Links de arquivos usam o formato original.")
            return
        self.catalog_busy = True
        self.formats = []
        self.catalog_url = None
        self.format_value.set(YOUTUBE_FORMAT_LABEL if self._source_kind(urls[0]) == 'youtube' else MEDIA_FORMAT_LABEL)
        self.format_box.configure(values=(self.format_value.get(),))
        self.catalog_button.configure(state="disabled")
        self.catalog_status.set("Consultando qualidades e áudio disponíveis; nenhum conteúdo de mídia está sendo baixado…")
        self._catalog_stop = threading.Event()
        worker = threading.Thread(target=_discover_media_job, args=(urls[0], self._catalog_stop, self._catalog_results, self._source_kind(urls[0])), name="lume-media-catalogue", daemon=True)
        self.app.background_jobs[:] = [job for job in self.app.background_jobs if job.is_alive()]
        self.app.background_jobs.append(worker)
        self.app.background_stops[worker] = self._catalog_stop
        worker.start()
        self._catalog_poll_id = self.after(100, self._await_catalog)

    def _await_catalog(self) -> None:
        self._catalog_poll_id = None
        try:
            url, catalogue, error = self._catalog_results.get_nowait()
        except queue.Empty:
            self._catalog_poll_id = self.after(100, self._await_catalog)
            return
        self.catalog_busy = False
        self.catalog_button.configure(state="normal")
        if self._urls() != [url]:
            self.catalog_status.set("O endereço mudou. Consulte os formatos novamente.")
            return
        if error:
            self.catalog_status.set(error)
            return
        self.catalog_url = url
        self.catalog_default_name = catalogue.get("filename")
        self.formats = list(catalogue.get("formats", []))
        if self.filename.get() in ("", self._suggested_name) and catalogue.get("filename"):
            self.filename.set(catalogue["filename"])
            self._suggested_name = catalogue["filename"]
        default_label = YOUTUBE_FORMAT_LABEL if self._source_kind(url) == 'youtube' else MEDIA_FORMAT_LABEL
        labels = [default_label] + [f"{index + 1}. {item['label']}" for index, item in enumerate(self.formats)]
        self.format_box.configure(values=labels)
        self.format_value.set(labels[0])
        self.catalog_status.set(f"{catalogue.get('title', 'Vídeo')} • {len(self.formats)} opção(ões) disponível(is). Algumas versões não informam o tamanho.")

    def _selected_format(self) -> dict | None:
        index = self.format_box.current()
        return self.formats[index - 1] if index > 0 and index <= len(self.formats) else None

    def _format_changed(self, _event=None) -> None:
        selected = self._selected_format()
        if selected is None:
            if self.catalog_default_name and self.filename.get() in ("", self._suggested_name):
                self.filename.set(self.catalog_default_name)
                self._suggested_name = self.catalog_default_name
            self.catalog_status.set("A opção automática da fonte será resolvida novamente antes da transferência." if self.catalog_url else
                                    "O arquivo original será transferido sem conversão.")
            return
        if self.filename.get() in ("", self._suggested_name):
            name = selected["filename"]
            self.filename.set(name)
            self._suggested_name = name
        role = "Somente áudio" if selected.get("audio_only") else "Vídeo"
        size = format_bytes(selected.get("expected_size")) if selected.get("expected_size") is not None else "não informado"
        self.catalog_status.set(f"{role} • {selected['ext'].upper()} • tamanho: {size}. A versão escolhida será preservada na retomada.")
        if selected.get('audio_label') or selected.get('subtitle_label'):
            self.catalog_status.set(self.catalog_status.get()+f" Áudio: {selected.get('audio_label') or 'da origem'}. Legenda: {selected.get('subtitle_label') or 'sem legenda selecionada'}.")
        if selected.get('protected'):
            self.catalog_status.set(self.catalog_status.get()+" Fonte criptografada: exige chaves KID fornecidas com autorização; não são extraídas do navegador.")

    def cancel(self) -> None:
        if self.request is not None:
            try:
                self.app.manager.dismiss_request(self.request["id"])
            except Exception as error:
                messagebox.showerror("Pedido não cancelado", str(error), parent=self)
                return
            self.app.refresh()
        self.destroy()

    def submit(self, start: bool = True) -> None:
        urls = self._urls()
        if not urls:
            messagebox.showwarning("Falta um endereço", "Cole pelo menos um endereço HTTP ou HTTPS.", parent=self)
            return
        directory = self.directory.get().strip()
        if not directory:
            messagebox.showwarning("Escolha o destino", "Informe a pasta em que deseja salvar os arquivos.", parent=self)
            return
        filename = self.filename.get().strip() or None
        sha = self.sha.get().strip() or None
        from .crypto_keys import parse_authorized_keys
        try:
            authorized_keys = parse_authorized_keys(self.authorized_keys.get())
        except Exception as error:
            self.authorized_keys.set("")
            messagebox.showerror("Confira as chaves", str(error), parent=self)
            return
        self.authorized_keys.set("")
        selected = self._selected_format()
        if len(urls) > 1 and (filename or sha or selected or authorized_keys):
            messagebox.showwarning("Opções de arquivo único", "Use nome, formato, chaves ou SHA-256 personalizado ao adicionar somente um endereço.", parent=self)
            return
        if selected and self.catalog_url != urls[0]:
            messagebox.showwarning("Formato desatualizado", "O endereço mudou. Consulte os formatos novamente.", parent=self)
            return
        if selected and filename and Path(filename).suffix.lower() != "." + selected["ext"].lower():
            messagebox.showwarning("Confira o nome", f"O formato escolhido é {selected['ext'].upper()}. Use um nome terminado em .{selected['ext']}.", parent=self)
            return
        try:
            parts = self.parts.get()
            if not 1 <= parts <= 8:
                raise ValueError("Escolha entre 1 e 8 conexões por arquivo.")
            scheduled_at = None
            if start and self.schedule_enabled.get():
                scheduled_at = dt.datetime.strptime(self.schedule_time.get().strip(), "%Y-%m-%d %H:%M").timestamp()
                if scheduled_at <= dt.datetime.now().timestamp():
                    raise ValueError("Escolha uma data e hora futuras para agendar.")
        except (ValueError, tk.TclError) as error:
            messagebox.showerror("Confira os dados", str(error) or "Conexões ou horário inválidos.", parent=self)
            return
        duplicate_policy = "copy" if self.duplicate_policy.get() == "Salvar nova cópia" else "error"
        values = dict(start=start, duplicate_policy=duplicate_policy, media_selection=selected["id"] if selected else None,
                      queue_id=self.queue_choices[self.queue_value.get()])
        if authorized_keys:
            values["authorized_keys"] = authorized_keys
        rejected: list[str] = []
        errors: list[str] = []
        for url in urls:
            try:
                if self.request is not None:
                    self.app.manager.confirm_request(self.request["id"], directory, filename, parts, **values)
                else:
                    self.app.manager.add(url, directory, filename=filename, parts=parts, sha256=sha, scheduled_at=scheduled_at, **values)
                self.result_count += 1
            except Exception as error:
                rejected.append(url)
                errors.append(f"{visible_url(url)}: {error}")
        self.app.refresh()
        if rejected:
            if self.request is None:
                self.urls.delete("1.0", "end")
                self.urls.insert("1.0", "\n".join(rejected))
            detail = "\n".join(errors[:8])
            messagebox.showerror("Confira o pedido", detail, parent=self)
        else:
            self.app.notice.set(f"{self.result_count} download(s) {'iniciado(s)' if start else 'aguardando início na fila'}.")
            self.destroy()

    def destroy(self) -> None:
        if getattr(self, "_disposed", False):
            return
        if hasattr(self, "authorized_keys"):
            self.authorized_keys.set("")
        self._catalog_stop.set()
        for name in ("_catalog_poll_id", "_catalog_start_id"):
            handle = getattr(self, name, None)
            if handle is not None:
                self.after_cancel(handle)
                setattr(self, name, None)
        app = self.app
        super().destroy()
        if getattr(app, "_confirmation", None) is self:
            app._confirmation = None


class ConfirmationDialog(AddDialog):
    """Browser request already persisted by the manager; X keeps it for later."""

    def __init__(self, app: App, request: dict):
        super().__init__(app, request)


class SettingsDialog(OwnedWindow):
    def __init__(self, app: App):
        super().__init__(app.root)
        self.app = app
        self.title("Preferências • OpenDownloader")
        self.geometry("570x390")
        self.resizable(False, False)
        self.transient(app.root)
        self.grab_set()
        frame = ttk.Frame(self, padding=24)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Seu ritmo de download", style="Title.TLabel").pack(anchor="w", pady=(0, 18))
        actions = ttk.Frame(frame)
        actions.pack(side="bottom", fill="x", pady=(12, 0))
        ttk.Button(actions, text="Cancelar", command=self.destroy).pack(side="right")
        ttk.Button(actions, text="Salvar preferências", style="Accent.TButton", command=self.submit).pack(side="right", padx=(0, 8))
        body = _scrollable_form(frame)
        options = app.manager.get_options()
        self.max_active = tk.IntVar(value=options.get("max_active", 2))
        self.speed_limit = tk.IntVar(value=options.get("speed_limit_kib", 0))
        form = ttk.Frame(body)
        form.pack(fill="x")
        ttk.Label(form, text="Arquivos simultâneos").grid(row=0, column=0, sticky="w", pady=8)
        ttk.Spinbox(form, from_=1, to=3, textvariable=self.max_active, width=9).grid(row=0, column=1, padx=(16, 0))
        ttk.Label(form, text="Limite total de velocidade (KiB/s)").grid(row=1, column=0, sticky="w", pady=8)
        ttk.Spinbox(form, from_=0, to=1048576, increment=256, textvariable=self.speed_limit, width=9).grid(row=1, column=1, padx=(16, 0))
        ttk.Label(body, text="0 = sem limite. As mudanças valem para a fila atual.", style="Muted.TLabel", wraplength=410).pack(anchor="w", pady=(10, 16))
        self.browser_auto_start = tk.BooleanVar(value=bool(options.get("browser_auto_start", False)))
        ttk.Checkbutton(body, text="Iniciar automaticamente links enviados pelo navegador", variable=self.browser_auto_start).pack(anchor="w")
        ttk.Label(body, text="Desmarcado: o OpenDownloader pede confirmação de nome, pasta e formato. Marcado: usa as preferências atuais e salva duplicatas como nova cópia.",
                  style="Muted.TLabel", wraplength=480).pack(anchor="w", pady=(6, 12))
        ttk.Label(body, text="Pastas por categoria", style="Title.TLabel").pack(anchor="w", pady=(8, 4))
        ttk.Label(body, text="São sugestões: use o botão na confirmação. Campo vazio remove a regra. Arquivos existentes não são movidos.",
                  style="Muted.TLabel", wraplength=460).pack(anchor="w", pady=(0, 8))
        self.category_directories = {}
        for category in (*FILE_CATEGORIES, "Outros"):
            rule = ttk.Frame(body)
            rule.pack(fill="x", pady=3)
            ttk.Label(rule, text=category, width=13).pack(side="left")
            variable = tk.StringVar(value=options.get("category_directories", {}).get(category, ""))
            self.category_directories[category] = variable
            ttk.Entry(rule, textvariable=variable, width=18).pack(side="left", fill="x", expand=True)
            ttk.Button(rule, text="…", command=lambda value=variable: self.choose_rule_directory(value)).pack(side="left", padx=(4, 0))
        self.bind("<Escape>", lambda _event: self.destroy())

    def choose_rule_directory(self, variable):
        directory = filedialog.askdirectory(parent=self, initialdir=variable.get() or None)
        if directory:
            variable.set(directory)

    def submit(self) -> None:
        try:
            self.app.manager.set_options(max_active=self.max_active.get(), speed_limit_kib=self.speed_limit.get(),
                                         browser_auto_start=self.browser_auto_start.get(),
                                         category_directories={key: value.get().strip() for key, value in self.category_directories.items() if value.get().strip()})
        except (Exception, tk.TclError) as error:
            messagebox.showerror("Preferências inválidas", str(error), parent=self)
            return
        self.app.notice.set("Preferências salvas.")
        self.destroy()


class UpdateDialog(OwnedWindow):
    """Explicit offline release verification; no update program is executed."""

    def __init__(self, app: App):
        super().__init__(app.root)
        self.app = app
        self.busy = False
        self.prepared: Path | None = None
        self._poll_id: str | None = None
        self._result: queue.SimpleQueue[tuple[Path | None, str | None]] = queue.SimpleQueue()
        self.title("Preparar atualização assinada • OpenDownloader")
        self.geometry("760x490")
        self.minsize(680, 460)
        self.transient(app.root)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self.request_close)
        outer = ttk.Frame(self, padding=24)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Conferir antes de atualizar", style="Title.TLabel").pack(anchor="w")
        ttk.Label(outer, text="O OpenDownloader autentica o manifesto e o pacote, depois prepara uma pasta separada.\nNenhum arquivo será instalado ou executado automaticamente.", style="Muted.TLabel", wraplength=620).pack(anchor="w", pady=(5, 15))
        actions = ttk.Frame(outer)
        actions.pack(fill="x", side="bottom", pady=(12, 0))
        self.close_button = ttk.Button(actions, text="Fechar", command=self.request_close)
        self.close_button.pack(side="right")
        self.prepare_button = ttk.Button(actions, text="Verificar e preparar", style="Accent.TButton", command=self.prepare)
        self.prepare_button.pack(side="right", padx=(0, 8))
        self.open_button = ttk.Button(actions, text="Abrir pasta preparada", command=self.open_prepared, state="disabled")
        self.open_button.pack(side="left")
        self.form = _scrollable_form(outer)
        self.manifest = tk.StringVar()
        self.package = tk.StringVar()
        self.key = tk.StringVar()
        self.inputs: list[ttk.Widget] = []
        for label, variable, kind in (("Manifesto assinado (.json)", self.manifest, "manifest"), ("Pacote de atualização (.zip)", self.package, "package")):
            ttk.Label(self.form, text=label).pack(anchor="w", pady=(8, 3))
            row = ttk.Frame(self.form)
            row.pack(fill="x")
            entry = ttk.Entry(row, textvariable=variable)
            entry.pack(side="left", fill="x", expand=True)
            choose = ttk.Button(row, text="Escolher…", command=lambda k=kind: self.choose_file(k))
            choose.pack(side="left", padx=(8, 0))
            self.inputs.extend((entry, choose))
        ttk.Label(self.form, text="Chave pública confiável (Ed25519 em base64)").pack(anchor="w", pady=(14, 3))
        key_row = ttk.Frame(self.form)
        key_row.pack(fill="x")
        key_entry = ttk.Entry(key_row, textvariable=self.key, font=("Consolas", 10))
        key_entry.pack(side="left", fill="x", expand=True)
        read_key = ttk.Button(key_row, text="Ler de arquivo…", command=self.read_key)
        read_key.pack(side="left", padx=(8, 0))
        self.inputs.extend((key_entry, read_key))
        ttk.Label(self.form, text="Use uma chave fornecida por uma fonte confiável, independente do pacote.\nA chave não será salva. Sem uma chave confiável, a atualização é bloqueada.", style="Muted.TLabel", wraplength=600).pack(anchor="w", pady=(7, 13))
        self.status = tk.StringVar(value="Selecione os arquivos e informe a chave pública.")
        ttk.Label(self.form, textvariable=self.status, wraplength=600).pack(anchor="w", pady=(0, 12))

    def choose_file(self, kind: str) -> None:
        if self.busy:
            return
        filetypes = [("Manifesto JSON", "*.json")] if kind == "manifest" else [("Pacote ZIP", "*.zip")]
        selected = filedialog.askopenfilename(parent=self, title="Selecione o arquivo", filetypes=filetypes)
        if selected:
            (self.manifest if kind == "manifest" else self.package).set(selected)

    def read_key(self) -> None:
        if self.busy:
            return
        selected = filedialog.askopenfilename(parent=self, title="Leia a chave pública de uma fonte confiável", filetypes=[("Chave pública em texto", "*.txt *.pub"), ("Todos os arquivos", "*")])
        if not selected:
            return
        try:
            with Path(selected).open("rb") as stream:
                raw = stream.read(4097)
            if len(raw) > 4096:
                raise ValueError("O arquivo de chave pública é grande demais.")
            self.key.set(raw.decode("utf-8").strip())
        except (OSError, UnicodeError, ValueError) as error:
            messagebox.showerror("Chave pública indisponível", str(error), parent=self)

    def prepare(self) -> None:
        if self.busy or self.app.closing:
            return
        manifest = self.manifest.get().strip()
        package = self.package.get().strip()
        key = self.key.get().strip()
        if not manifest or not package or not key:
            messagebox.showerror("Atualização bloqueada", "Informe o manifesto, o pacote e uma chave pública confiável.", parent=self)
            return
        self.busy = True
        self.prepared = None
        self.prepare_button.configure(state="disabled")
        self.close_button.configure(state="disabled")
        self.open_button.configure(state="disabled")
        for widget in self.inputs:
            widget.configure(state="disabled")
        self.status.set("Conferindo assinatura, versão e integridade do pacote…")
        self._show_status()

        from . import VERSION
        worker = threading.Thread(target=_prepare_update_job,
                                  args=(Path(manifest), Path(package), key,
                                        Path(self.app.manager.data_dir) / "updates", VERSION,
                                        self._result),
                                  name="lume-verify-update", daemon=True)
        self.app.background_jobs[:] = [job for job in self.app.background_jobs if job.is_alive()]
        self.app.background_jobs.append(worker)
        worker.start()
        self._poll_id = self.after(150, self._await_result)

    def _await_result(self) -> None:
        self._poll_id = None
        try:
            prepared, error = self._result.get_nowait()
        except queue.Empty:
            self._poll_id = self.after(150, self._await_result)
            return
        self.busy = False
        self.prepare_button.configure(state="normal")
        self.close_button.configure(state="normal")
        for widget in self.inputs:
            widget.configure(state="normal")
        if error:
            self.status.set("Atualização bloqueada. Nenhum programa foi executado.")
            self._show_status()
            messagebox.showerror("Atualização bloqueada", error, parent=self)
            return
        self.prepared = Path(prepared)
        self.status.set(f"Pacote autenticado e preparado em:\n{self.prepared}\nA instalação continua sendo manual; nenhum programa foi executado.")
        self._show_status()
        self.open_button.configure(state="normal")
        self.app.notice.set("Atualização autenticada preparada em pasta separada. Nenhum arquivo foi executado.")

    def _show_status(self) -> None:
        self.update_idletasks()
        self.form.master.yview_moveto(1.0)

    def open_prepared(self) -> None:
        if self.prepared is None:
            return
        self.app.open_directory(self.prepared)

    def request_close(self) -> None:
        if not self.busy:
            self.destroy()

    def destroy(self) -> None:
        if getattr(self, "_disposed", False):
            return
        if self._poll_id is not None:
            self.after_cancel(self._poll_id)
            self._poll_id = None
        super().destroy()


class App:
    def __init__(self, root: tk.Tk, manager: Manager):
        self.root = root
        self.manager = manager
        self.tasks: dict[str, dict] = {}
        self.closing = False
        self._disposed = False
        self.background_jobs: list[threading.Thread] = []
        self.background_stops: dict[threading.Thread, threading.Event] = {}
        self._confirmation: ConfirmationDialog | None = None
        self._seen_requests: set[str] = set()
        self._close_result: queue.SimpleQueue[str | None] = queue.SimpleQueue()
        self._poll_id: str | None = None
        self._close_poll_id: str | None = None
        self.root.title("OpenDownloader")
        self.root.geometry("1160x740")
        self.root.minsize(980, 620)
        self.root.configure(bg=PAPER)
        self.root.protocol("WM_DELETE_WINDOW", self.begin_close)
        self.root.bind("<Destroy>", self._on_destroy, add="+")
        self._styles()
        self._build()
        self.refresh()
        self._poll_id = self.root.after(500, self._poll)
        self.root.bind("<Control-n>", lambda _event: self.new_download())
        self.root.bind("<Control-f>", self.focus_search)
        self.root.bind("<Control-i>", lambda _event: self.import_urls())
        self.root.bind("<Control-e>", lambda _event: self.export_urls())

    def _styles(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", font=("Segoe UI", 10), background=PAPER, foreground=INK)
        style.configure("TFrame", background=PAPER)
        style.configure("TLabel", background=PAPER)
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 16), foreground=INK)
        style.configure("Section.TLabel", font=("Segoe UI Semibold", 9), foreground=MUTED)
        style.configure("TButton", font=("Segoe UI", 10), width=0, padding=(9, 7), background="white", foreground=INK, borderwidth=1)
        style.map("TButton", background=[("disabled", "#e3eaf1"), ("active", "#e5edf5")],
                  foreground=[("disabled", "#536779"), ("!disabled", INK)])
        style.configure("Accent.TButton", background=TEAL, foreground="white", borderwidth=0, padding=(11, 8))
        style.map("Accent.TButton", background=[("disabled", "#d5e3e6"), ("active", "#245f4d")],
                  foreground=[("disabled", "#405d62"), ("!disabled", "white")])
        style.configure("Header.TButton", background=NAVY, foreground="white", borderwidth=0, padding=(10, 5))
        style.map("Header.TButton", background=[("disabled", "#d5e3e6"), ("active", "#2b5448")],
                  foreground=[("disabled", "#405d62"), ("!disabled", "white")])
        line_height = tkfont.Font(root=self.root, font=("Segoe UI", 10)).metrics("linespace")
        style.configure("Treeview", background="white", fieldbackground="white", rowheight=max(28, line_height + 9), borderwidth=0)
        style.configure("Treeview.Heading", background="#e7edf4", foreground=INK, font=("Segoe UI Semibold", 10), padding=3)
        style.map("Treeview", background=[("selected", "#d8eeee")], foreground=[("selected", INK)])
        style.configure("Navigation.Treeview", background=PAPER, fieldbackground=PAPER,
                        rowheight=max(24, line_height + 6), font=("Segoe UI", 10))
        style.map("Navigation.Treeview", background=[("selected", "#d8e8e0")],
                  foreground=[("selected", "#183c33")])
        style.configure("TEntry", padding=6)
        style.configure("Lume.Horizontal.TProgressbar", background=TEAL, troughcolor="#dfe8ed", borderwidth=0)

    def _build(self) -> None:
        menu = tk.Menu(self.root)
        file_menu = tk.Menu(menu, tearoff=False)
        file_menu.add_command(label="Novo download…", accelerator="Ctrl+N", command=self.new_download)
        file_menu.add_command(label="Importar lista de URLs…", accelerator="Ctrl+I", command=self.import_urls)
        file_menu.add_command(label="Exportar URLs selecionadas ou visíveis…", accelerator="Ctrl+E", command=self.export_urls)
        file_menu.add_separator()
        file_menu.add_command(label="Preferências…", command=self.settings)
        file_menu.add_command(label="Fechar", command=self.begin_close)
        menu.add_cascade(label="Arquivo", menu=file_menu)
        queue_menu = tk.Menu(menu, tearoff=False)
        queue_menu.add_command(label="Buscar na fila", accelerator="Ctrl+F", command=self.focus_search)
        queue_menu.add_command(label="Antecipar na fila", accelerator="Alt+↑", command=lambda: self.move_selected(-1))
        queue_menu.add_command(label="Adiar na fila", accelerator="Alt+↓", command=lambda: self.move_selected(1))
        queue_menu.add_command(label="Substituir endereço expirado…", command=self.replace_source_selected)
        queue_menu.add_separator()
        queue_menu.add_command(label="Nova fila…", command=self.create_queue)
        queue_menu.add_command(label="Suspender novos inícios da fila exibida", command=lambda: self.set_displayed_queue(False))
        queue_menu.add_command(label="Liberar novos inícios da fila exibida", command=lambda: self.set_displayed_queue(True))
        queue_menu.add_command(label="Mover seleção para outra fila…", command=self.assign_selected_queue)
        menu.add_cascade(label="Fila", menu=queue_menu)
        integration = tk.Menu(menu, tearoff=False)
        integration.add_command(label="Conectar Chrome e Edge…", command=self.integration_center)
        integration.add_command(label="Como gravar a reprodução…", command=self.recording_help)
        menu.add_cascade(label="Navegadores", menu=integration)
        security = tk.Menu(menu, tearoff=False)
        security.add_command(label="Preparar atualização assinada…", command=self.prepare_update)
        menu.add_cascade(label="Segurança", menu=security)
        help_menu = tk.Menu(menu, tearoff=False)
        help_menu.add_command(label="Compatibilidade de vídeos…", command=self.media_help)
        help_menu.add_command(label="Sobre o OpenDownloader", command=self.about)
        menu.add_cascade(label="Ajuda", menu=help_menu)
        self.root.configure(menu=menu)
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(2, weight=1)

        # Compact identity strip; queue controls, rather than promotional cards,
        # own the vertical space. Every action remains a native labeled control.
        header = tk.Frame(self.root, background=NAVY, padx=12, pady=6)
        header.grid(row=0, column=0, sticky="ew")
        tk.Label(header, text="↓", font=("Segoe UI", 17), fg="#e7f0ba", bg=NAVY).pack(side="left", padx=(0, 8))
        tk.Label(header, text="OpenDownloader", font=("Segoe UI Semibold", 14), fg="white", bg=NAVY).pack(side="left")
        self.settings_button = ttk.Button(header, text="Preferências", style="Header.TButton", command=self.settings)
        self.settings_button.pack(side="right")
        self.browser_button = ttk.Button(header, text="Navegadores", style="Header.TButton", command=self.integration_center)
        self.browser_button.pack(side="right", padx=(0, 6))
        toolbar = ttk.Frame(self.root, padding=(10, 7))
        toolbar.grid(row=1, column=0, sticky="ew")
        self.add_button = ttk.Button(toolbar, text="＋ Novo download", style="Accent.TButton", command=self.new_download)
        self.add_button.pack(side="left", padx=(0, 8))
        self.action_buttons: dict[str, ttk.Button] = {}
        for text, command in (("Continuar", lambda: self.apply_action("resume")),
                              ("Pausar", lambda: self.apply_action("pause")),
                              ("Reiniciar", self.restart_selected), ("Cancelar", self.cancel_selected),
                              ("Remover", self.remove_selected)):
            button = ttk.Button(toolbar, text=text, command=command)
            button.pack(side="left", padx=(0, 4))
            self.action_buttons[text] = button
        self.folder_button = ttk.Button(toolbar, text="Abrir pasta", command=self.open_folder)
        self.folder_button.pack(side="left", padx=(4, 4))
        self.details_button = ttk.Button(toolbar, text="Detalhes", command=self.show_details)
        self.details_button.pack(side="left")

        body = ttk.Frame(self.root, padding=(10, 0, 10, 0))
        body.grid(row=2, column=0, sticky="nsew")
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)
        sidebar = ttk.Frame(body, padding=(0, 3, 10, 0))
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.columnconfigure(0, weight=1)
        sidebar.rowconfigure(1, weight=1)
        ttk.Label(sidebar, text="BIBLIOTECA", style="Section.TLabel").grid(row=0, column=0, sticky="w", pady=(1, 6))
        self.navigation = ttk.Treeview(sidebar, columns=("count",), show="tree", style="Navigation.Treeview",
                                       selectmode="browse", height=4, takefocus=True)
        self.navigation.column("#0", width=157, minwidth=120, stretch=False)
        self.navigation.column("count", width=35, minwidth=30, stretch=False, anchor="e")
        self.navigation.grid(row=1, column=0, sticky="nsew")
        nav_scroll = ttk.Scrollbar(sidebar, orient="vertical", command=self.navigation.yview)
        nav_scroll.grid(row=1, column=1, sticky="ns")
        self.navigation.configure(yscrollcommand=nav_scroll.set)
        self.navigation.tag_configure("group", font=("Segoe UI Semibold", 9), foreground=MUTED)
        self.navigation.tag_configure("active", font=("Segoe UI Semibold", 10), foreground="#205943")
        self.navigation_filters: dict[str, tuple[str, str]] = {}
        for group, title, values in (("status", "SITUAÇÃO", STATUS_FILTERS),
                                      ("category", "TIPO DE ARQUIVO", ("Todos os tipos", *FILE_CATEGORIES, "Outros"))):
            self.navigation.insert("", "end", iid=group, text=title, open=True, tags=("group",))
            for index, label in enumerate(values):
                identity = f"{group}.{index}"
                self.navigation.insert(group, "end", iid=identity, text=label, values=(0,))
                self.navigation_filters[identity] = (group, label)
        self.navigation.bind("<<TreeviewSelect>>", self.select_navigation)
        self.navigation.bind("<Return>", self.select_navigation)
        self.clear_button = ttk.Button(sidebar, text="Limpar filtros", command=self.clear_filters)
        self.clear_button.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(7, 0))

        main = ttk.Frame(body)
        main.grid(row=0, column=1, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(2, weight=1)
        selectors = ttk.Frame(main, padding=(0, 0, 0, 7))
        selectors.grid(row=0, column=0, sticky="ew")
        selectors.columnconfigure(3, weight=1)
        ttk.Label(selectors, text="Fila").grid(row=0, column=0, padx=(0, 6))
        self.queue_filter = tk.StringVar(value="Todas as filas")
        self.queue_box = ttk.Combobox(selectors, textvariable=self.queue_filter, state="readonly", width=13)
        self.queue_box.grid(row=0, column=1, sticky="w")
        self.queue_box.bind("<<ComboboxSelected>>", lambda _event: self.refresh())
        ttk.Label(selectors, text="Buscar").grid(row=0, column=2, padx=(12, 6))
        self.search_value = tk.StringVar()
        self.search_entry = ttk.Entry(selectors, textvariable=self.search_value, width=10)
        self.search_entry.grid(row=0, column=3, sticky="ew")
        self.search_entry.bind("<Escape>", lambda _event: self.search_value.set(""))
        self.confirm_button = ttk.Button(selectors, text="Confirmar…", command=self.confirm_selected, state="disabled")
        self.confirm_button.grid(row=0, column=4, sticky="e", padx=(8, 0))
        filters = ttk.Frame(main, padding=(0, 0, 0, 8))
        filters.grid(row=1, column=0, sticky="ew")
        filters.columnconfigure(5, weight=1)
        self.filter_value = tk.StringVar(value="Todos")
        ttk.Label(filters, text="Estado").grid(row=0, column=0, padx=(0, 5))
        self.filter_box = ttk.Combobox(filters, textvariable=self.filter_value, values=STATUS_FILTERS, state="readonly", width=13)
        self.filter_box.grid(row=0, column=1, sticky="w")
        self.filter_box.bind("<<ComboboxSelected>>", lambda _event: self.refresh())
        self.category_value = tk.StringVar(value="Todos os tipos")
        ttk.Label(filters, text="Tipo").grid(row=0, column=2, padx=(10, 5))
        self.category_box = ttk.Combobox(filters, textvariable=self.category_value,
                                        values=("Todos os tipos", *FILE_CATEGORIES, "Outros"), state="readonly", width=13)
        self.category_box.grid(row=0, column=3, sticky="w")
        self.category_box.bind("<<ComboboxSelected>>", lambda _event: self.refresh())
        self.sort_value = tk.StringVar(value="Ordem da fila")
        ttk.Label(filters, text="Ordenar").grid(row=0, column=4, padx=(10, 5))
        self.sort_box = ttk.Combobox(filters, textvariable=self.sort_value,
                                    values=("Ordem da fila", "Nome A–Z", "Maior tamanho", "Menor tamanho"), state="readonly", width=13)
        self.sort_box.grid(row=0, column=5, sticky="e")
        self.sort_box.bind("<<ComboboxSelected>>", lambda _event: self.refresh())

        table_frame = ttk.Frame(main)
        table_frame.grid(row=2, column=0, sticky="nsew")
        columns = ("name", "status", "progress", "size", "speed", "eta", "parts")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="extended", height=3)
        headings = {"name": ("Arquivo", 280), "status": ("Estado", 118), "progress": ("Progresso", 88),
                    "size": ("Recebido / total", 153), "speed": ("Velocidade", 96), "eta": ("Restante", 88), "parts": ("Partes", 60)}
        for column, (label, width) in headings.items():
            self.tree.heading(column, text=label)
            self.tree.column(column, width=width, minwidth=60, stretch=column == "name", anchor="w" if column == "name" else "center")
        self.tree.heading("name", command=lambda: self.sort_column("name"))
        self.tree.heading("size", command=lambda: self.sort_column("size"))
        self.tree.grid(row=0, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        self.tree.tag_configure("completed", foreground="#226249")
        self.tree.tag_configure("failed", foreground="#a12d32")
        self.tree.tag_configure("paused", foreground="#815912")
        self.tree.tag_configure("awaiting_confirmation", foreground="#79551a")
        self.tree.tag_configure("held", foreground=MUTED)
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self.update_details())
        self.tree.bind("<Double-1>", lambda _event: self.show_details())
        self.tree.bind("<Return>", lambda _event: self.show_details())
        self.tree.bind("<Delete>", lambda _event: self.remove_selected())
        self.tree.bind("<Alt-Up>", lambda _event: self.move_selected(-1))
        self.tree.bind("<Alt-Down>", lambda _event: self.move_selected(1))
        self.tree.bind("<space>", self.toggle_selected)
        self.tree.bind("<Control-Return>", self.activate_selected)
        self.tree.bind("<Button-3>", self.show_context_menu)
        self.tree.bind("<Shift-F10>", self.show_context_menu)
        self.context_menu = tk.Menu(self.root, tearoff=False)
        self._context_actions: dict[int, str] = {}
        self.context_menu.add_command(label="Confirmar…", command=self.confirm_selected)
        for label in ("Continuar", "Pausar", "Reiniciar", "Cancelar", "Remover"):
            self.context_menu.add_command(label=label, command=self.action_buttons[label].invoke)
            self._context_actions[self.context_menu.index("end")] = label
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Abrir pasta", command=self.open_folder)
        self.context_menu.add_command(label="Detalhes", command=self.show_details)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Substituir endereço expirado…", command=self.replace_source_selected)
        self.context_menu.add_command(label="Mover para outra fila…", command=self.assign_selected_queue)
        self.empty_label = ttk.Label(self.tree, text="Sua fila está pronta.\n\nClique em ＋ Novo download ou pressione Ctrl+N para começar.",
                                     anchor="center", justify="center", style="Muted.TLabel")
        self.empty_label.place(relx=0.5, rely=0.45, anchor="center")

        details = ttk.Frame(main, padding=(0, 8, 0, 4))
        details.grid(row=3, column=0, sticky="ew")
        details.columnconfigure(0, weight=1)
        self.selected_name = tk.StringVar(value="Escolha um download para ver os detalhes.")
        self.selected_info = tk.StringVar(value="")
        ttk.Label(details, textvariable=self.selected_name, font=("Segoe UI Semibold", 10), width=1).grid(row=0, column=0, sticky="ew")
        self.progress = ttk.Progressbar(details, style="Lume.Horizontal.TProgressbar", maximum=100)
        self.progress.grid(row=1, column=0, sticky="ew", pady=(5, 4))
        # The full source/error remains in Details; one line cannot push actions
        # outside a compact window or expose credentials through a second parser.
        ttk.Label(details, textvariable=self.selected_info, style="Muted.TLabel", width=1, anchor="w").grid(row=2, column=0, sticky="ew")
        footer = ttk.Frame(self.root, padding=(10, 6))
        footer.grid(row=3, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        self.notice = tk.StringVar(value="Pronto. Ctrl+N: novo download • Ctrl+F: buscar")
        ttk.Label(footer, textvariable=self.notice, style="Muted.TLabel", width=1).grid(row=0, column=0, sticky="ew")
        self.count_value = tk.StringVar(value="0")
        self.active_value = tk.StringVar(value="0")
        self.speed_value = tk.StringVar(value="0 B/s")
        self.done_value = tk.StringVar(value="0")
        for column, (caption, variable) in enumerate((("Na lista", self.count_value), ("Ativos", self.active_value),
                                                     ("Total", self.speed_value)), 1):
            metric = ttk.Frame(footer)
            metric.grid(row=0, column=column, padx=(15, 0))
            ttk.Label(metric, text=caption + ":", style="Muted.TLabel").pack(side="left", padx=(0, 4))
            ttk.Label(metric, textvariable=variable).pack(side="left")
        self.search_value.trace_add("write", lambda *_args: self.refresh())

    def select_navigation(self, _event=None) -> str:
        selection = self.navigation.selection()
        choice = self.navigation_filters.get(selection[0]) if selection else None
        if choice is not None and not self.closing:
            kind, value = choice
            (self.filter_value if kind == "status" else self.category_value).set(value)
            self.refresh()
        return "break"

    def clear_filters(self) -> None:
        if self.closing:
            return
        self.queue_filter.set("Todas as filas")
        self.filter_value.set("Todos")
        self.category_value.set("Todos os tipos")
        self.navigation.selection_remove(self.navigation.selection())
        self.search_value.set("")  # The existing search trace owns one refresh.

    def sort_column(self, column: str) -> None:
        if self.closing:
            return
        current = self.sort_value.get()
        if column == "name":
            self.sort_value.set("Ordem da fila" if current == "Nome A–Z" else "Nome A–Z")
        elif column == "size":
            self.sort_value.set("Menor tamanho" if current == "Maior tamanho" else "Maior tamanho")
        else:
            return
        self.refresh()

    def show_context_menu(self, event) -> str:
        if self.closing:
            return "break"
        keyboard = getattr(event, "keysym", "") == "F10"
        if not keyboard:
            identity = self.tree.identify_row(event.y)
            if not identity:
                return "break"
            if identity not in self.tree.selection():
                self.tree.selection_set(identity)
            self.tree.focus(identity)
        if not self.selected():
            return "break"
        self.update_details()
        self.context_menu.entryconfigure(0, state=self.confirm_button.cget("state"))
        for index, label in self._context_actions.items():
            self.context_menu.entryconfigure(index, state=self.action_buttons[label].cget("state"))
        if keyboard:
            box = self.tree.bbox(self.tree.selection()[0])
            x = self.tree.winfo_rootx() + 20
            y = self.tree.winfo_rooty() + (box[1] + box[3] if box else 20)
        else:
            x, y = event.x_root, event.y_root
        try:
            self.context_menu.tk_popup(x, y)
        finally:
            self.context_menu.grab_release()
        return "break"

    def new_download(self) -> None:
        if not self.closing:
            AddDialog(self)

    def recording_help(self) -> None:
        messagebox.showinfo("Gravar reprodução", "No Chrome ou Edge, abra a extensão OpenDownloader e clique em Gravar reprodução.\n\nEscolha formato, qualidade de codificação e duração máxima. Clique em Escolher tela ou aba e selecione a superfície no navegador. Ajuste a área arrastando na prévia ou pelos campos em pixels e clique em Gravar área selecionada. Superfície inteira restaura a captura sem recorte. Para gravar som, compartilhe o áudio da aba. A prévia é silenciosa para evitar eco.\n\nUse Pausar, Continuar e Parar e salvar na aba de gravação. Ao terminar, confirme nome e pasta neste aplicativo. A gravação acontece durante a reprodução; conteúdo protegido pode ser bloqueado pelo navegador.\n\nOs originais e parciais ficam na pasta recordings dos dados do aplicativo. Cancelar um pedido não apaga o original.", parent=self.root)

    def media_help(self) -> None:
        messagebox.showinfo("Compatibilidade de vídeos", "Downloads: arquivos HTTP(S), páginas públicas compatíveis e HLS/DASH sob demanda. Em Consultar formatos, escolha qualidade, faixa de áudio e legenda WebVTT disponível. HLS aceita legendas VOD segmentadas; DASH aceita um arquivo WebVTT BaseURL. Legendas são incorporadas no MP4. Transmissões ao vivo e legendas TTML/fMP4 ainda não são suportadas.\n\nCriptografia: HLS AES-128/identity; SAMPLE-AES com H.264/AAC em TS; DASH CENC/CBCS nos contêineres suportados. Exigem acesso autorizado às chaves. As chaves explícitas ficam somente na sessão.\n\nA extensão informa quando observa a API EME no player. Isso não identifica qual DRM está em uso nem garante que as fontes detectadas correspondam ao filme. Um anúncio também pode aparecer.\n\nGravação: seleção explícita de aba/tela, som compartilhado, prévia e limite de duração. Pode ser bloqueada ou resultar em imagem preta/sem som. Login, conectores oficiais e licenças offline dependem do provedor; não são habilitados pela assinatura do serviço neste aplicativo.", parent=self.root)

    def focus_search(self, _event=None) -> str:
        self.search_entry.focus_set()
        self.search_entry.selection_range(0, "end")
        return "break"

    def import_urls(self) -> None:
        if self.closing:
            return
        from .network import DownloadError, validate_url
        source = filedialog.askopenfilename(parent=self.root, title="Importar URLs para confirmação",
                                            filetypes=(("Lista de URLs UTF-8", "*.txt"), ("Todos os arquivos", "*.*")))
        if not source:
            return
        try:
            # Bound the text retained by the interactive editor before decoding.
            with Path(source).open("rb") as stream:
                data = stream.read(4 * 1024 * 1024 + 1)
            if len(data) > 4 * 1024 * 1024:
                raise ValueError("Divida a lista em arquivos de até 4 MiB para usar o editor.")
            urls = [line.strip() for line in data.decode("utf-8-sig").splitlines() if line.strip()]
            if not urls:
                raise ValueError("O arquivo não contém URLs.")
            for index, url in enumerate(urls, 1):
                try:
                    validate_url(url)
                except DownloadError:
                    raise ValueError(f"Linha {index}: informe um endereço HTTP/HTTPS válido.") from None
            dialog = AddDialog(self)
            dialog.urls.insert("1.0", "\n".join(urls))
            dialog.urls.focus_set()
            self.notice.set(f"{len(urls)} URL(s) importada(s). Confira destino e confirme antes de baixar.")
        except (OSError, UnicodeError, ValueError) as error:
            messagebox.showerror("Lista não importada", str(error), parent=self.root)

    def export_urls(self) -> None:
        if self.closing:
            return
        from .network import DownloadError
        tasks = self.selected() or [self.tasks[identity] for identity in self.tree.get_children()]
        tasks = [task for task in tasks if task.get("kind") != "recording" and not task.get("recording")]
        if not tasks:
            self.notice.set("Selecione downloads com URL HTTP/HTTPS para exportar. Gravações são arquivos locais.")
            return
        target = filedialog.asksaveasfilename(parent=self.root, title="Exportar URLs completas (sem usuário/senha HTTP)",
                                             defaultextension=".txt", initialfile="lume-urls.txt",
                                             filetypes=(("Lista de URLs UTF-8", "*.txt"),))
        if not target:
            return
        try:
            content = "\n".join(export_url(task["url"]) for task in tasks) + "\n"
            Path(target).write_text(content, encoding="utf-8")
            self.notice.set(f"{len(tasks)} URL(s) exportada(s). Parâmetros e fragmentos originais foram preservados.")
        except (OSError, ValueError, DownloadError) as error:
            messagebox.showerror("Lista não exportada", str(error), parent=self.root)

    def move_selected(self, offset: int) -> None:
        if self.closing:
            return
        tasks = self.selected()
        if len(tasks) != 1 or tasks[0].get("_is_request"):
            self.notice.set("Escolha um download confirmado para alterar sua ordem na fila.")
            return
        try:
            self.manager.move(tasks[0]["id"], offset)
            self.sort_value.set("Ordem da fila")
            self.refresh()
            self.tree.see(tasks[0]["id"])
            self.notice.set("Ordem da fila atualizada. Downloads em andamento não foram interrompidos.")
        except Exception as error:
            messagebox.showerror("Não foi possível reordenar", str(error), parent=self.root)

    def replace_source_selected(self) -> None:
        if self.closing:
            return
        tasks = self.selected()
        if len(tasks) == 1 and (tasks[0].get("kind") == "recording" or tasks[0].get("recording")):
            self.notice.set("Gravações são arquivos locais e não possuem endereço substituível.")
            return
        if len(tasks) != 1 or tasks[0].get("_is_request") or tasks[0].get("status") not in {"failed", "paused", "held"}:
            self.notice.set("Escolha um download pausado, com falha ou aguardando início para trocar o endereço.")
            return
        url = simpledialog.askstring("Substituir endereço", "Cole o novo endereço HTTP/HTTPS:", parent=self.root)
        if not url:
            return
        if not messagebox.askyesno("Recomeçar com novo endereço", "A troca descartará os dados parciais deste download e exigirá baixar desde o início. O arquivo final existente será preservado. A tarefa aguardará seu comando Continuar. Prosseguir?", parent=self.root):
            return
        try:
            self.manager.replace_source(tasks[0]["id"], url.strip())
            self.refresh()
            self.notice.set("Endereço substituído. Confira o download e clique em Continuar quando desejar.")
        except Exception as error:
            messagebox.showerror("Endereço não substituído", str(error), parent=self.root)

    def settings(self) -> None:
        if not self.closing:
            SettingsDialog(self)

    def prepare_update(self) -> None:
        if not self.closing:
            UpdateDialog(self)

    def integration_center(self) -> None:
        if not self.closing:
            from .setup_ui import IntegrationDialog
            IntegrationDialog(self.root)

    def _poll(self) -> None:
        if self.closing:
            return
        self.background_jobs[:] = [job for job in self.background_jobs if job.is_alive()]
        self.background_stops = {job: stop for job, stop in self.background_stops.items() if job.is_alive()}
        self.refresh()
        self._open_next_request()
        self._poll_id = self.root.after(500, self._poll)

    def _open_next_request(self) -> None:
        if self.closing or self._confirmation is not None or self.root.grab_current() is not None:
            return
        request = next((item for item in self.tasks.values()
                        if item.get("_is_request") and item["id"] not in self._seen_requests), None)
        if request:
            self._show_confirmation(request)

    def _show_confirmation(self, request: dict) -> None:
        if self._confirmation is not None:
            self._confirmation.lift()
            return
        if self.closing:
            return
        self.root.deiconify()
        self.root.lift()
        self._confirmation = ConfirmationDialog(self, request)
        self._seen_requests.add(request["id"])
        self._confirmation.lift()

    def confirm_selected(self) -> None:
        selected = self.selected()
        request = next((item for item in selected if item.get("_is_request")), None)
        if request is None:
            request = next((item for item in self.tasks.values() if item.get("_is_request")), None)
        if request:
            self._show_confirmation(request)

    def create_queue(self):
        name = simpledialog.askstring("Nova fila", "Nome da fila:", parent=self.root)
        if name is None:
            return
        try:
            self.manager.create_queue(name)
            self.queue_filter.set(name.strip())
            self.refresh()
        except Exception as error:
            messagebox.showerror("Fila não criada", str(error), parent=self.root)

    def set_displayed_queue(self, enabled):
        identity = self.queue_choices.get(self.queue_filter.get())
        if identity is None:
            messagebox.showinfo("Escolha uma fila", "Escolha uma fila no filtro Fila.", parent=self.root)
            return
        try:
            self.manager.set_queue_enabled(identity, enabled)
            self.notice.set("Fila liberada." if enabled else "Novos inícios suspensos. Transferências em andamento continuam.")
            self.refresh()
        except Exception as error:
            messagebox.showerror("Fila não alterada", str(error), parent=self.root)

    def assign_selected_queue(self):
        tasks = [task for task in self.selected() if not task.get("_is_request")]
        if not tasks:
            return
        name = simpledialog.askstring("Mover para fila", "Informe o nome da fila: " + ", ".join(self.queue_choices), parent=self.root)
        if name is None:
            return
        identity = self.queue_choices.get(name.strip())
        if identity is None:
            messagebox.showerror("Fila inexistente", "Escolha um dos nomes exibidos.", parent=self.root)
            return
        errors = []
        for task in tasks:
            try:
                self.manager.assign_queue(task["id"], identity)
            except Exception as error:
                errors.append(str(error))
        if errors:
            messagebox.showerror("Atribuição não concluída", "\n".join(errors[:8]), parent=self.root)
        self.refresh()

    def toggle_selected(self, _event=None):
        for task in self.selected():
            if task.get("_is_request"):
                continue
            action = "pause" if task.get("status") in ACTIVE | {"queued", "scheduled"} else "resume"
            if task.get("status") in {"completed", "cancelled"}:
                continue
            try:
                if action == "resume" and not self._ensure_authorized_keys(task):
                    continue
                getattr(self.manager, action)(task["id"])
            except Exception as error:
                messagebox.showerror("Ação não concluída", str(error), parent=self.root)
        self.refresh()
        return "break"

    def activate_selected(self, _event=None):
        if any(task.get("_is_request") for task in self.selected()):
            self.confirm_selected()
        else:
            self.show_details()
        return "break"

    @staticmethod
    def _matches_status(task: dict, selected: str) -> bool:
        """One presentation rule serves both rows and navigation facets."""
        status = task.get("status")
        return (selected == "Todos" or
                (selected == "Aguardando" and status in {"awaiting_confirmation", "held"}) or
                (selected == "Em andamento" and status in ACTIVE | {"queued"}) or
                (selected == "Pausados" and status == "paused") or
                (selected == "Agendados" and status == "scheduled") or
                (selected == "Concluídos" and status == "completed") or
                (selected == "Falhas" and (status == "failed" or (task.get("_is_request") and bool(task.get("error"))))))

    def _matches_context(self, task: dict) -> bool:
        queue = self.queue_choices.get(self.queue_filter.get())
        if queue is not None and not task.get("_is_request") and task.get("queue_id", "default") != queue:
            return False
        query = self.search_value.get().strip().casefold()
        if query and query not in str(task.get("filename") or "").casefold() and query not in str(task.get("url") or "").casefold():
            return False
        return True

    def _visible(self, task: dict) -> bool:
        if not self._matches_context(task):
            return False
        category = self.category_value.get()
        if category != "Todos os tipos" and task_category(task) != category:
            return False
        return self._matches_status(task, self.filter_value.get())

    def _update_navigation(self, tasks: list[dict]) -> None:
        """Facets retain queue/search context and the other dimension's filter.

        Classify each candidate once for the counts instead of refreshing the
        manager or reinterpreting filenames independently for every sidebar row.
        """
        counts = {choice: 0 for choice in self.navigation_filters.values()}
        category_filter = self.category_value.get()
        status_filter = self.filter_value.get()
        for task in tasks:
            if not self._matches_context(task):
                continue
            category = task_category(task)
            if category_filter == "Todos os tipos" or category == category_filter:
                for value in STATUS_FILTERS:
                    if self._matches_status(task, value):
                        counts[("status", value)] += 1
            if self._matches_status(task, status_filter):
                counts[("category", "Todos os tipos")] += 1
                counts[("category", category)] += 1
        for identity, (kind, value) in self.navigation_filters.items():
            active = value == (status_filter if kind == "status" else category_filter)
            self.navigation.item(identity, values=(counts[(kind, value)],), tags=("active",) if active else ())

    def refresh(self) -> None:
        if self.closing:
            return
        if getattr(self.manager, "recording_error", ""):
            self.notice.set(self.manager.recording_error)
        try:
            task_list = self.manager.list_tasks()
            task_ids = {task["id"] for task in task_list}
            requests = [dict(item, _is_request=True, status="awaiting_confirmation")
                        for item in self.manager.list_requests() if item["id"] not in task_ids]
            self._seen_requests.intersection_update(item["id"] for item in requests)
            task_list += requests
        except Exception as error:
            self.notice.set(f"Não foi possível atualizar a lista: {error}")
            return
        self.tasks = {str(task["id"]): task for task in task_list}
        self.queue_choices = {row["name"]: row["id"] for row in self.manager.list_queues()}
        self.queue_box.configure(values=("Todas as filas", *self.queue_choices))
        self._update_navigation(task_list)
        self.count_value.set(str(len(task_list)))
        self.active_value.set(str(sum(task.get("status") in ACTIVE for task in task_list)))
        self.done_value.set(str(sum(task.get("status") == "completed" for task in task_list)))
        speed = sum(max(0, float(task.get("speed") or 0)) for task in task_list if task.get("status") in ACTIVE)
        self.speed_value.set(f"{format_bytes(speed)}/s")
        order = self.sort_value.get()
        self.tree.heading("name", text="Arquivo ↑" if order == "Nome A–Z" else "Arquivo")
        self.tree.heading("size", text="Recebido / total" + (" ↓" if order == "Maior tamanho" else " ↑" if order == "Menor tamanho" else ""))
        if order == "Nome A–Z":
            task_list.sort(key=lambda task: (str(task.get("filename") or "").casefold(), str(task["id"])))
        elif order in {"Maior tamanho", "Menor tamanho"}:
            task_list.sort(key=lambda task: (task.get("total") is None,
                                            (task.get("total") or 0) * (-1 if order == "Maior tamanho" else 1)))
        shown: set[str] = set()
        visible_order: list[str] = []
        for task in task_list:
            if not self._visible(task):
                continue
            task_id = str(task["id"])
            shown.add(task_id)
            visible_order.append(task_id)
            total = task.get("total")
            downloaded = max(0, task.get("downloaded") or 0)
            speed = max(0, task.get("speed") or 0)
            progress = f"{min(100, downloaded * 100 / total):.1f}%" if total else ("100%" if task.get("status") == "completed" else "—")
            eta = (total - downloaded) / speed if total and speed > 0 else None
            values = (task.get("filename") or "Obtendo nome…", STATUS.get(task.get("status"), task.get("status", "—")), progress, f"{format_bytes(downloaded)} / {format_bytes(total)}", f"{format_bytes(speed)}/s" if speed else "—", format_duration(eta), str(task.get("parts", 1)))
            if self.tree.exists(task_id):
                self.tree.item(task_id, values=values, tags=(task.get("status", ""),))
            else:
                self.tree.insert("", "end", iid=task_id, values=values, tags=(task.get("status", ""),))
        for task_id in self.tree.get_children():
            if task_id not in shown:
                self.tree.delete(task_id)
        if self.tree.get_children() != tuple(visible_order):
            for position, task_id in enumerate(visible_order):
                self.tree.move(task_id, "", position)
        if shown:
            self.empty_label.place_forget()
        else:
            self.empty_label.configure(text="Nenhum download neste filtro." if self.tasks else "Sua fila está pronta.\n\nClique em ＋ Novo download ou pressione Ctrl+N para começar.")
            self.empty_label.place(relx=0.5, rely=0.45, anchor="center")
        self.update_details()

    def selected(self) -> list[dict]:
        return [self.tasks[task_id] for task_id in self.tree.selection() if task_id in self.tasks]

    def update_details(self) -> None:
        tasks = self.selected()
        has_request = any(task.get("_is_request") for task in tasks)
        self.confirm_button.configure(state="normal" if not self.closing and
                                      (has_request or (not tasks and any(item.get("_is_request") for item in self.tasks.values()))) else "disabled")
        for button in self.action_buttons.values():
            button.configure(state="normal" if tasks and not has_request and not self.closing else "disabled")
        self.folder_button.configure(state="normal" if tasks and not self.closing else "disabled")
        self.details_button.configure(state="normal" if tasks and not self.closing else "disabled")
        if has_request and not self.closing:
            self.action_buttons["Cancelar"].configure(state="normal")
            self.action_buttons["Remover"].configure(state="normal")
        if not tasks:
            self.selected_name.set("Escolha um download para ver os detalhes.")
            self.selected_info.set("")
            self.progress["value"] = 0
            return
        task = tasks[0]
        total = task.get("total")
        self.progress["value"] = min(100, (task.get("downloaded") or 0) * 100 / total) if total else (100 if task.get("status") == "completed" else 0)
        self.selected_name.set(task.get("filename") or "Obtendo nome do arquivo…")
        info = task.get("display_url") or visible_url(task.get("url", ""))
        if task.get("queue_known") is False:
            info += "  •  Fila desconhecida: novos inícios bloqueados. Pause e escolha uma fila existente."
        elif task.get("queue_enabled") is False and task.get("status") in {"queued", "scheduled"}:
            info += "  •  Fila suspensa: aguardando liberação de novos inícios."
        if task.get("status") == "scheduled" and task.get("scheduled_at"):
            info += "  •  " + dt.datetime.fromtimestamp(task["scheduled_at"]).strftime("Agendado: %d/%m/%Y às %H:%M")
        if task.get("error"):
            info = " ".join(str(task["error"]).splitlines()) + "  •  " + info
        self.selected_info.set(info)

    def apply_action(self, action: str) -> None:
        if self.closing:
            return
        tasks = self.selected()
        if not tasks:
            return
        errors: list[str] = []
        for task in tasks:
            try:
                if task.get("_is_request"):
                    if action not in {"cancel", "remove"}:
                        raise ValueError("Confirme o pedido antes de iniciar a transferência.")
                    self.manager.dismiss_request(task["id"])
                else:
                    if action in {"resume", "restart"} and not self._ensure_authorized_keys(task):
                        continue
                    getattr(self.manager, action)(task["id"])
            except Exception as error:
                errors.append(f"{task.get('filename') or 'Download'}: {error}")
        if errors:
            messagebox.showerror("Ação não concluída", "\n".join(errors[:8]), parent=self.root)
        self.refresh()

    def _ensure_authorized_keys(self, task: dict) -> bool:
        if not task.get("requires_keys") or not self.manager.needs_authorized_keys(task["id"]):
            return True
        from .crypto_keys import parse_authorized_keys
        text = simpledialog.askstring("Chaves autorizadas", "Informe KID:CHAVE (32 hexadecimais cada). Separe até 16 pares por ponto e vírgula.\nAs chaves ficam somente na memória até fechar o aplicativo.", parent=self.root, show="●")
        if text is None:
            return False
        try:
            keys = parse_authorized_keys(text)
        finally:
            text = None
        self.manager.set_authorized_keys(task["id"], keys)
        return True

    def cancel_selected(self) -> None:
        if self.selected() and messagebox.askyesno("Cancelar download", "Cancelar os downloads selecionados? Os arquivos finais já concluídos serão preservados.", parent=self.root):
            self.apply_action("cancel")

    def restart_selected(self) -> None:
        if self.selected() and messagebox.askyesno("Reiniciar download", "Recomeçar os downloads selecionados desde o início? Os dados parciais serão descartados.", parent=self.root):
            self.apply_action("restart")

    def remove_selected(self) -> None:
        if self.selected() and messagebox.askyesno("Remover da lista", "Remover os downloads selecionados da lista? Arquivos finais já concluídos serão preservados.", parent=self.root):
            self.apply_action("remove")

    def open_folder(self) -> None:
        tasks = self.selected()
        if not tasks:
            return
        self.open_directory(Path(tasks[0]["directory"]))

    def open_directory(self, directory: Path) -> None:
        if not directory.is_dir():
            messagebox.showerror("Pasta indisponível", "A pasta de destino não está disponível neste momento.", parent=self.root)
            return
        try:
            if sys.platform == "win32":
                os.startfile(str(directory))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(directory)])
            else:
                subprocess.Popen(["xdg-open", str(directory)])
        except OSError as error:
            messagebox.showerror("Não foi possível abrir a pasta", str(error), parent=self.root)

    def show_details(self) -> None:
        tasks = self.selected()
        if not tasks:
            return
        task = tasks[0]
        if task.get("_is_request"):
            self._show_confirmation(task)
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("Detalhes do download")
        dialog.geometry("700x410")
        dialog.transient(self.root)
        frame = ttk.Frame(dialog, padding=22)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=task.get("filename") or "Download", style="Title.TLabel", wraplength=640).pack(anchor="w", pady=(0, 12))
        address = task.get("display_url") or visible_url(task.get("url", ""))
        content = "\n\n".join((f"Estado: {STATUS.get(task.get('status'), task.get('status', '—'))}", f"Endereço: {address}", f"Destino: {task.get('directory', '')}", f"Recebido: {format_bytes(task.get('downloaded'))} / {format_bytes(task.get('total'))}", f"SHA-256 esperado: {task.get('sha256_expected') or 'Não informado'}", f"SHA-256 calculado: {task.get('sha256') or 'Disponível após conclusão'}", f"Detalhes: {task.get('error') or 'Nenhum erro registrado.'}"))
        text = tk.Text(frame, wrap="word", font=("Segoe UI", 10), relief="flat", bg="white", padx=10, pady=10)
        text.insert("1.0", content)
        text.configure(state="disabled")
        text.pack(fill="both", expand=True)
        ttk.Button(frame, text="Fechar", command=dialog.destroy).pack(anchor="e", pady=(12, 0))

    def about(self) -> None:
        messagebox.showinfo("OpenDownloader", "OpenDownloader\nGerenciador de downloads local e autoral.\n\nFila persistente, retomada, transferência por partes e TLS com validação de certificados.\n\nOs arquivos baixados nunca são executados automaticamente.", parent=self.root)

    def begin_close(self) -> None:
        if self.closing:
            return
        self.closing = True
        for stop in self.background_stops.values():
            stop.set()
        if self._poll_id is not None:
            self.root.after_cancel(self._poll_id)
            self._poll_id = None
        self.notice.set("Finalizando transferências e preservando a fila…")
        self.add_button.configure(state="disabled")
        self.confirm_button.configure(state="disabled")
        for button in (self.folder_button, self.details_button, self.browser_button,
                       self.settings_button, self.clear_button):
            button.configure(state="disabled")
        for button in self.action_buttons.values():
            button.configure(state="disabled")
        for child in self.root.winfo_children():
            if isinstance(child, tk.Toplevel):
                child.destroy()

        threading.Thread(target=_close_manager_job,
                         args=(self.manager, tuple(self.background_jobs), self._close_result),
                         name="lume-ui-close", daemon=True).start()
        self._close_poll_id = self.root.after(100, self._await_close)

    def _await_close(self) -> None:
        self._close_poll_id = None
        try:
            result = self._close_result.get_nowait()
        except queue.Empty:
            self._close_poll_id = self.root.after(100, self._await_close)
            return
        if result is not None:
            self.notice.set("Não foi possível encerrar com segurança. Tente novamente.")
            messagebox.showerror("Atenção ao encerrar", f"A fila não confirmou o encerramento:\n{result}\n\nA janela continuará aberta para permitir nova tentativa.", parent=self.root)
            self.closing = False
            self.add_button.configure(state="normal")
            for button in (self.browser_button, self.settings_button, self.clear_button):
                button.configure(state="normal")
            self.update_details()
            self._poll_id = self.root.after(500, self._poll)
            return
        self.root.destroy()

    def _on_destroy(self, event) -> None:
        if event.widget is self.root:
            self.dispose()

    def dispose(self) -> None:
        """Finalize the UI ownership graph on its creator thread."""
        if self._disposed:
            return
        self._disposed = True
        self.closing = True
        for stop in self.background_stops.values():
            stop.set()
        for name in ("_poll_id", "_close_poll_id"):
            handle = getattr(self, name)
            if handle is not None:
                self.root.after_cancel(handle)
                setattr(self, name, None)
        _release_tk_state(self)


def run_gui(manager: Manager) -> None:
    root = tk.Tk()
    app = App(root, manager)
    try:
        root.mainloop()
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass
        app.dispose()
        manager.close()
        # Tcl and its Python cycles are owned by this thread. Do not defer
        # their finalization to whichever download worker next triggers GC.
        del app, root
        gc.collect()
