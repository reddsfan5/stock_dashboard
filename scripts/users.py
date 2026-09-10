#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地用户管理 CLI。

示例：
  python -m scripts.users list
  python -m scripts.users create --username alice --password 'secret12'
  python -m scripts.users create --username bob --password 'secret12' --admin
  python -m scripts.users migrate
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from data.users import UserRepository, bootstrap_admin, migrate_personal_data_to_admin


def cmd_list(_args):
    repo = UserRepository()
    users = repo.list_users()
    if not users:
        print("（尚无用户；启动 Web 或运行 migrate 会创建管理员 xiaodong）")
        return 0
    for u in users:
        flag = "admin" if u["role"] == "admin" else "member"
        en = "启用" if u["enabled"] else "禁用"
        print(f"{u['id']:>3}  {u['username']:<16}  {u['display_name']:<12}  {flag:<6}  {en}  {u['created_at']}")
    return 0


def cmd_create(args):
    password = args.password
    if not password:
        password = getpass.getpass("密码: ")
        confirm = getpass.getpass("确认密码: ")
        if password != confirm:
            print("两次密码不一致", file=sys.stderr)
            return 1
    repo = UserRepository()
    user = repo.create_user(
        username=args.username,
        password=password,
        display_name=args.display_name or args.username,
        role="admin" if args.admin else "member",
    )
    print(f"✓ 已创建用户 {user['username']} (id={user['id']}, role={user['role']})")
    return 0


def cmd_migrate(_args):
    boot = bootstrap_admin()
    summary = migrate_personal_data_to_admin(boot["user"]["id"])
    print(f"✓ 管理员 id={summary['admin_user_id']}")
    for name in summary["tables"]:
        print(f"  - {name}")
    if not summary["tables"]:
        print("  （无待迁移表，或 state 库尚未创建）")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="本地多用户管理")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="列出用户")
    p_list.set_defaults(func=cmd_list)

    p_create = sub.add_parser("create", help="创建用户（无公开注册）")
    p_create.add_argument("--username", required=True)
    p_create.add_argument("--password", default="")
    p_create.add_argument("--display-name", default="")
    p_create.add_argument("--admin", action="store_true")
    p_create.set_defaults(func=cmd_create)

    p_mig = sub.add_parser("migrate", help="引导管理员并把历史个人数据归到该用户")
    p_mig.set_defaults(func=cmd_migrate)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
