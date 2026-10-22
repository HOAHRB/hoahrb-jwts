import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from hoahrb_jwts.auth_session import TeachingSession
from hoahrb_jwts.browser_login import export_browser_cookies
from hoahrb_jwts.config import Settings
from hoahrb_jwts.errors import ConfigError

PLAN = "执行教学计划查询<select name=pageNj></select><select name=pageYxdm></select>"


class StoreTests(unittest.TestCase):
    def test_scoped_two_site_session_restart_and_no_dotenv_required(self):
        with tempfile.TemporaryDirectory() as directory:
            env = Path(directory) / ".env.jwts"
            settings = Settings.from_sources({}, env)
            with TeachingSession(settings) as session:
                session._seed_cookie("SID=teaching", "jwts.hit.edu.cn", secure=False)
                session._seed_cookie("TGC=cas", "ids.hit.edu.cn", secure=True)
                session._verified = True
                session.persist_verified_cookie()
            self.assertFalse(env.exists())
            with TeachingSession(Settings.from_sources({}, env)) as session:
                cookies = export_browser_cookies(session)
                self.assertEqual({item["name"] for item in cookies}, {"SID", "TGC"})
                self.assertNotIn("TGC=", session._prepare("GET", "/").headers["cookie"])
                self.assertNotIn(
                    "cookie", session._prepare("GET", "http://ids.hit.edu.cn/").headers
                )

    def test_concurrent_write_rejected_and_base_url_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.json"
            settings = replace(Settings.from_env({}), cookie_file=path)
            with TeachingSession(settings) as first, TeachingSession(settings) as second:
                first._seed_cookie("SID=new", "jwts.hit.edu.cn", secure=False)
                first._verified = second._verified = True
                first.persist_verified_cookie()
                with self.assertRaises(ConfigError):
                    second.persist_verified_cookie()
            with TeachingSession(
                replace(Settings.from_env({}), cookie_file=path, base_url="https://other.example")
            ) as other:
                self.assertEqual(list(other._client.cookies), [])

    def test_existing_json_keeps_both_sessions_without_dotenv_fingerprint(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.json"
            with TeachingSession(replace(Settings.from_env({}), cookie_file=None)) as session:
                session._seed_cookie("SID=old", "jwts.hit.edu.cn", secure=False)
                path.write_text(
                    json.dumps(
                        {
                            "version": 1,
                            "fingerprint": "old",
                            "cookies": [c.__dict__ for c in session._client.cookies],
                        }
                    )
                )
            with TeachingSession(replace(Settings.from_env({}), cookie_file=path)) as session:
                self.assertEqual(session._prepare("GET", "/").headers["cookie"], "SID=old")
                session._verified = True
                session.persist_verified_cookie()
            self.assertEqual(json.loads(path.read_text())["version"], 2)
