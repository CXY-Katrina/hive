"""Bounded durable binary logs, independent of workflow state and display tails.

Keys are caller-generated SHA-256 identities, never user paths. Appending a
replayed chunk verifies existing bytes and returns the file's authoritative
offset, covering a crash after disk flush but before the database update.
Archives are retained until an explicit future retention policy removes them.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import re
import stat
import threading

from .domain import DomainError


MAX_BYTES = 20 * 1024 * 1024
CHUNK = 65536
KEY = re.compile(r'[0-9a-f]{64}')
_LOCKS = [threading.RLock() for _ in range(64)]


def _require(condition, message, code=422):
    if not condition:
        raise DomainError(message, code)


def _plain_file(info):
    # Windows junctions and other reparse points also redirect paths.
    return not (stat.S_ISLNK(info.st_mode) or
                getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400))


class LogArchive:
    def __init__(self, data_dir, max_bytes=MAX_BYTES):
        _require(type(max_bytes) is int and 1 <= max_bytes <= MAX_BYTES, 'Invalid log archive capacity')
        self.max_bytes = max_bytes
        self.root = (Path(data_dir) / 'workflow-logs').absolute()
        self._directories(create=True)

    def _directories(self, create=False):
        for path in [*reversed(self.root.parents), self.root]:
            try:
                info = path.lstat()
            except FileNotFoundError:
                if not create:
                    raise DomainError('Log archive directory disappeared', 503) from None
                try:
                    path.mkdir(mode=0o700)
                except FileExistsError:
                    pass
                info = path.lstat()
            _require(_plain_file(info) and stat.S_ISDIR(info.st_mode),
                     'Log archive directory must not be a symbolic link or reparse point', 503)

    def _key(self, key):
        _require(isinstance(key, str) and KEY.fullmatch(key), 'Log key must be a lowercase SHA-256 identity')
        return self.root / (key + '.log')

    @contextmanager
    def _file(self, key, create=False):
        path = self._key(key)
        with _LOCKS[int(key[:2], 16) % len(_LOCKS)]:
            self._directories()
            try:
                old = path.lstat()
            except FileNotFoundError:
                old = None
            if old is not None:
                _require(_plain_file(old) and stat.S_ISREG(old.st_mode) and old.st_nlink == 1,
                         'Log file must be a single regular file, not a link', 503)
            flags = (os.O_RDWR | os.O_CREAT) if create else os.O_RDONLY
            flags |= getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
            try:
                descriptor = os.open(path, flags, 0o600)
            except FileNotFoundError:
                if not create:
                    yield None
                    return
                raise DomainError('Log archive directory disappeared', 503) from None
            except OSError:
                raise DomainError('Log archive file cannot be opened safely', 503) from None
            with os.fdopen(descriptor, 'r+b' if create else 'rb', buffering=0) as file:
                current, on_disk = os.fstat(file.fileno()), path.lstat()
                _require(_plain_file(on_disk) and stat.S_ISREG(current.st_mode) and current.st_nlink == 1
                         and (current.st_dev, current.st_ino) == (on_disk.st_dev, on_disk.st_ino),
                         'Log file identity changed while opening', 503)
                self._directories()
                if os.name == 'nt':
                    import msvcrt
                    file.seek(0)
                    msvcrt.locking(file.fileno(), msvcrt.LK_LOCK if create else msvcrt.LK_RLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(file.fileno(), fcntl.LOCK_EX if create else fcntl.LOCK_SH)
                try:
                    yield file
                finally:
                    if os.name == 'nt':
                        file.seek(0)
                        msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(file.fileno(), fcntl.LOCK_UN)

    def _size(self, file):
        size = os.fstat(file.fileno()).st_size
        _require(0 <= size <= self.max_bytes, 'Existing log exceeds archive capacity', 503)
        return size

    def append(self, key, offset, data):
        """Append at a checked offset; matching retransmissions never duplicate bytes."""
        self._key(key)
        _require(type(offset) is int and 0 <= offset <= self.max_bytes, 'Invalid log append offset')
        _require(isinstance(data, bytes) and len(data) <= CHUNK, 'Log chunks must be bytes, at most 64 KiB')
        with self._file(key, create=True) as file:
            size = self._size(file)
            _require(offset <= size, 'Log append would leave an uncollected gap', 409)
            accepted = data[:self.max_bytes - offset]
            overlap = min(len(accepted), size - offset)
            file.seek(offset)
            _require(file.read(overlap) == accepted[:overlap], 'Retried log chunk differs from archived bytes', 409)
            rest = memoryview(accepted)[overlap:]
            file.seek(size)
            while rest:
                written = file.write(rest)
                if not written:
                    raise DomainError('Log archive write did not complete', 503)
                rest = rest[written:]
            file.flush()
            os.fsync(file.fileno())
            size = self._size(file)
            return {'next_offset': size, 'size': size, 'truncated': size >= self.max_bytes}

    def read(self, key, offset=0, limit=CHUNK):
        """Read bounded raw bytes; download handlers can stream successive chunks."""
        self._key(key)
        _require(type(offset) is int and 0 <= offset <= self.max_bytes and type(limit) is int
                 and 1 <= limit <= CHUNK, 'Invalid log read range')
        with self._file(key) as file:
            if file is None:
                return dict(data=b'', next_offset=offset, size=0, eof=False, missing=True, truncated=False)
            size = self._size(file)
            _require(offset <= size, 'Log read offset is beyond archived data', 409)
            file.seek(offset)
            data = file.read(min(limit, size - offset))
            _require(len(data) == min(limit, size - offset), 'Log archive read is incomplete', 503)
            return dict(data=data, next_offset=offset + len(data), size=size, eof=offset + len(data) >= size,
                        missing=False, truncated=size >= self.max_bytes)
