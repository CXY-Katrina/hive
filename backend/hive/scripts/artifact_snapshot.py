"""Container-side bounded snapshots. Standard library only; never extract archives."""
import base64
import errno
import gzip
import hashlib
import json
import os
import re
import stat
import sys
import tarfile
import uuid

MAX_ENTRIES = 10000
CHUNK = 1024 * 1024
PREFIX = '/var/tmp/hive-artifact-'


class PolicyError(Exception):
    pass


def stable(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def open_directory(path):
    """Walk from / with pinned directory FDs, refusing every symlink ancestor."""
    descriptor = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in path[1:].split('/'):
            if component in {'', '.', '..'}:
                raise PolicyError('UNSAFE_PATH')
            next_descriptor = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                      dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


class LimitedWriter:
    def __init__(self, file, limit):
        self.file, self.limit, self.size = file, limit, 0

    def write(self, data):
        self.size += len(data)
        if self.size > self.limit:
            raise PolicyError('OVERSIZED')
        return self.file.write(data)

    def flush(self):
        self.file.flush()


def create(path, limit):
    token = uuid.uuid4().hex
    destination = PREFIX + token + '.tar.gz'
    entries, total, discovered = 0, 0, 1

    def add(archive, fd, name):
        nonlocal entries, total, discovered
        if name.count('/') > 128:
            raise PolicyError('TOO_DEEP')
        before = os.fstat(fd)
        entries += 1
        if entries > MAX_ENTRIES:
            raise PolicyError('TOO_MANY_ENTRIES')
        info = tarfile.TarInfo(name)
        info.mode = stat.S_IMODE(before.st_mode) & 0o777
        info.mtime = int(before.st_mtime)
        if stat.S_ISDIR(before.st_mode):
            info.type = tarfile.DIRTYPE
            archive.addfile(info)
            # scandir is lazy: even a huge hostile directory stops at the cap.
            with os.scandir(fd) as scan:
                names = []
                for entry in scan:
                    discovered += 1
                    if discovered > MAX_ENTRIES:
                        raise PolicyError('TOO_MANY_ENTRIES')
                    names.append(entry.name)
            for child in sorted(names):
                observed = os.stat(child, dir_fd=fd, follow_symlinks=False)
                if not (stat.S_ISREG(observed.st_mode) or stat.S_ISDIR(observed.st_mode)):
                    raise PolicyError('SPECIAL_FILE')
                flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                if stat.S_ISDIR(observed.st_mode):
                    flags |= os.O_DIRECTORY
                child_fd = os.open(child, flags, dir_fd=fd)
                try:
                    if stable(os.fstat(child_fd)) != stable(observed):
                        raise PolicyError('CHANGED')
                    add(archive, child_fd, name + '/' + child)
                finally:
                    os.close(child_fd)
        elif stat.S_ISREG(before.st_mode):
            if before.st_nlink != 1:
                raise PolicyError('SPECIAL_FILE')
            total += before.st_size
            if total > limit:
                raise PolicyError('OVERSIZED')
            info.size = before.st_size
            with os.fdopen(os.dup(fd), 'rb') as source:
                archive.addfile(info, source)
        else:
            raise PolicyError('SPECIAL_FILE')
        if stable(os.fstat(fd)) != stable(before):
            raise PolicyError('CHANGED')

    root = open_directory(path)
    try:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'wb') as output:
            with gzip.GzipFile(filename='', mode='wb', fileobj=LimitedWriter(output, limit), mtime=0) as compressed:
                with tarfile.open(fileobj=compressed, mode='w|', format=tarfile.PAX_FORMAT) as archive:
                    add(archive, root, path.rsplit('/', 1)[-1])
        with open(destination, 'rb') as output:
            digest = hashlib.file_digest(output, 'sha256').hexdigest() if hasattr(hashlib, 'file_digest') else digest_file(output)
        return dict(token=token, size=os.stat(destination).st_size, sha256=digest,
                    unpacked_size=total, entry_count=entries)
    except BaseException:
        if os.path.exists(destination):
            os.unlink(destination)
        raise
    finally:
        os.close(root)


def digest_file(file):
    digest = hashlib.sha256()
    for block in iter(lambda: file.read(CHUNK), b''):
        digest.update(block)
    return digest.hexdigest()


def main(args):
    action = args[0]
    if action == 'create':
        print(json.dumps(create(args[1], int(args[2]))))
        return
    token = args[1]
    if not re.fullmatch(r'[0-9a-f]{32}', token):
        raise PolicyError('UNSAFE_PATH')
    path = PREFIX + token + '.tar.gz'
    if action == 'drop':
        os.unlink(path)
        print('REMOVED')
    elif action == 'read':
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as file:
            if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):
                raise PolicyError('SPECIAL_FILE')
            file.seek(int(args[2]))
            print(base64.b64encode(file.read(CHUNK)).decode())
    else:
        raise PolicyError('UNSAFE_PATH')


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except PolicyError as error:
        print(json.dumps({'error': str(error)}))
    except (FileNotFoundError, NotADirectoryError):
        print(json.dumps({'error': 'MISSING_OR_UNSAFE_PATH'}))
    except OSError as error:
        print(json.dumps({'error': 'UNSAFE_PATH' if error.errno in {errno.ELOOP, errno.ENOTDIR}
                          else 'IO_OR_UNSAFE_PATH'}))
