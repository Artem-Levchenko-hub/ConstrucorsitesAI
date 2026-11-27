"""Current-user Windows DPAPI wrappers with a protected current-user-only DACL.

YLEUM_RECOVERY_HOME is a directory selector, never a key transport. Native APIs
are loaded lazily; unsupported platforms and permissive/untrusted stores fail closed.
"""

import ctypes
import os
import tempfile
from ctypes import wintypes
from pathlib import Path
from typing import Any

from .profile import _check_path, _validate_name
from .redaction import LocalUnlockError


class _Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


class _TokenUser(ctypes.Structure):
    _fields_ = [("sid", ctypes.c_void_p), ("attributes", wintypes.DWORD)]


class _Acl(ctypes.Structure):
    _fields_ = [
        ("revision", ctypes.c_ubyte),
        ("reserved", ctypes.c_ubyte),
        ("size", wintypes.WORD),
        ("count", wintypes.WORD),
        ("reserved2", wintypes.WORD),
    ]


def _platform() -> str:
    return os.name


def _require_windows() -> None:
    if _platform() != "nt":
        raise LocalUnlockError()


def _libraries() -> tuple[Any, Any, Any]:
    _require_windows()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    security = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    pointer = ctypes.c_void_p
    blob = ctypes.POINTER(_Blob)
    for name in ("CryptProtectData", "CryptUnprotectData"):
        func = getattr(crypt, name)
        func.argtypes = [blob, pointer, blob, pointer, pointer, wintypes.DWORD, blob]
        func.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [pointer]
    kernel.LocalFree.restype = pointer
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    security.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    security.OpenProcessToken.restype = wintypes.BOOL
    security.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        pointer,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    security.GetTokenInformation.restype = wintypes.BOOL
    security.ConvertSidToStringSidW.argtypes = [pointer, ctypes.POINTER(pointer)]
    security.ConvertSidToStringSidW.restype = wintypes.BOOL
    security.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(pointer),
        pointer,
    ]
    security.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    security.GetSecurityDescriptorOwner.argtypes = [
        pointer,
        ctypes.POINTER(pointer),
        ctypes.POINTER(wintypes.BOOL),
    ]
    security.GetSecurityDescriptorOwner.restype = wintypes.BOOL
    security.GetSecurityDescriptorDacl.argtypes = [
        pointer,
        ctypes.POINTER(wintypes.BOOL),
        ctypes.POINTER(pointer),
        ctypes.POINTER(wintypes.BOOL),
    ]
    security.GetSecurityDescriptorDacl.restype = wintypes.BOOL
    security.SetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        pointer,
        pointer,
        pointer,
        pointer,
    ]
    security.SetNamedSecurityInfoW.restype = wintypes.DWORD
    security.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPCWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.POINTER(pointer),
        pointer,
        ctypes.POINTER(pointer),
        pointer,
        ctypes.POINTER(pointer),
    ]
    security.GetNamedSecurityInfoW.restype = wintypes.DWORD
    security.GetSecurityDescriptorControl.argtypes = [
        pointer,
        ctypes.POINTER(wintypes.WORD),
        ctypes.POINTER(wintypes.DWORD),
    ]
    security.GetSecurityDescriptorControl.restype = wintypes.BOOL
    security.EqualSid.argtypes = [pointer, pointer]
    security.EqualSid.restype = wintypes.BOOL
    return crypt, security, kernel


def _dpapi(data: bytes, entropy: bytes, *, decrypt: bool) -> bytes:
    crypt, _, kernel = _libraries()
    data_buffer = ctypes.create_string_buffer(data)
    entropy_buffer = ctypes.create_string_buffer(entropy)
    input_blob = _Blob(len(data), ctypes.cast(data_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    entropy_blob = _Blob(len(entropy), ctypes.cast(entropy_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = _Blob()
    func = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    # CRYPTPROTECT_UI_FORBIDDEN only; never CRYPTPROTECT_LOCAL_MACHINE.
    if not func(
        ctypes.byref(input_blob),
        None,
        ctypes.byref(entropy_blob),
        None,
        None,
        0x1,
        ctypes.byref(output),
    ):
        raise LocalUnlockError()
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        ctypes.memset(output.data, 0, output.size)
        kernel.LocalFree(output.data)
        ctypes.memset(data_buffer, 0, len(data))


def _dpapi_protect(data: bytes, entropy: bytes) -> bytes:
    return _dpapi(data, entropy, decrypt=False)


def _dpapi_unprotect(data: bytes, entropy: bytes) -> bytes:
    return _dpapi(data, entropy, decrypt=True)


def _current_sid(security: Any, kernel: Any) -> str:
    token = wintypes.HANDLE()
    if not security.OpenProcessToken(kernel.GetCurrentProcess(), 0x8, ctypes.byref(token)):
        raise LocalUnlockError()
    try:
        needed = wintypes.DWORD()
        security.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
        if not needed.value:
            raise LocalUnlockError()
        buffer = ctypes.create_string_buffer(needed.value)
        if not security.GetTokenInformation(token, 1, buffer, needed, ctypes.byref(needed)):
            raise LocalUnlockError()
        user = ctypes.cast(buffer, ctypes.POINTER(_TokenUser)).contents
        text = ctypes.c_void_p()
        if not security.ConvertSidToStringSidW(user.sid, ctypes.byref(text)):
            raise LocalUnlockError()
        try:
            return ctypes.wstring_at(text)
        finally:
            kernel.LocalFree(text)
    finally:
        kernel.CloseHandle(token)


def _expected_descriptor(security: Any, kernel: Any, *, directory: bool) -> ctypes.c_void_p:
    sid = _current_sid(security, kernel)
    # Full control for exactly the token's user, protected from inherited ACEs.
    inheritance = "OICI" if directory else ""
    sddl = f"O:{sid}D:P(A;{inheritance};FA;;;{sid})"
    descriptor = ctypes.c_void_p()
    if not security.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, 1, ctypes.byref(descriptor), None
    ):
        raise LocalUnlockError()
    return descriptor


def _descriptor_parts(security: Any, descriptor: ctypes.c_void_p) -> tuple[Any, Any]:
    owner, dacl = ctypes.c_void_p(), ctypes.c_void_p()
    defaulted, present = wintypes.BOOL(), wintypes.BOOL()
    if not security.GetSecurityDescriptorOwner(
        descriptor, ctypes.byref(owner), ctypes.byref(defaulted)
    ):
        raise LocalUnlockError()
    if (
        not security.GetSecurityDescriptorDacl(
            descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)
        )
        or not present.value
        or not dacl
    ):
        raise LocalUnlockError()
    return owner, dacl


def _restrict_acl(path: Path) -> None:
    _check_path(path)
    _, security, kernel = _libraries()
    descriptor = _expected_descriptor(security, kernel, directory=path.is_dir())
    try:
        owner, dacl = _descriptor_parts(security, descriptor)
        # SE_FILE_OBJECT=1; OWNER|DACL|PROTECTED_DACL. No POSIX permission substitute.
        result = security.SetNamedSecurityInfoW(str(path), 1, 0x80000005, owner, None, dacl, None)
        if result:
            raise LocalUnlockError()
    finally:
        kernel.LocalFree(descriptor)
    _verify_acl(path)


def _acl_bytes(dacl: Any) -> bytes:
    return ctypes.string_at(dacl, ctypes.cast(dacl, ctypes.POINTER(_Acl)).contents.size)


def _verify_acl(path: Path) -> None:
    _check_path(path)
    _, security, kernel = _libraries()
    descriptor, owner, dacl = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
    if security.GetNamedSecurityInfoW(
        str(path),
        1,
        0x5,
        ctypes.byref(owner),
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(descriptor),
    ):
        raise LocalUnlockError()
    expected = ctypes.c_void_p()
    try:
        expected = _expected_descriptor(security, kernel, directory=path.is_dir())
        expected_owner, expected_dacl = _descriptor_parts(security, expected)
        control, revision = wintypes.WORD(), wintypes.DWORD()
        if not security.GetSecurityDescriptorControl(
            descriptor, ctypes.byref(control), ctypes.byref(revision)
        ):
            raise LocalUnlockError()
        if (
            not control.value & 0x1000
            or not dacl
            or not owner
            or not security.EqualSid(owner, expected_owner)
            or _acl_bytes(dacl) != _acl_bytes(expected_dacl)
        ):
            raise LocalUnlockError()
    finally:
        kernel.LocalFree(descriptor)
        if expected:
            kernel.LocalFree(expected)


def _key_path(name: str) -> Path:
    _require_windows()
    _validate_name(name)
    value = os.environ.get("YLEUM_RECOVERY_HOME")
    if not value:
        raise LocalUnlockError()
    home = Path(value)
    path = home / ".local-keys" / f"{name}.dpapi"
    _check_path(path)
    if not home.is_dir():
        raise LocalUnlockError()
    return path


def _entropy(name: str) -> bytes:
    return b"yleum-recovery-key-v1\x00" + name.encode("ascii")


def activate_local_key(name: str, key: bytes) -> None:
    temporary: Path | None = None
    try:
        path = _key_path(name)
        if not isinstance(key, bytes) or len(key) != 32:
            raise LocalUnlockError()
        wrapped = _dpapi_protect(key, _entropy(name))
        try:
            path.parent.mkdir()
        except FileExistsError:
            _verify_acl(path.parent)
        else:
            _restrict_acl(path.parent)
        _check_path(path)
        if path.exists():
            _verify_acl(path)
        fd, filename = tempfile.mkstemp(prefix=".key-", dir=path.parent)
        temporary = Path(filename)
        with os.fdopen(fd, "wb") as stream:
            _restrict_acl(temporary)
            stream.write(wrapped)
            stream.flush()
            os.fsync(stream.fileno())
        _check_path(path)
        os.replace(temporary, path)
        temporary = None
        _verify_acl(path)
    except Exception:
        raise LocalUnlockError() from None
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                raise LocalUnlockError() from None


def load_local_key(name: str) -> bytes:
    try:
        path = _key_path(name)
        _verify_acl(path.parent)
        _verify_acl(path)
        key = _dpapi_unprotect(path.read_bytes(), _entropy(name))
        if len(key) != 32:
            raise LocalUnlockError()
        return key
    except Exception:
        raise LocalUnlockError() from None
