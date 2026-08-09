from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from threading import RLock
from typing import Protocol


class SecretStoreUnavailable(RuntimeError):
    pass


class SecretStore(Protocol):
    def get(self, key: str) -> str: ...
    def set(self, key: str, value: str) -> None: ...
    def delete(self, key: str) -> None: ...


class InMemorySecretStore:
    def __init__(self) -> None:
        self._values: dict[str, str] = {}
        self._lock = RLock()

    def get(self, key: str) -> str:
        with self._lock:
            return self._values.get(key, "")

    def set(self, key: str, value: str) -> None:
        with self._lock:
            if value:
                self._values[key] = value
            else:
                self._values.pop(key, None)

    def delete(self, key: str) -> None:
        self.set(key, "")


class _Credential(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


class WindowsCredentialSecretStore:
    _TYPE_GENERIC = 1
    _PERSIST_LOCAL_MACHINE = 2

    def __init__(self) -> None:
        if os.name != "nt":
            raise SecretStoreUnavailable("Windows Credential Manager is unavailable")
        self._api = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        self._api.CredWriteW.argtypes = [ctypes.POINTER(_Credential), wintypes.DWORD]
        self._api.CredWriteW.restype = wintypes.BOOL
        self._api.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(_Credential))]
        self._api.CredReadW.restype = wintypes.BOOL
        self._api.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        self._api.CredDeleteW.restype = wintypes.BOOL
        self._api.CredFree.argtypes = [ctypes.c_void_p]

    @staticmethod
    def _target(key: str) -> str:
        return f"Planix/{key}"

    def get(self, key: str) -> str:
        pointer = ctypes.POINTER(_Credential)()
        if not self._api.CredReadW(self._target(key), self._TYPE_GENERIC, 0, ctypes.byref(pointer)):
            error = ctypes.get_last_error()
            if error == 1168:
                return ""
            raise SecretStoreUnavailable(f"Credential Manager read failed ({error})")
        try:
            credential = pointer.contents
            raw = ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize)
            return raw.decode("utf-16-le")
        finally:
            self._api.CredFree(pointer)

    def set(self, key: str, value: str) -> None:
        if not value:
            self.delete(key)
            return
        raw = value.encode("utf-16-le")
        blob = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
        credential = _Credential(
            Type=self._TYPE_GENERIC,
            TargetName=self._target(key),
            CredentialBlobSize=len(raw),
            CredentialBlob=ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte)),
            Persist=self._PERSIST_LOCAL_MACHINE,
            UserName="Planix",
        )
        if not self._api.CredWriteW(ctypes.byref(credential), 0):
            raise SecretStoreUnavailable(f"Credential Manager write failed ({ctypes.get_last_error()})")

    def delete(self, key: str) -> None:
        if not self._api.CredDeleteW(self._target(key), self._TYPE_GENERIC, 0):
            error = ctypes.get_last_error()
            if error != 1168:
                raise SecretStoreUnavailable(f"Credential Manager delete failed ({error})")


_memory_store = InMemorySecretStore()
_store: SecretStore | None = None


def get_secret_store() -> SecretStore:
    global _store
    if os.getenv("PLANIX_SECRET_STORE", "").casefold() == "memory":
        return _memory_store
    if _store is None:
        _store = WindowsCredentialSecretStore()
    return _store


def provider_secret_key(provider: str) -> str:
    return f"provider/{provider}/api-key"


__all__ = ["InMemorySecretStore", "SecretStore", "SecretStoreUnavailable", "get_secret_store", "provider_secret_key"]
