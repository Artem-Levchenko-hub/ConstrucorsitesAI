"""Trusted Python program for a private, uncredentialed volume-write helper.

Guest paths are traversed with pinned no-follow directory descriptors. Renaming
a new inode never writes through an existing hardlink or a raced leaf symlink.
"""

SOURCE_WRITE_SCRIPT = r'''
import os
import stat
import sys
import tarfile
import uuid


def parts(name):
    value = name.split('/')
    if not name or chr(92) in name or chr(0) in name or any(p in ('', '.', '..') for p in value):
        raise ValueError('invalid source write path')
    return value


def target_stat(parent, name):
    try:
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return
    if not stat.S_ISREG(current.st_mode):
        raise ValueError('source write target is not a regular file')


def parent_fd(root, path):
    current = os.dup(root)
    try:
        for segment in path:
            try:
                os.mkdir(segment, mode=0o755, dir_fd=current)
            except FileExistsError:
                pass
            child = os.open(segment, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=current)
            os.close(current)
            current = child
        return current
    except BaseException:
        os.close(current)
        raise


def write_file(parent, name, data):
    target_stat(parent, name)
    temporary = '.omnia-write-' + uuid.uuid4().hex
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o644, dir_fd=parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        target_stat(parent, name)
        # A later leaf substitution can only be replaced, never followed.
        os.rename(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
        os.fsync(parent)
    finally:
        try:
            os.unlink(temporary, dir_fd=parent)
        except FileNotFoundError:
            pass


def apply(root_path, archive_path):
    # Parse the entire private payload before touching the workspace.
    entries = []
    names = set()
    total = 0
    with tarfile.open(archive_path, mode='r:') as archive:
        for count, member in enumerate(archive):
            if count >= 20000:
                raise ValueError('source write header budget exceeded')
            path = parts(member.name)
            if member.isdir():
                continue
            if not member.isfile() or member.name in names:
                raise ValueError('invalid source write payload')
            total += member.size
            if len(names) >= 5000 or member.size > 2 * 1024 * 1024 or total > 64 * 1024 * 1024:
                raise ValueError('source write payload exceeds budget')
            names.add(member.name)
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError('missing source write payload')
            data = stream.read()
            if len(data) != member.size:
                raise ValueError('incomplete source write payload')
            entries.append((path, data))
    root = os.open(root_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        # Validate every existing target before replacing any source file.
        # Do not retain one descriptor per file: production's soft limit is 1024.
        for path, _ in entries:
            parent = parent_fd(root, path[:-1])
            try:
                target_stat(parent, path[-1])
            finally:
                os.close(parent)
        for path, data in entries:
            # Reopen from the pinned root with no-follow and revalidate after
            # the first pass; the guest can mutate directories between passes.
            parent = parent_fd(root, path[:-1])
            try:
                write_file(parent, path[-1], data)
            finally:
                os.close(parent)
    finally:
        os.close(root)


try:
    archive_path = sys.argv[2]
    if len(sys.argv) == 4:
        size = int(sys.argv[3])
        if size < 0 or size > 128 * 1024 * 1024:
            raise ValueError('source write archive exceeds budget')
        with open(archive_path, 'xb') as output:
            remaining = size
            while remaining:
                chunk = sys.stdin.buffer.read(min(65536, remaining))
                if not chunk:
                    raise ValueError('incomplete source write upload')
                output.write(chunk)
                remaining -= len(chunk)
    apply(sys.argv[1], archive_path)
except Exception:
    print('guarded source write rejected', file=sys.stderr)
    sys.exit(1)
'''
