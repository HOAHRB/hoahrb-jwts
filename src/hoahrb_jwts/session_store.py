"""Atomic persistence of verified, scoped teaching and CAS cookies only."""

import json
import os
import tempfile
from contextlib import contextmanager
from http.cookiejar import Cookie, CookieJar
from pathlib import Path

from .cookie_policy import accepted
from .errors import ConfigError


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


class SessionCookieStore:
    """Store credentials independently of dotenv; reject concurrent overwrites."""

    def __init__(self, path: Path, base_url: str):
        self.state_path = path
        self.base_url = base_url
        self._state = self._read_state()

    def _read_state(self) -> bytes | None:
        try:
            return self.state_path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError:
            raise ConfigError("无法读取教务会话文件") from None

    def restore(self, jar: CookieJar) -> None:
        if self._state is None:
            return
        try:
            state = json.loads(self._state)
            if state.get("base_url", self.base_url) != self.base_url:
                return
            if state.get("version") not in {1, 2} or not isinstance(state.get("cookies"), list):
                raise ValueError
            restored = []
            for item in state["cookies"]:
                item = dict(item)
                item["rest"] = item.pop("_rest", {})
                entry = Cookie(**item)
                if not accepted(
                    entry.domain, entry.secure, entry.domain_specified, self.base_url
                ) or not entry.path.startswith("/"):
                    raise ValueError
                if not entry.is_expired():
                    restored.append(entry)
            jar.clear()
            for entry in restored:
                jar.set_cookie(entry)
        except (ValueError, TypeError, KeyError, AttributeError):
            raise ConfigError("教务会话缓存损坏，请移除缓存后重新验证凭据") from None

    def save(self, jar: CookieJar) -> None:
        entries = [
            entry.__dict__
            for entry in jar
            if accepted(entry.domain, entry.secure, entry.domain_specified, self.base_url)
            and not entry.is_expired()
        ]
        state = json.dumps(
            {"version": 2, "base_url": self.base_url, "cookies": entries}, ensure_ascii=True
        ).encode()
        temporary = None
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            with _locked(self.state_path):
                if self._read_state() != self._state:
                    raise ConfigError("教务会话已被其他进程更新，未覆盖较新的凭据")
                if state == self._state:
                    return
                with tempfile.NamedTemporaryFile(
                    mode="wb", dir=self.state_path.parent, prefix=".jwts-state-", delete=False
                ) as stream:
                    temporary = Path(stream.name)
                    stream.write(state)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chmod(temporary, 0o600)
                os.replace(temporary, self.state_path)
                self._state = state
        except OSError:
            raise ConfigError("无法持久化教务会话") from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
