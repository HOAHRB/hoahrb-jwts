"""一次性重新登录教务系统；不启动机器人、不修改持久开关。"""

import argparse
import os
import sys
from pathlib import Path

from hoahrb_jwts.browser_login import AUTHENTICATION_LOCK, browser_login
from hoahrb_jwts.config import Settings
from hoahrb_jwts.diagnostics import configure_auth_logging, logger
from hoahrb_jwts.errors import AuthenticationError, ConfigError, TransportError


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="打开浏览器重新登录教务系统，验证成功后保存两站 Cookie。",
        epilog="默认复用已有会话，可能自动完成登录；失败保留原凭据。完成后照常使用 jwts 抓取命令。",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="忽略保存的教务和 CAS Cookie，从空会话登录；成功后替换原凭据",
    )
    parser.add_argument(
        "--env-file", type=Path, default=Path(".env"), help="非敏感配置文件路径（默认：.env）"
    )
    args = parser.parse_args(argv)
    try:
        configure_auth_logging()
    except OSError:
        print("警告：无法创建认证诊断日志", file=sys.stderr)
    try:
        with AUTHENTICATION_LOCK:
            browser_login(Settings.from_sources(os.environ, args.env_file), fresh=args.fresh)
    except (AuthenticationError, ConfigError, TransportError) as exc:
        logger.warning("relogin_failed type=%s", type(exc).__name__)
        print(f"重新登录失败：{exc}", file=sys.stderr)
        return 1
    print("教务登录成功，已保存会话。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
