#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""项目服务统一入口。

常用命令：
    python -m scripts.serve                  # 一键启动全部 HTTP 服务
    python -m scripts.serve status           # 查看服务与定时任务状态
    python -m scripts.serve start journal    # 启动选股日记所在的共享 Web 服务
    python -m scripts.serve restart web --replace-conflicts
    python -m scripts.serve restart web --no-lan   # 仅本机
    python -m scripts.serve stop all

minute、grid、trainer、journal、news、watchlist 是业务入口，共用一个 Web 进程；reports 是为了兼容
旧索引地址而保留的纯静态服务。每日数据更新是计划任务，不是常驻服务。
"""

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PROJECT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_DIR / "output"
RUNTIME_DIR = PROJECT_DIR / "cache" / "services"
LOG_DIR = OUTPUT_DIR / "logs"

WEB_PORT = 8765
REPORT_PORT = 8000
WEB_HEALTH_URL = "http://127.0.0.1:{}/api/health".format(WEB_PORT)
REPORT_HEALTH_URL = "http://127.0.0.1:{}/index.html".format(REPORT_PORT)

PAGE_PATHS = {
    "minute": "/minute_view.html",
    "grid": "/grid_simulator.html",
    "trainer": "/trading_trainer.html",
    "journal": "/stock_journal.html",
    "news": "/market_news.html",
    "watchlist": "/watchlist.html",
    "symbol": "/symbol.html",
    "web": "/index.html",
}
TARGET_ALIASES = {
    "minute": "web",
    "grid": "web",
    "trainer": "web",
    "journal": "web",
    "news": "web",
    "watchlist": "web",
    "symbol": "web",
    "interactive": "web",
    "static": "reports",
    "report": "reports",
    "reports": "reports",
    "web": "web",
}


def _http_json(url, timeout=1.5):
    request = Request(url, headers={"Cache-Control": "no-cache"})
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None, None


def _http_ok(url, timeout=1.5):
    request = Request(url, headers={"Cache-Control": "no-cache"})
    try:
        with urlopen(request, timeout=timeout) as response:
            return 200 <= response.status < 400
    except (HTTPError, URLError, TimeoutError, OSError):
        return False


def web_is_healthy():
    status, payload = _http_json(WEB_HEALTH_URL)
    return bool(
        status == 200
        and payload
        and payload.get("status") == "ok"
        and payload.get("service") == "stock-interactive-web"
        and set(payload.get("features", [])) >= {
            "minute", "grid", "trainer", "journal", "news", "market_context",
            "training_loop", "watchlist", "symbol_context", "hypotheses",
        }
    )


def report_is_healthy():
    return _http_ok(REPORT_HEALTH_URL)


def _pid_file(service):
    return RUNTIME_DIR / (service + ".json")


def _read_record(service):
    try:
        return json.loads(_pid_file(service).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None


def _write_record(service, pid, command):
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "service": service,
        "pid": pid,
        "command": command,
        "started_at": datetime.now().isoformat(timespec="seconds"),
    }
    _pid_file(service).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _remove_record(service):
    try:
        _pid_file(service).unlink()
    except FileNotFoundError:
        pass


def _process_alive(pid):
    if not isinstance(pid, int) or pid <= 1:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def _process_command(pid):
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "command="],
        capture_output=True, text=True, check=False,
    )
    return result.stdout.strip()


def _process_cwd(pid):
    result = subprocess.run(
        ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
        capture_output=True, text=True, check=False,
    )
    for line in result.stdout.splitlines():
        if line.startswith("n"):
            return line[1:]
    return ""


def _listening_pids(port):
    result = subprocess.run(
        ["lsof", "-nP", "-iTCP:{}".format(port), "-sTCP:LISTEN", "-t"],
        capture_output=True, text=True, check=False,
    )
    pids = []
    for line in result.stdout.splitlines():
        try:
            pid = int(line.strip())
        except ValueError:
            continue
        if pid not in pids:
            pids.append(pid)
    return pids


def _is_safe_project_listener(pid, service):
    """只允许替换本项目已知服务，绝不终止占用相同端口的无关程序。"""
    command = _process_command(pid)
    cwd = _process_cwd(pid)
    in_project = cwd == str(PROJECT_DIR) or cwd.startswith(str(PROJECT_DIR) + os.sep)
    if not in_project:
        return False
    if service == "web":
        return (
            "scripts.services.minute_viewer" in command
            or "scripts/serve.py" in command
            or "scripts.serve" in command
        )
    return ("http.server" in command or "scripts.services.static_reports" in command) and str(REPORT_PORT) in command


def _describe_listeners(port):
    rows = []
    for pid in _listening_pids(port):
        command = _process_command(pid) or "未知命令"
        rows.append("PID {} ({})".format(pid, command))
    return rows


def _terminate_pid(pid, timeout=5.0):
    if not _process_alive(pid):
        return
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _process_alive(pid):
            return
        time.sleep(0.1)
    os.kill(pid, signal.SIGKILL)


def _clear_safe_conflicts(service, port):
    listeners = _listening_pids(port)
    unsafe = [pid for pid in listeners if not _is_safe_project_listener(pid, service)]
    if unsafe:
        details = "; ".join(_describe_listeners(port))
        raise RuntimeError("端口 {} 含非本项目进程，拒绝替换：{}".format(port, details))
    for pid in listeners:
        print("  停止旧服务 PID {}…".format(pid))
        _terminate_pid(pid)
    _remove_record(service)


def _spawn(service, command, log_name):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / log_name
    log_handle = log_path.open("a", encoding="utf-8")
    try:
        process = subprocess.Popen(
            command,
            cwd=str(PROJECT_DIR),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    finally:
        log_handle.close()
    _write_record(service, process.pid, command)
    return process.pid, log_path


def _wait_until(check, pid, timeout=30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        if not _process_alive(pid):
            return False
        time.sleep(0.25)
    return False


def _selected_web_url(requested_target):
    path = PAGE_PATHS.get(requested_target, PAGE_PATHS["web"])
    return "http://127.0.0.1:{}{}".format(WEB_PORT, path)


def start_web(requested_target="web", replace_conflicts=False, lan=True):
    if web_is_healthy():
        _, health = _http_json(WEB_HEALTH_URL)
        host = (health or {}).get('listen_host', '127.0.0.1')
        if (host == '0.0.0.0') != lan:
            print('监听模式不同，请运行 python -m scripts.serve restart web' + ('' if lan else ' --no-lan'))
            return False
        print("✓ 交互 Web 已运行：{}".format(_selected_web_url(requested_target)))
        return True

    listeners = _listening_pids(WEB_PORT)
    if listeners:
        if not replace_conflicts:
            print("✗ 端口 {} 被旧服务或其他程序占用：".format(WEB_PORT))
            for row in _describe_listeners(WEB_PORT):
                print("  - " + row)
            print("  确认后运行：python -m scripts.serve restart web --replace-conflicts")
            return False
        try:
            _clear_safe_conflicts("web", WEB_PORT)
        except RuntimeError as exc:
            print("✗ {}".format(exc))
            return False

    command = [
        sys.executable, "-u", "-m", "scripts.services.minute_viewer", "--serve",
        "--host", "0.0.0.0" if lan else "127.0.0.1", "--port", str(WEB_PORT),
    ]
    pid, log_path = _spawn("web", command, "interactive_web.log")
    if not _wait_until(web_is_healthy, pid):
        print("✗ 交互 Web 启动失败，查看日志：{}".format(log_path))
        _remove_record("web")
        return False
    print("✓ 交互 Web 已启动（PID {}）".format(pid))
    if lan:
        addresses = set()
        try:
            for row in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                if not row[4][0].startswith('127.'):
                    addresses.add(row[4][0])
        except OSError:
            pass
        try:
            result = subprocess.run(['/sbin/ifconfig'], capture_output=True, text=True, check=False)
            import re
            addresses.update(a for a in re.findall(r'inet (\d+\.\d+\.\d+\.\d+)', result.stdout) if not a.startswith('127.'))
        except OSError:
            pass
        print('局域网模式 · 同网设备可直接访问和操作')
        for address in sorted(addresses):
            print('  手机入口：http://{}:{}/daily_ops.html'.format(address, WEB_PORT))
    print("  分时：  http://127.0.0.1:{}/minute_view.html".format(WEB_PORT))
    print("  网格：  http://127.0.0.1:{}/grid_simulator.html".format(WEB_PORT))
    print("  训练：  http://127.0.0.1:{}/trading_trainer.html".format(WEB_PORT))
    print("  日记：  http://127.0.0.1:{}/stock_journal.html".format(WEB_PORT))
    print("  资讯：  http://127.0.0.1:{}/market_news.html".format(WEB_PORT))
    print("  观察池：http://127.0.0.1:{}/watchlist.html".format(WEB_PORT))
    print("  日志：  {}".format(log_path))
    return True


def start_reports(replace_conflicts=False):
    if report_is_healthy():
        record = _read_record("reports")
        owner = "本入口管理" if record and _process_alive(record.get("pid")) else "外部/launchd 管理"
        print("✓ 静态报告已运行（{}）：http://127.0.0.1:{}/index.html".format(owner, REPORT_PORT))
        return True

    listeners = _listening_pids(REPORT_PORT)
    if listeners:
        if not replace_conflicts:
            print("✗ 端口 {} 已被占用，但静态报告不可用".format(REPORT_PORT))
            return False
        try:
            _clear_safe_conflicts("reports", REPORT_PORT)
        except RuntimeError as exc:
            print("✗ {}".format(exc))
            return False

    command = [
        sys.executable, "-u", "-m", "scripts.services.static_reports", "--port", str(REPORT_PORT),
    ]
    pid, log_path = _spawn("reports", command, "static_reports.log")
    if not _wait_until(report_is_healthy, pid, timeout=10.0):
        print("✗ 静态报告启动失败，查看日志：{}".format(log_path))
        _remove_record("reports")
        return False
    print("✓ 静态报告已启动（PID {}）：http://127.0.0.1:{}/index.html".format(pid, REPORT_PORT))
    return True


def _stop_owned(service, health_check):
    record = _read_record(service)
    pid = record.get("pid") if record else None
    if pid and _process_alive(pid):
        print("  停止 {}（PID {}）…".format(service, pid))
        _terminate_pid(pid)
        _remove_record(service)
        return True
    _remove_record(service)
    if health_check():
        print("! {} 正在运行，但由 launchd/其他终端管理，本命令未停止它".format(service))
    else:
        print("- {} 未运行".format(service))
    return True


def stop_target(service, replace_conflicts=False):
    if service == "web":
        if replace_conflicts and _listening_pids(WEB_PORT):
            try:
                _clear_safe_conflicts("web", WEB_PORT)
                print("✓ web 已停止")
                return True
            except RuntimeError as exc:
                print("✗ {}".format(exc))
                return False
        return _stop_owned("web", web_is_healthy)
    if replace_conflicts and _listening_pids(REPORT_PORT):
        try:
            _clear_safe_conflicts("reports", REPORT_PORT)
            print("✓ reports 已停止")
            return True
        except RuntimeError as exc:
            print("✗ {}".format(exc))
            return False
    return _stop_owned("reports", report_is_healthy)


def _launchd_job_state(label):
    """Return short state for one LaunchAgent label, or None if not loaded."""
    if sys.platform != "darwin":
        return None
    domain = "gui/{}/{}".format(os.getuid(), label)
    result = subprocess.run(
        ["launchctl", "print", domain], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        return None
    return "运行中" if "state = running" in result.stdout else "已调度"


def _launchd_update_status():
    if sys.platform != "darwin":
        return "非 macOS：请检查系统调度器"
    weekday = _launchd_job_state("com.stock.cache-update")
    saturday = _launchd_job_state("com.stock.market-context-saturday")
    parts = []
    if weekday:
        parts.append("{}（工作日 18:30 全量）".format(weekday))
    else:
        parts.append("工作日全量未加载")
    if saturday:
        parts.append("{}（周六 07:00 market_context）".format(saturday))
    else:
        parts.append("周六轻量未加载")
    return "；".join(parts)


def show_status():
    web_listeners = _listening_pids(WEB_PORT)
    report_listeners = _listening_pids(REPORT_PORT)
    if web_is_healthy():
        _, health = _http_json(WEB_HEALTH_URL)
        web_status = "运行中 · " + ("局域网" if (health or {}).get("listen_host") == "0.0.0.0" else "仅本机")
    elif web_listeners:
        web_status = "异常：端口被旧/错误服务占用"
    else:
        web_status = "未运行"
    if report_is_healthy():
        report_record = _read_record("reports")
        managed = report_record and _process_alive(report_record.get("pid"))
        report_status = "运行中（{}）".format("本入口管理" if managed else "launchd/外部管理")
    elif report_listeners:
        report_status = "异常：端口被占用"
    else:
        report_status = "未运行"

    print("项目运行单元：2 个 HTTP 服务 + 2 个计划任务")
    print("- 交互 Web  {:<30} http://127.0.0.1:{}/".format(web_status, WEB_PORT))
    print("  └─ 分时 / 网格 / T+1训练 / 选股日记 / 市场资讯，共用一个进程")
    print("- 静态报告  {:<30} http://127.0.0.1:{}/index.html".format(report_status, REPORT_PORT))
    print("- 计划更新  {}（不计入 start all）".format(_launchd_update_status()))
    if web_listeners and not web_is_healthy():
        print("\n8765 监听详情：")
        for row in _describe_listeners(WEB_PORT):
            print("  - " + row)


def resolve_targets(target):
    if target == "all":
        return ["web", "reports"]
    resolved = TARGET_ALIASES.get(target)
    if not resolved:
        raise ValueError("未知服务目标：{}".format(target))
    return [resolved]


def build_parser():
    parser = argparse.ArgumentParser(
        description="统一管理分时、网格、T+1训练、选股日记、市场资讯、观察池和静态报告服务"
    )
    parser.add_argument(
        "action", nargs="?", default="start",
        choices=["start", "stop", "restart", "status"],
        help="默认 start",
    )
    parser.add_argument(
        "target", nargs="?", default="all",
        choices=["all", "web", "interactive", "minute", "grid", "trainer", "journal", "news", "watchlist", "reports", "report", "static"],
        help="默认 all；minute/grid/trainer/journal/news/watchlist 共用 web 进程",
    )
    parser.add_argument(
        "--replace-conflicts", action="store_true",
        help="仅替换占用目标端口的本项目旧服务，不会终止无关进程",
    )
    parser.add_argument(
        "--lan",
        dest="lan",
        action="store_true",
        default=True,
        help="交互服务默认允许可信局域网/Tailscale 访问（默认开启）",
    )
    parser.add_argument(
        "--no-lan",
        dest="lan",
        action="store_false",
        help="仅本机 127.0.0.1 监听，禁止局域网与 Tailscale 网卡访问",
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.action == "status":
        show_status()
        return 0

    targets = resolve_targets(args.target)
    ok = True
    if args.action in {"stop", "restart"}:
        for service in reversed(targets):
            ok = stop_target(service, args.replace_conflicts) and ok
        if args.action == "stop":
            return 0 if ok else 1

    for service in targets:
        if service == "web":
            started = start_web(args.target, args.replace_conflicts, args.lan)
        else:
            started = start_reports(args.replace_conflicts)
        ok = started and ok
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
