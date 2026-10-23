"""SQLite queue and an OS-owned single-desktop-instance lock."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import errno
import uuid
from pathlib import Path


class InstanceAlreadyRunning(RuntimeError):
    pass


class InstanceLock:
    def __init__(self, path: Path):
        self.file = path.open("a+b")
        self.file.seek(0, os.SEEK_END)
        if not self.file.tell():
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self.file.close()
            if error.errno in (errno.EACCES, errno.EAGAIN):
                raise InstanceAlreadyRunning("O OpenDownloader já está aberto com esta pasta de dados.") from None
            raise

    def close(self):
        if not self.file.closed:
            if os.name == "nt":
                import msvcrt
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            self.file.close()


class Store:
    def __init__(self, path: Path):
        self.lock = threading.RLock()
        self.connection = sqlite3.connect(path, check_same_thread=False)
        try:
            self._backup_before_upgrade(path)
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA synchronous=FULL")
            self.connection.execute("CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            self.connection.execute("CREATE TABLE IF NOT EXISTS settings (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            self.connection.execute("CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, status TEXT NOT NULL, payload TEXT NOT NULL)")
            self.connection.execute("CREATE INDEX IF NOT EXISTS requests_status ON requests(status)")
            self.connection.commit()
        except BaseException:
            self.connection.close()
            raise

    def _backup_before_upgrade(self, path: Path):
        tables = {row[0] for row in self.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'tasks' not in tables or 'requests' in tables:
            return
        folder = path.parent / 'backups'
        if folder.is_symlink() or (hasattr(folder, 'is_junction') and folder.is_junction()):
            raise OSError('A pasta de backup não pode ser um link de diretório.')
        folder.mkdir(exist_ok=True)
        backup = folder / ('queue-before-1.2-' + uuid.uuid4().hex + '.sqlite3')
        descriptor = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        output = sqlite3.connect(backup)
        try:
            self.connection.backup(output)
        finally:
            output.close()

    def load(self) -> list[dict]:
        with self.lock:
            return [json.loads(row[0]) for row in self.connection.execute("SELECT payload FROM tasks ORDER BY rowid")]

    def save(self, task: dict):
        with self.lock, self.connection:
            self.connection.execute("INSERT INTO tasks VALUES (?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                                    (task["id"], json.dumps(task, ensure_ascii=False)))

    def save_many(self, tasks: list[dict]):
        """Serialize before writing; every queue-order change commits together."""
        rows = [(task["id"], json.dumps(task, ensure_ascii=False)) for task in tasks]
        with self.lock, self.connection:
            self.connection.executemany(
                "INSERT INTO tasks VALUES (?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload", rows)

    def task(self, identity: str) -> dict | None:
        with self.lock:
            row = self.connection.execute("SELECT payload FROM tasks WHERE id=?", (identity,)).fetchone()
            return json.loads(row[0]) if row else None

    def admit(self, task: dict, request: dict | None = None):
        """A browser approval and its queue item share one durable transaction."""
        with self.lock, self.connection:
            self.connection.execute("INSERT INTO tasks VALUES (?,?)", (task['id'], json.dumps(task, ensure_ascii=False)))
            if request is not None:
                completed = {**request, 'status': 'confirmed'}
                changed = self.connection.execute(
                    "UPDATE requests SET status=?,payload=? WHERE id=? AND status='awaiting_confirmation'",
                    ('confirmed', json.dumps(completed, ensure_ascii=False), request['id']))
                if changed.rowcount != 1:
                    raise ValueError('O pedido não está mais aguardando confirmação.')

    def remove(self, task_id: str):
        with self.lock, self.connection:
            self.connection.execute("DELETE FROM tasks WHERE id=?", (task_id,))

    def options(self) -> dict:
        with self.lock:
            row = self.connection.execute("SELECT payload FROM settings WHERE id='options'").fetchone()
            return json.loads(row[0]) if row else {}

    def pending_requests(self) -> list[dict]:
        with self.lock:
            return [json.loads(row[0]) for row in self.connection.execute(
                "SELECT payload FROM requests WHERE status='awaiting_confirmation' ORDER BY rowid")]

    def request(self, identity: str) -> dict | None:
        with self.lock:
            row = self.connection.execute("SELECT payload FROM requests WHERE id=?", (identity,)).fetchone()
            return json.loads(row[0]) if row else None

    def save_request(self, request: dict):
        # Terminal receipts prevent a late durable inbox claim from resurrecting
        # a cancelled request or downloading an acknowledged request twice.
        with self.lock, self.connection:
            self.connection.execute(
                "INSERT INTO requests VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,payload=excluded.payload",
                (request["id"], request["status"], json.dumps(request, ensure_ascii=False)))

    def request_error(self, request: dict):
        # An uncertain task admission must never roll a confirmed receipt back
        # into pending merely because an error acknowledgement arrived late.
        with self.lock, self.connection:
            self.connection.execute(
                "UPDATE requests SET payload=? WHERE id=? AND status='awaiting_confirmation'",
                (json.dumps(request, ensure_ascii=False), request['id']))

    def save_options(self, options: dict):
        with self.lock, self.connection:
            self.connection.execute("INSERT INTO settings VALUES ('options',?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                                    (json.dumps(options),))

    def close(self):
        with self.lock:
            self.connection.close()
