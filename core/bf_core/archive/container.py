"""The `.bfggis` container (ported from BF archive/container.py, format 1 unchanged: BF files open in BF2).

    magic  b"BFGGIS\\r\\n"            8 bytes
    u16    format version            little-endian (FORMAT_VERSION)
    u32    header length             little-endian
    header JSON (UTF-8)              app and schema versions, encryption, a summary of the project
    body                             tar (PAX) -> zstd -> [AES-256-GCM in chunks when a password is set]

Encrypted body: a sequence of chunks `u32 length | ciphertext+tag`, each holding up to CHUNK bytes of
the compressed stream. The nonce is the 7-byte prefix from the header, a 4-byte chunk counter and a
final flag byte, so chunks cannot be reordered, dropped or cut off unnoticed; the header bytes are
the associated data of every chunk, so it cannot be edited either. The key comes from the password
by scrypt. A plain body relies on the zstd frame checksum and the checksums of the manifest.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import struct
import tarfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Any

import zstandard
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"BFGGIS\r\n"
FORMAT_VERSION = 1
SUFFIX = ".bfggis"
CHUNK = 4 << 20
ZSTD_LEVEL = 3
SCRYPT = {"n": 1 << 15, "r": 8, "p": 1}
_MAX_HEADER = 1 << 20


class ArchiveError(Exception):
    """A problem with the file; the message is shown to the user."""


class NotAnArchive(ArchiveError):
    pass


class BadPassword(ArchiveError):
    pass


class Corrupted(ArchiveError):
    pass


class TooNew(ArchiveError):
    pass


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _key(password: str, kdf: dict[str, Any]) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=base64.b64decode(kdf["salt"]),
        n=int(kdf["n"]),
        r=int(kdf["r"]),
        p=int(kdf["p"]),
        maxmem=256 << 20,
        dklen=32,
    )


def _nonce(prefix: bytes, counter: int, final: bool) -> bytes:
    return prefix + struct.pack("<I", counter) + (b"\x01" if final else b"\x00")


# ---- writing


class _EncryptingSink(io.RawIOBase):
    """Collects the compressed stream and writes it as authenticated chunks."""

    def __init__(self, out: Any, key: bytes, prefix: bytes, aad: bytes):
        self._out, self._aes, self._prefix, self._aad = out, AESGCM(key), prefix, aad
        self._buf = bytearray()
        self._n = 0
        self._finished = False

    def writable(self) -> bool:
        return True

    def _emit(self, data: bytes, final: bool) -> None:
        ct = self._aes.encrypt(_nonce(self._prefix, self._n, final), data, self._aad)
        self._out.write(struct.pack("<I", len(ct)))
        self._out.write(ct)
        self._n += 1

    def write(self, b: Any) -> int:
        self._buf += b
        while len(self._buf) > CHUNK:  # keep at least one byte back for the final chunk
            self._emit(bytes(self._buf[:CHUNK]), False)
            del self._buf[:CHUNK]
        return len(b)

    def finish(self) -> None:
        if not self._finished:
            self._emit(bytes(self._buf), True)
            self._buf.clear()
            self._finished = True


class _CountingFile(io.RawIOBase):
    def __init__(self, f: IO[bytes]):
        self._f = f
        self.count = 0

    def writable(self) -> bool:
        return True

    def write(self, b: Any) -> int:
        self._f.write(b)
        self.count += len(b)
        return len(b)


class ArchiveWriter:
    """Writes the container; `tar` takes the entries. Use as a context manager."""

    def __init__(self, path: Path, header: dict[str, Any], password: str | None = None):
        self._file = path.open("wb")
        header = dict(header)
        header["format"] = FORMAT_VERSION
        header["compression"] = "zstd"
        key = None
        prefix = b""
        if password:
            kdf = {"name": "scrypt", **SCRYPT, "salt": _b64(os.urandom(16))}
            prefix = os.urandom(7)
            header.update(encrypted=True, cipher="AES-256-GCM", chunk=CHUNK, kdf=kdf, nonce_prefix=_b64(prefix))
            header.pop("summary", None)  # nothing about the project is readable without the password
            key = _key(password, kdf)
        else:
            header["encrypted"] = False
        raw = json.dumps(header, ensure_ascii=False).encode("utf-8")
        head = MAGIC + struct.pack("<HI", FORMAT_VERSION, len(raw)) + raw
        self._file.write(head)
        self.counter = _CountingFile(self._file)
        self._sink = _EncryptingSink(self.counter, key, prefix, head) if key else None
        target: Any = self._sink if self._sink else self.counter
        self._zstd = zstandard.ZstdCompressor(level=ZSTD_LEVEL, write_checksum=True, threads=-1).stream_writer(
            target, closefd=False
        )
        self.tar = tarfile.open(fileobj=self._zstd, mode="w|", format=tarfile.PAX_FORMAT)

    def add_bytes(self, name: str, data: bytes) -> None:
        info = tarfile.TarInfo(name)
        info.size = len(data)
        self.tar.addfile(info, io.BytesIO(data))

    def add_stream(self, name: str, size: int, f: IO[bytes]) -> None:
        info = tarfile.TarInfo(name)
        info.size = size
        self.tar.addfile(info, f)

    def add_file(self, name: str, path: Path) -> None:
        info = tarfile.TarInfo(name)
        info.size = path.stat().st_size
        with path.open("rb") as f:
            self.tar.addfile(info, f)

    def close(self) -> None:
        self.tar.close()
        self._zstd.flush(zstandard.FLUSH_FRAME)
        self._zstd.close()
        if self._sink:
            self._sink.finish()
        self._file.flush()
        os.fsync(self._file.fileno())
        self._file.close()

    def abort(self) -> None:
        try:
            self._file.close()
        except OSError:
            pass

    def __enter__(self) -> ArchiveWriter:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if exc_type is None:
            self.close()
        else:
            self.abort()


# ---- reading


def read_header(f: IO[bytes]) -> tuple[dict[str, Any], bytes]:
    """(header, its raw bytes including the magic); the file is left at the start of the body."""
    magic = f.read(len(MAGIC))
    if magic != MAGIC:
        raise NotAnArchive("Это не файл проекта BedrockForge (.bfggis)")
    fixed = f.read(6)
    if len(fixed) != 6:
        raise Corrupted("Файл обрезан: нет заголовка")
    version, size = struct.unpack("<HI", fixed)
    if version > FORMAT_VERSION:
        raise TooNew("Файл записан более новой версией BedrockForge: обновите программу")
    if size > _MAX_HEADER:
        raise Corrupted("Заголовок файла повреждён")
    raw = f.read(size)
    if len(raw) != size:
        raise Corrupted("Файл обрезан: нет заголовка")
    try:
        header = json.loads(raw)
    except ValueError as exc:
        raise Corrupted("Заголовок файла повреждён") from exc
    return header, magic + fixed + raw


class _DecryptingSource(io.RawIOBase):
    def __init__(self, f: IO[bytes], key: bytes, prefix: bytes, aad: bytes):
        self._f, self._aes, self._prefix, self._aad = f, AESGCM(key), prefix, aad
        self._buf = b""
        self._pos = 0
        self._n = 0
        self._done = False

    def readable(self) -> bool:
        return True

    def _next(self) -> None:
        size = self._f.read(4)
        if len(size) < 4:
            raise Corrupted("Файл обрезан: не хватает конца данных")
        (n,) = struct.unpack("<I", size)
        if n > CHUNK + 64:
            raise Corrupted("Файл повреждён")
        ct = self._f.read(n)
        if len(ct) != n:
            raise Corrupted("Файл обрезан: не хватает конца данных")
        for final in (False, True):
            try:
                self._buf = self._aes.decrypt(_nonce(self._prefix, self._n, final), ct, self._aad)
            except InvalidTag:
                continue
            self._pos = 0
            self._done = final
            self._n += 1
            return
        if self._n == 0:
            raise BadPassword("Неверный пароль")
        raise Corrupted("Файл повреждён: данные не прошли проверку")

    def readinto(self, b: Any) -> int:
        while self._pos >= len(self._buf):
            if self._done:
                return 0
            self._next()
        n = min(len(b), len(self._buf) - self._pos)
        b[:n] = self._buf[self._pos : self._pos + n]
        self._pos += n
        return n

    def check_first(self) -> None:
        """Decrypts the first chunk: a wrong password is found without reading the whole file."""
        if self._n == 0:
            self._next()


class _CountingReader(io.RawIOBase):
    """Reports how far into the file reading has got (for progress)."""

    def __init__(self, f: IO[bytes], on_read: Callable[[int], None]):
        self._f, self._on_read = f, on_read
        self.pos = 0

    def readable(self) -> bool:
        return True

    def readinto(self, b: Any) -> int:
        n = self._f.readinto(b)  # type: ignore[attr-defined]
        self.pos += n or 0
        self._on_read(self.pos)
        return n or 0


@contextmanager
def open_archive(
    path: Path, password: str | None = None, on_read: Callable[[int], None] | None = None
) -> Iterator[tuple[dict[str, Any], tarfile.TarFile]]:
    """Yields (header, tar stream). Raises BadPassword when a password is needed or wrong.
    `on_read(bytes)` is called with the position reached in the file."""
    with path.open("rb") as raw:
        header, head = read_header(raw)
        f: IO[bytes] = io.BufferedReader(_CountingReader(raw, on_read), 1 << 20) if on_read else raw
        source: IO[bytes] = f
        if header.get("encrypted"):
            if not password:
                raise BadPassword("Файл защищён паролем")
            try:
                key = _key(password, header["kdf"])
                dec = _DecryptingSource(f, key, base64.b64decode(header["nonce_prefix"]), head)
            except (KeyError, ValueError) as exc:
                raise Corrupted("Заголовок файла повреждён") from exc
            dec.check_first()
            source = io.BufferedReader(dec, CHUNK)
        reader = zstandard.ZstdDecompressor().stream_reader(source, read_across_frames=True, closefd=False)
        try:
            with tarfile.open(fileobj=reader, mode="r|") as tar:
                yield header, tar
        except (zstandard.ZstdError, tarfile.TarError, EOFError) as exc:
            raise Corrupted(f"Файл повреждён: {exc}") from exc


def read_json(tar: tarfile.TarFile, member: tarfile.TarInfo) -> Any:
    f = tar.extractfile(member)
    if f is None:
        raise Corrupted(f"Нет данных «{member.name}»")
    try:
        return json.loads(f.read())
    except ValueError as exc:
        raise Corrupted(f"Повреждена запись «{member.name}»") from exc
