"""HTTP transport. TLS policy and redirect interpretation have one owner here."""
from __future__ import annotations

import http.client
import errno
import random
import re
import ssl
import select
import threading
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from urllib.parse import quote, unquote_to_bytes, urljoin, urlsplit, urlunsplit
from . import VERSION


class DownloadError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class Interrupted(DownloadError):
    def __init__(self):
        super().__init__("interrupted", "Transferência interrompida pelo usuário.")


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded recovery before a GET response is delivered to its consumer."""
    max_attempts: int = 3
    base_delay: float = .25
    max_delay: float = 2
    max_retry_after: float = 30

    def __post_init__(self):
        if (type(self.max_attempts) is not int or not 1 <= self.max_attempts <= 5
                or not 0 <= self.base_delay <= self.max_delay <= 30
                or not 0 <= self.max_retry_after <= 120):
            raise ValueError("Política de retentativas inválida.")

    def delay(self, attempt: int, retry_after: str | None = None) -> float | None:
        """None means the server's minimum wait exceeds the allowed budget."""
        minimum = 0.0
        if retry_after is not None:
            value = retry_after.strip()
            if re.fullmatch(r"[0-9]+", value):
                # Bound parsing before int conversion of untrusted huge values.
                digits = value.lstrip("0")
                if len(digits) > 3:
                    return None
                minimum = float(int(digits or "0"))
            else:
                try:
                    stamp = parsedate_to_datetime(value)
                    if stamp.tzinfo is not None:
                        minimum = max(0, stamp.timestamp() - time.time())
                except (ValueError, TypeError, OverflowError):
                    pass
            if minimum > self.max_retry_after:
                return None
        return max(minimum, random.uniform(0, min(self.max_delay, self.base_delay * 2**attempt)))


_TRANSIENT_STATUS = frozenset((408, 429, 500, 502, 503, 504))
_TRANSIENT_ERRNO = frozenset((errno.ECONNRESET, errno.ECONNABORTED, errno.ETIMEDOUT,
                            errno.EPIPE, errno.ECONNREFUSED, errno.EHOSTUNREACH,
                            errno.ENETUNREACH))


def _transient_open_error(error: BaseException) -> bool:
    # Certificate, protocol and malformed HTTP failures must remain visible.
    return (not isinstance(error, ssl.SSLError)
            and (isinstance(error, (TimeoutError, http.client.RemoteDisconnected))
                 or isinstance(error, OSError) and error.errno in _TRANSIENT_ERRNO))


def transient_body_error(error: BaseException) -> bool:
    """Body recovery may retry short framing, never TLS/protocol/permission errors."""
    return isinstance(error,http.client.IncompleteRead) or _transient_open_error(error)


def validate_url(url: str) -> str:
    if not isinstance(url, str) or not url or len(url) > 8192:
        raise DownloadError("url", "Informe uma URL HTTP ou HTTPS válida (até 8192 caracteres).")
    if any(ord(c) < 32 or ord(c) == 127 for c in url) or "\\" in url:
        raise DownloadError("url", "A URL contém caracteres inválidos.")
    try:
        parsed = urlsplit(url)
        if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
            raise ValueError()
        if parsed.username is not None or parsed.password is not None:
            raise DownloadError("credentials", "URLs com usuário/senha não são aceitas.")
        port = parsed.port
        if port is not None and not 1 <= port <= 65535:
            raise ValueError()
        host = parsed.hostname.encode("idna").decode("ascii")
        authority = f"[{host}]" if ":" in host else host
        if port is not None:
            authority += f":{port}"
        path = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
        query = quote(parsed.query, safe="/%?:@!$&'()*+,;=-._~")
        return urlunsplit((parsed.scheme.lower(), authority, path, query, ""))
    except (ValueError, UnicodeError):
        raise DownloadError("url", "Informe uma URL HTTP ou HTTPS válida.") from None


def display_url(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "…" if parsed.query else "", ""))


class Control:
    def __init__(self):
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.connections: set[http.client.HTTPConnection] = set()

    def check(self):
        if self.stop.is_set():
            raise Interrupted()

    def interrupt(self):
        self.stop.set()
        with self.lock:
            # Keep cancellation and release serialized: a connection returned to
            # another task must never be closed by an old cancellation snapshot.
            for conn in self.connections:
                conn.close()


class _BodyResponse(http.client.HTTPResponse):
    """Record successful framing completion before close() hides its cause."""
    body_complete = False

    def read(self, amt=None):
        value = super().read(amt)
        if self.isclosed() and (self.length == 0 or self.chunked):
            self.body_complete = True
        return value

    def readinto(self, buffer):
        value = super().readinto(buffer)
        if self.isclosed() and (self.length == 0 or self.chunked):
            self.body_complete = True
        return value


class _CancellationConnection:
    def connect(self):
        self.lume_control.check()
        super().connect()
        # A late handshake must not create an untracked connection after cancel.
        try:
            self.lume_control.check()
        except Interrupted:
            self.close()
            raise


class _HTTPConnection(_CancellationConnection, http.client.HTTPConnection):
    response_class = _BodyResponse


class _HTTPSConnection(_CancellationConnection, http.client.HTTPSConnection):
    response_class = _BodyResponse


@dataclass
class _HostGate:
    semaphore: threading.BoundedSemaphore
    users: int = 0


@dataclass
class Response:
    connection: http.client.HTTPConnection
    response: http.client.HTTPResponse
    url: str
    control: Control
    slot: threading.Semaphore
    transport: Transport
    origin: tuple
    host: str
    closed: bool = False

    def close(self, *, reusable=True):
        if not self.closed:
            self.closed = True
            try:
                complete = (reusable and getattr(self.response, "body_complete", False)
                            and not self.response.will_close)
                # Remove the task's authority before moving ownership into the
                # idle pool. interrupt() uses the same lock to avoid stale closes.
                with self.control.lock:
                    complete = complete and not self.control.stop.is_set()
                    self.control.connections.discard(self.connection)
                    self.response.close()
                self.transport._release(self.origin, self.connection, complete)
            finally:
                self.slot.release()
                self.transport._drop_host(self.host)

    def __enter__(self):
        return self

    def __exit__(self, error_type, *_):
        self.close(reusable=error_type is None)


class Transport:
    def __init__(self, context: ssl.SSLContext | None = None, timeout: float = 8, *,
                 max_connections_per_host: int = 4, keep_alive: bool = True,
                 idle_timeout: float = 15, retry_policy: RetryPolicy | None = None):
        if type(max_connections_per_host) is not int or not 1 <= max_connections_per_host <= 8:
            raise ValueError("Escolha de 1 a 8 conexões por servidor.")
        if type(keep_alive) is not bool or not 0 < idle_timeout <= 120:
            raise ValueError("Configuração de reutilização de conexões inválida.")
        self.context = context or ssl.create_default_context()
        if not self.context.check_hostname or self.context.verify_mode != ssl.CERT_REQUIRED:
            raise ValueError("TLS exige cadeia e hostname verificados.")
        self.context.minimum_version = ssl.TLSVersion.TLSv1_2
        self.context.set_alpn_protocols(["http/1.1"])
        self.timeout = timeout
        self.max_connections_per_host = max_connections_per_host
        self.keep_alive = keep_alive
        self.idle_timeout = idle_timeout
        self.retry_policy = retry_policy if retry_policy is not None else RetryPolicy()
        self.lock = threading.Lock()
        self.host_slots: dict[str, _HostGate] = {}
        self.idle: dict[tuple, list[tuple[float, http.client.HTTPConnection]]] = {}
        self.closed = False

    @staticmethod
    def _healthy(connection):
        sock = connection.sock
        if sock is None or sock.fileno() < 0:
            return False
        try:
            # Readability on an idle HTTP socket means EOF or unsolicited bytes;
            # neither may become a response belonging to another request.
            return not (isinstance(sock, ssl.SSLSocket) and sock.pending()) and not select.select([sock], [], [], 0)[0]
        except (OSError, ValueError):
            return False

    def _prune(self):
        now = time.monotonic()
        for origin in list(self.idle):
            kept = []
            for returned, connection in self.idle[origin]:
                if now - returned < self.idle_timeout and self._healthy(connection):
                    kept.append((returned, connection))
                else:
                    connection.close()
            if kept:
                self.idle[origin] = kept
            else:
                del self.idle[origin]

    def _release(self, origin, connection, reusable):
        with self.lock:
            self._prune()
            count = sum(len(items) for items in self.idle.values())
            if (reusable and self.keep_alive and not self.closed and count < 32
                    and self._healthy(connection)):
                self.idle.setdefault(origin, []).append((time.monotonic(), connection))
                return
        connection.close()

    def _drop_host(self, host):
        with self.lock:
            gate = self.host_slots[host]
            gate.users -= 1
            if not gate.users:
                del self.host_slots[host]

    def close(self):
        """Close idle resources; active responses discard their sockets on exit."""
        with self.lock:
            self.closed = True
            for items in self.idle.values():
                for _, connection in items:
                    connection.close()
            self.idle.clear()

    def open(self, url: str, headers: dict[str, str], control: Control) -> Response:
        """Retry only opening a GET; delivered or partially read bodies never replay."""
        for attempt in range(self.retry_policy.max_attempts):
            control.check()
            try:
                owned = self._open_once(url, headers, control)
            except (OSError, http.client.HTTPException) as error:
                control.check()
                if attempt + 1 == self.retry_policy.max_attempts or not _transient_open_error(error):
                    raise
                delay = self.retry_policy.delay(attempt)
            else:
                if (owned.response.status not in _TRANSIENT_STATUS
                        or attempt + 1 == self.retry_policy.max_attempts):
                    return owned
                delay = self.retry_policy.delay(attempt, owned.response.getheader("Retry-After"))
                if delay is None:
                    return owned
                # Never drain arbitrary error bodies or reuse their sockets.
                url = owned.url
                owned.close(reusable=False)
            # Release the host lease during backoff, and wake promptly on cancel.
            deadline = time.monotonic() + delay
            while True:
                control.check()
                with self.lock:
                    if self.closed:
                        raise DownloadError("transport_closed", "O transporte já foi encerrado.")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                control.stop.wait(min(.1, remaining))
        raise AssertionError("Unreachable retry budget")

    def _open_once(self, url: str, headers: dict[str, str], control: Control) -> Response:
        url = validate_url(url)
        for _ in range(9):
            control.check()
            parsed = urlsplit(url)
            host = parsed.hostname or ""
            origin = (parsed.scheme, host, parsed.port or (443 if parsed.scheme == "https" else 80))
            with self.lock:
                if self.closed:
                    raise DownloadError("transport_closed", "O transporte já foi encerrado.")
                if host not in self.host_slots and len(self.host_slots) >= 128:
                    raise DownloadError("capacity", "Limite de servidores simultâneos atingido.")
                gate = self.host_slots.setdefault(host, _HostGate(threading.BoundedSemaphore(self.max_connections_per_host)))
                gate.users += 1
                slot = gate.semaphore
            try:
                while not slot.acquire(timeout=.1):
                    control.check()
                    with self.lock:
                        if self.closed:
                            raise DownloadError("transport_closed", "O transporte já foi encerrado.")
            except BaseException:
                self._drop_host(host)
                raise
            conn = None
            try:
                control.check()
                with self.lock:
                    if self.closed:
                        raise DownloadError("transport_closed", "O transporte já foi encerrado.")
                    self._prune()
                    available = self.idle.get(origin)
                    if available:
                        _, conn = available.pop()
                        if not available:
                            del self.idle[origin]
                if conn is None:
                    if parsed.scheme == "https":
                        conn = _HTTPSConnection(host, parsed.port, timeout=self.timeout, context=self.context)
                    else:
                        conn = _HTTPConnection(host, parsed.port, timeout=self.timeout)
                conn.lume_control = control
                with control.lock:
                    control.check()
                    control.connections.add(conn)
                request_headers = {"User-Agent": "OpenDownloader/"+VERSION, "Accept-Encoding": "identity",
                                   "Connection": "keep-alive" if self.keep_alive else "close", **headers}
                target = parsed.path + (("?" + parsed.query) if parsed.query else "")
                conn.request("GET", target, headers=request_headers)
                response = conn.getresponse()
                owned = Response(conn, response, url, control, slot, self, origin, host)
            except BaseException:
                if conn:
                    conn.close()
                    with control.lock:
                        control.connections.discard(conn)
                slot.release()
                self._drop_host(host)
                raise
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader("Location")
                owned.close()
                if not location:
                    raise DownloadError("redirect", "Redirecionamento sem destino.")
                new_url = validate_url(urljoin(url, location))
                if parsed.scheme == "https" and urlsplit(new_url).scheme != "https":
                    raise DownloadError("tls_downgrade", "Redirecionamento HTTPS para HTTP recusado.")
                # No cookies, authorization or ambient credentials are carried between origins.
                url = new_url
                continue
            if response.getheader("Content-Encoding", "identity").lower() != "identity":
                owned.close()
                raise DownloadError("encoding", "O servidor enviou uma codificação não solicitada.")
            return owned
        raise DownloadError("redirect", "Limite de redirecionamentos excedido.")


_HTTP_TOKEN = r"[!#$%&'*+.^_`|~0-9A-Za-z-]+"
_DISPOSITION_PARAMETER = re.compile(
    rf'\s*;\s*({_HTTP_TOKEN})\s*=\s*(?:"((?:[^"\\\r\n]|\\[^\r\n])*)"|({_HTTP_TOKEN}))\s*')


def content_disposition_filename(value: str | None) -> str | None:
    """Parse RFC 6266/8187 names; caller owns destination/path sanitization.

    Quoted semicolons and escaped quotes belong to a parameter, not separators.
    Ambiguous duplicate parameters and malformed grammar are ignored. A valid
    extended name takes precedence over the optional legacy fallback.
    """
    if not value or any(ord(char) < 32 and char != "\t" or ord(char) == 127 for char in value):
        return None
    disposition = re.match(rf"\s*{_HTTP_TOKEN}\s*", value)
    if disposition is None:
        return None
    parameters = {}
    position = disposition.end()
    while position < len(value):
        parameter = _DISPOSITION_PARAMETER.match(value, position)
        if parameter is None:
            return None
        name, quoted, token = parameter.groups()
        name = name.lower()
        if name in parameters:
            return None
        parameters[name] = re.sub(r"\\(.)", r"\1", quoted) if quoted is not None else token
        position = parameter.end()
    extended = parameters.get("filename*")
    if extended:
        parts = extended.split("'", 2)
        if (len(parts) == 3 and parts[0].lower() in ("utf-8", "iso-8859-1")
                and parts[2].isascii() and not re.search(r"%(?![0-9a-fA-F]{2})", parts[2])):
            try:
                decoded = unquote_to_bytes(parts[2]).decode(parts[0], errors="strict")
                if decoded:
                    return decoded
            except (UnicodeError, LookupError):
                pass
    return parameters.get("filename") or None


def content_length(response: http.client.HTTPResponse) -> int | None:
    values = response.headers.get_all("Content-Length", [])
    if not values:
        return None
    if len(values) != 1 or not re.fullmatch(r"[0-9]+", values[0]):
        raise DownloadError("length", "Content-Length inválido.")
    value = int(values[0])
    if value > 2**63 - 1:
        raise DownloadError("length", "Tamanho excede a capacidade suportada.")
    return value


def content_range(value: str | None) -> tuple[int, int, int]:
    match = re.fullmatch(r"bytes ([0-9]+)-([0-9]+)/([0-9]+)", value or "")
    if not match:
        raise DownloadError("range", "Content-Range ausente ou inválido.")
    start, end, total = map(int, match.groups())
    if not 0 <= start <= end < total <= 2**63 - 1:
        raise DownloadError("range", "Intervalo de bytes inconsistente.")
    return start, end, total


def validator(response: http.client.HTTPResponse) -> str | None:
    etag = response.getheader("ETag")
    if etag and re.fullmatch(r'"[^"\r\n]*"', etag):
        return etag
    if etag:
        return None  # If-Range must not replace an existing weak entity tag with a date.
    modified = response.getheader("Last-Modified")
    # Conservatively qualify date validators with a 60-second gap; see RFC 9110
    # 8.8.2.2 and 13.1.5. Otherwise use a whole single transfer.
    if modified and response.getheader("Date"):
        from email.utils import format_datetime, parsedate_to_datetime
        try:
            stamp = parsedate_to_datetime(modified)
            date = parsedate_to_datetime(response.getheader("Date"))
            if stamp.tzinfo and date.tzinfo and (date - stamp).total_seconds() >= 60:
                return format_datetime(stamp, usegmt=True)
        except (ValueError, TypeError, OverflowError):
            pass
    return None
