"""Secret storage for Subway MCP credentials.

macOS stores secrets in the native Keychain (ctypes SecItem calls). Windows and
Linux have no single OS keychain Subway can rely on, so they fall back to a
0600-permission file store under ~/.subway/secrets/ — best effort, but it keeps
OAuth flows working instead of raising NotImplementedError on every call.
"""

import ctypes
import os
import sys


SUBWAY_MCP_SERVICE = "Subway-MCP-credentials"


class KeychainError(Exception):
    def __init__(self, code: int) -> None:
        self.code = int(code)
        if self.code in (-128, -60006):
            message = f"User declined a Keychain prompt (OSStatus {self.code})"
        else:
            message = f"Keychain operation failed with OSStatus {self.code}"
        super().__init__(message)


class _CFDictionaryCallbacks(ctypes.Structure):
    _fields_ = [
        ("version", ctypes.c_long),
        ("retain", ctypes.c_void_p),
        ("release", ctypes.c_void_p),
        ("copyDescription", ctypes.c_void_p),
        ("equal", ctypes.c_void_p),
        ("hash", ctypes.c_void_p),
    ]


_cf = None
_security = None
_backend = None
_ERR_SEC_ITEM_NOT_FOUND = -25300
_UTF8_ENCODING = 0x08000100


def _require_macos() -> None:
    if sys.platform != "darwin":
        raise NotImplementedError("Keychain storage needs macOS")


def _validate_identity(service: str, account: str) -> None:
    for name, value in (("service", service), ("account", account)):
        if not isinstance(value, str) or not value or "\0" in value:
            raise ValueError(f"{name} must be a non-empty string without NUL")


def _validate_secret(secret: bytes) -> None:
    if not isinstance(secret, bytes) or len(secret) > 64 * 1024:
        raise ValueError("secret must be bytes no larger than 64 KB")


def _load_frameworks():
    global _cf, _security
    if _cf is None or _security is None:
        _cf = ctypes.CDLL(
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
        )
        _security = ctypes.CDLL(
            "/System/Library/Frameworks/Security.framework/Security"
        )

        _cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        _cf.CFStringCreateWithCString.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_uint32,
        ]
        _cf.CFDataCreate.restype = ctypes.c_void_p
        _cf.CFDataCreate.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_long,
        ]
        _cf.CFDictionaryCreate.restype = ctypes.c_void_p
        _cf.CFDictionaryCreate.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_long,
            ctypes.POINTER(_CFDictionaryCallbacks),
            ctypes.POINTER(_CFDictionaryCallbacks),
        ]
        _cf.CFDataGetBytePtr.restype = ctypes.POINTER(ctypes.c_uint8)
        _cf.CFDataGetBytePtr.argtypes = [ctypes.c_void_p]
        _cf.CFDataGetLength.restype = ctypes.c_long
        _cf.CFDataGetLength.argtypes = [ctypes.c_void_p]
        _cf.CFRelease.restype = None
        _cf.CFRelease.argtypes = [ctypes.c_void_p]

        _security.SecItemUpdate.restype = ctypes.c_int32
        _security.SecItemUpdate.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        _security.SecItemAdd.restype = ctypes.c_int32
        _security.SecItemAdd.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        _security.SecItemCopyMatching.restype = ctypes.c_int32
        _security.SecItemCopyMatching.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        _security.SecItemDelete.restype = ctypes.c_int32
        _security.SecItemDelete.argtypes = [ctypes.c_void_p]
        _security.SecKeychainCopyDefault.restype = ctypes.c_int32
        _security.SecKeychainCopyDefault.argtypes = [
            ctypes.POINTER(ctypes.c_void_p)
        ]
    return _cf, _security


def _constant(library, name: str) -> int:
    return ctypes.c_void_p.in_dll(library, name).value


def _create_string(cf, value: str) -> int:
    result = cf.CFStringCreateWithCString(
        None, value.encode("utf-8"), _UTF8_ENCODING
    )
    if not result:
        raise MemoryError("Could not create Keychain string")
    return result


def _create_dictionary(cf, keys, values) -> int:
    key_callbacks = _CFDictionaryCallbacks.in_dll(
        cf, "kCFTypeDictionaryKeyCallBacks"
    )
    value_callbacks = _CFDictionaryCallbacks.in_dll(
        cf, "kCFTypeDictionaryValueCallBacks"
    )
    key_array = (ctypes.c_void_p * len(keys))(*keys)
    value_array = (ctypes.c_void_p * len(values))(*values)
    result = cf.CFDictionaryCreate(
        None,
        key_array,
        value_array,
        len(keys),
        ctypes.byref(key_callbacks),
        ctypes.byref(value_callbacks),
    )
    if not result:
        raise MemoryError("Could not create Keychain attributes")
    return result


def _base_attributes(cf, service: str, account: str, extras=()):
    owned = []
    try:
        service_value = _create_string(cf, service)
        owned.append(service_value)
        account_value = _create_string(cf, account)
        owned.append(account_value)
        keys = [
            _constant(_security, "kSecClass"),
            _constant(_security, "kSecAttrService"),
            _constant(_security, "kSecAttrAccount"),
        ]
        values = [
            _constant(_security, "kSecClassGenericPassword"),
            service_value,
            account_value,
        ]
        for key_name, value in extras:
            keys.append(_constant(_security, key_name))
            values.append(value)
        dictionary = _create_dictionary(cf, keys, values)
        return dictionary, owned
    except BaseException:
        for value in reversed(owned):
            cf.CFRelease(value)
        raise


def _copy_default_keychain(cf, security) -> None:
    keychain = ctypes.c_void_p()
    status = security.SecKeychainCopyDefault(ctypes.byref(keychain))
    try:
        if status != 0:
            raise KeychainError(status)
    finally:
        _release(cf, keychain.value)


def _authentication_ui_skip(security):
    try:
        key = ctypes.c_void_p.in_dll(
            security, "kSecUseAuthenticationUI"
        ).value
        value = ctypes.c_void_p.in_dll(
            security, "kSecUseAuthenticationUISkip"
        ).value
    except ValueError:
        return ()
    return (("kSecUseAuthenticationUI", value),) if key is not None else ()


def _release(cf, *refs) -> None:
    for ref in reversed(refs):
        if ref:
            cf.CFRelease(ref)


class _CtypesBackend:
    def add(self, service: str, account: str, secret: bytes, label: str) -> int:
        cf, security = _load_frameworks()
        _copy_default_keychain(cf, security)
        label_ref = None
        secret_data = None
        attributes = None
        owned = []
        try:
            label_ref = _create_string(cf, label)
            secret_data = _create_secret_data(cf, secret)
            accessible = _constant(
                security, "kSecAttrAccessibleWhenUnlockedThisDeviceOnly"
            )
            attributes, owned = _base_attributes(
                cf,
                service,
                account,
                (
                    ("kSecValueData", secret_data),
                    ("kSecAttrAccessible", accessible),
                    ("kSecAttrLabel", label_ref),
                ),
            )
            return security.SecItemAdd(attributes, None)
        finally:
            _release(cf, attributes, *owned, secret_data, label_ref)

    def update(self, service: str, account: str, secret: bytes) -> int:
        cf, security = _load_frameworks()
        _copy_default_keychain(cf, security)
        label = None
        secret_data = None
        query = None
        attributes = None
        owned = []
        try:
            label = _create_string(cf, "Subway MCP")
            secret_data = _create_secret_data(cf, secret)
            accessible = _constant(
                security, "kSecAttrAccessibleWhenUnlockedThisDeviceOnly"
            )
            query, owned = _base_attributes(cf, service, account)
            attributes = _create_dictionary(
                cf,
                [
                    _constant(security, "kSecValueData"),
                    _constant(security, "kSecAttrAccessible"),
                    _constant(security, "kSecAttrLabel"),
                ],
                [secret_data, accessible, label],
            )
            return security.SecItemUpdate(query, attributes)
        finally:
            _release(cf, attributes, query, *owned, secret_data, label)

    def copy(self, service: str, account: str) -> tuple[int, bytes | None]:
        cf, security = _load_frameworks()
        query = None
        owned = []
        result = ctypes.c_void_p()
        try:
            query, owned = _base_attributes(
                cf,
                service,
                account,
                (
                    ("kSecReturnData", _constant(cf, "kCFBooleanTrue")),
                    ("kSecMatchLimit", _constant(security, "kSecMatchLimitOne")),
                )
                + _authentication_ui_skip(security),
            )
            status = security.SecItemCopyMatching(query, ctypes.byref(result))
            if status != 0:
                return status, None
            length = cf.CFDataGetLength(result)
            data_ptr = cf.CFDataGetBytePtr(result)
            data = ctypes.string_at(data_ptr, length) if length else b""
            return status, data
        finally:
            _release(cf, result.value, query, *owned)

    def delete(self, service: str, account: str) -> int:
        cf, security = _load_frameworks()
        query, owned = _base_attributes(
            cf,
            service,
            account,
            _authentication_ui_skip(security),
        )
        try:
            return security.SecItemDelete(query)
        finally:
            _release(cf, query, *owned)


def _create_secret_data(cf, secret: bytes) -> int:
    secret_buffer = ctypes.create_string_buffer(secret, max(1, len(secret)))
    secret_data = cf.CFDataCreate(
        None,
        ctypes.cast(secret_buffer, ctypes.POINTER(ctypes.c_uint8)),
        len(secret),
    )
    if not secret_data:
        raise MemoryError("Could not create Keychain data")
    return secret_data


class _FileBackend:
    """File-backed secret store for platforms without a native keychain.

    Mirrors _CtypesBackend's status semantics: 0 on success,
    _ERR_SEC_ITEM_NOT_FOUND (-25300) when the item does not exist.
    """

    def __init__(self, directory: str) -> None:
        self._dir = directory
        try:
            os.makedirs(self._dir, mode=0o700, exist_ok=True)
            os.chmod(self._dir, 0o700)
        except OSError:
            pass  # Best effort: a read-only or exotic home dir still beats a hard crash.

    def _path(self, service: str, account: str) -> str:
        raw = f"{service}.{account}"
        safe = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in raw)
        return os.path.join(self._dir, safe + ".secret")

    def _write(self, path: str, secret: bytes) -> None:
        tmp = path + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(secret)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)

    def add(self, service: str, account: str, secret: bytes, label: str = "") -> int:
        self._write(self._path(service, account), secret)
        return 0

    def update(self, service: str, account: str, secret: bytes) -> int:
        self._write(self._path(service, account), secret)
        return 0

    def copy(self, service: str, account: str) -> tuple[int, bytes | None]:
        try:
            with open(self._path(service, account), "rb") as fh:
                return 0, fh.read()
        except FileNotFoundError:
            return _ERR_SEC_ITEM_NOT_FOUND, None

    def delete(self, service: str, account: str) -> int:
        try:
            os.remove(self._path(service, account))
        except FileNotFoundError:
            return _ERR_SEC_ITEM_NOT_FOUND
        return 0


def _secrets_dir() -> str:
    override = os.environ.get("SUBWAY_DIR", "").strip()
    base = override if override else os.path.join(os.path.expanduser("~"), ".subway")
    return os.path.join(base, "secrets")


def _get_backend():
    global _backend
    if _backend is None:
        if sys.platform == "darwin":
            _backend = _CtypesBackend()
        else:
            _backend = _FileBackend(_secrets_dir())
    return _backend


def _set_backend(backend) -> None:
    global _backend
    _backend = backend


def set_secret(service: str, account: str, secret: bytes) -> None:
    _validate_identity(service, account)
    _validate_secret(secret)
    backend = _get_backend()
    status = backend.update(service, account, secret)
    if status == _ERR_SEC_ITEM_NOT_FOUND:
        status = backend.add(service, account, secret, "Subway MCP")
    if status != 0:
        raise KeychainError(status)


def get_secret(service: str, account: str) -> bytes | None:
    _validate_identity(service, account)
    status, secret = _get_backend().copy(service, account)
    if status == _ERR_SEC_ITEM_NOT_FOUND:
        return None
    if status != 0:
        raise KeychainError(status)
    return secret


def delete_secret(service: str, account: str) -> None:
    _validate_identity(service, account)
    status = _get_backend().delete(service, account)
    if status not in (0, _ERR_SEC_ITEM_NOT_FOUND):
        raise KeychainError(status)
