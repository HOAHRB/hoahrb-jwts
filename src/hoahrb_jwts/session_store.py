"""成功验证会话后原子回写 Cookie，保留其他配置、注释和换行。"""

import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from http.cookiejar import Cookie, CookieJar
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values

from .errors import ConfigError

_ASSIGNMENT = re.compile(
    r"^(?P<prefix>[ \t]*(?:export[ \t]+)?HIT_JW_COOKIE[ \t]*=[ \t]*)"
    r'(?P<value>"(?:\\.|[^"\r\n])*"|\'(?:\\.|[^\'\r\n])*\'|[^#\r\n]*?)'
    r"(?P<suffix>[ \t]*(?:\#[^\r\n]*)?)(?P<newline>\r?\n|$)",
    re.MULTILINE,
)


@contextmanager
def _locked(path: Path):
    lock_path = path.with_name(path.name + ".lock")
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except OSError:
        raise ConfigError("Cookie 配置正在写入或无法加锁，请稍后重试") from None
    try:
        yield
    finally:
        os.close(descriptor)
        lock_path.unlink(missing_ok=True)


def persist_cookie(path: Path, cookie: str, *, expected: str) -> None:
    with _locked(path):
        _persist_cookie(path, cookie, expected=expected)


def _persist_cookie(path: Path, cookie: str, *, expected: str) -> None:
    if not cookie or "\r" in cookie or "\n" in cookie:
        raise ConfigError("刷新后的 Cookie 无效")
    temporary = None
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            original = stream.read()
        has_bom = path.read_bytes().startswith(b"\xef\xbb\xbf")
        current = dotenv_values(path, encoding="utf-8-sig", interpolate=False).get("HIT_JW_COOKIE")
        if current != expected or len(list(_ASSIGNMENT.finditer(original))) != 1:
            raise ConfigError("Cookie 配置已被修改或存在重复配置，未覆盖本地文件")
        quoted = '"' + cookie.replace("\\", "\\\\").replace('"', '\\"') + '"'
        updated = _ASSIGNMENT.sub(
            lambda m: m["prefix"] + quoted + m["suffix"] + m["newline"], original, count=1
        )
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8-sig" if has_bom else "utf-8",
            newline="",
            dir=path.parent,
            prefix=".jwts-cookie-",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(updated)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, path.stat().st_mode & 0o777)
        os.replace(temporary, path)
    except OSError:
        raise ConfigError("无法回写教务 Cookie 配置") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class SessionCookieStore:
    """Save a verified jar, retaining domain/path/expiry, bound to the input credentials.

    The dotenv header remains compatible with existing tools. The sidecar is the
    authoritative scoped jar only while its credential fingerprint still matches.
    Each file is atomic; a crash between replacements leaves an ignored sidecar,
    never one attached to a different manually supplied credential.
    """

    def __init__(self, path: Path, cookie: str, base_url: str, cas_cookie: str = ""):
        self.path = path
        self.state_path = path.with_name(path.name + ".cookies.json")
        self.cookie = cookie
        self.base_url = base_url
        self.cas_cookie = cas_cookie
        self._state = self._read_state()

    def _read_state(self) -> bytes | None:
        try:
            return self.state_path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError:
            raise ConfigError("无法读取教务会话缓存") from None

    def _fingerprint(self, cookie: str) -> str:
        return hashlib.sha256(
            json.dumps([self.base_url, cookie, self.cas_cookie], ensure_ascii=True).encode()
        ).hexdigest()

    def restore(self, jar: CookieJar) -> None:
        if self._state is None:
            return
        try:
            state = json.loads(self._state)
            if state.get("fingerprint") != self._fingerprint(self.cookie):
                return
            if state.get("version") != 1 or not isinstance(state.get("cookies"), list):
                raise ValueError
            allowed = {urlsplit(self.base_url).hostname, "ids.hit.edu.cn"}
            restored = []
            for item in state["cookies"]:
                item = dict(item)
                item["rest"] = item.pop("_rest", {})
                entry = Cookie(**item)
                if entry.domain.lstrip(".") not in allowed or not entry.path.startswith("/"):
                    raise ValueError
                if entry.domain.lstrip(".") == "ids.hit.edu.cn" and not entry.secure:
                    raise ValueError
                if not entry.is_expired():
                    restored.append(entry)
            jar.clear()
            for entry in restored:
                jar.set_cookie(entry)
        except (ValueError, TypeError, KeyError, AttributeError):
            raise ConfigError("教务会话缓存损坏，请移除缓存后重新验证凭据") from None

    def save(self, cookie: str, jar: CookieJar) -> None:
        allowed = {urlsplit(self.base_url).hostname, "ids.hit.edu.cn"}
        entries = [
            entry.__dict__
            for entry in jar
            if entry.domain.lstrip(".") in allowed
            and not entry.is_expired()
            and (entry.domain.lstrip(".") != "ids.hit.edu.cn" or entry.secure)
        ]
        state = json.dumps(
            {"version": 1, "fingerprint": self._fingerprint(cookie), "cookies": entries},
            ensure_ascii=True,
        ).encode()
        temporary = None
        with _locked(self.path):
            try:
                if self._read_state() != self._state:
                    raise ConfigError("教务会话已被其他进程更新，未覆盖较新的凭据")
                # Check dotenv even when the header did not change (e.g. CAS rotation).
                current = dotenv_values(self.path, encoding="utf-8-sig", interpolate=False).get(
                    "HIT_JW_COOKIE"
                )
                if current != self.cookie:
                    raise ConfigError("Cookie 配置已被修改，未覆盖本地文件")
                current_cas = (
                    dotenv_values(self.path, encoding="utf-8-sig", interpolate=False).get(
                        "HIT_JW_CAS_COOKIE", ""
                    )
                    or ""
                )
                if current_cas.strip() != self.cas_cookie:
                    raise ConfigError("统一认证凭据已被修改，未覆盖本地文件")
                if state == self._state and cookie == self.cookie:
                    return
                with tempfile.NamedTemporaryFile(
                    mode="wb", dir=self.path.parent, prefix=".jwts-state-", delete=False
                ) as stream:
                    temporary = Path(stream.name)
                    stream.write(state)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chmod(temporary, 0o600)
                if cookie != self.cookie:
                    _persist_cookie(self.path, cookie, expected=self.cookie)
                os.replace(temporary, self.state_path)
                self.cookie = cookie
                self._state = state
            except OSError:
                raise ConfigError("无法持久化教务会话") from None
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
