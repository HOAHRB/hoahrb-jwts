from dataclasses import replace

import responses

from hoahrb_jwts.auth_session import TeachingSession
from hoahrb_jwts.browser_login import export_browser_cookies, import_browser_cookies
from hoahrb_jwts.config import Settings


@responses.activate
def test_parent_cookie_round_trip_and_cas_send(tmp_path):
    settings = replace(Settings.from_env({}), cookie_file=tmp_path / "session.json")
    with TeachingSession(settings) as session:
        import_browser_cookies(
            session,
            [
                {
                    "name": "__access_token",
                    "value": "parent-secret",
                    "domain": ".hit.edu.cn",
                    "path": "/",
                    "secure": True,
                    "httpOnly": True,
                },
                {"name": "bad", "value": "bad", "domain": ".edu.cn", "path": "/", "secure": True},
            ],
        )
        session._verified = True
        session.persist_verified_cookie()
    urls = [
        "https://ids.hit.edu.cn/authserver/login",
        "http://jwts.hit.edu.cn/loginCAS",
        "https://other.hit.edu.cn/",
    ]
    for url in urls:
        responses.get(url, status=200)
    with TeachingSession(settings) as session:
        exported = export_browser_cookies(session)
        assert len(exported) == 1
        assert exported[0]["domain"] == ".hit.edu.cn"
        assert exported[0]["httpOnly"] is True
        for url in urls:
            session._send("GET", url)
    assert [c.request.headers.get("Cookie", "") for c in responses.calls] == [
        "__access_token=parent-secret",
        "",
        "",
    ]
