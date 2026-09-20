"""Sync Tonghuashun (同花顺) per-stock 题材/概念 tags into classifications DB.

Pulls each stock's F10 concept page (`basic.10jqka.com.cn/{code}/concept.html`)
because board detail pagination is capped (~5 pages / ~50 names) without login,
and the ajax constituent API returns 403 from many egress IPs.

Does not modify shenwan or eastmoney memberships.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterable

import py_mini_racer
import requests
from bs4 import BeautifulSoup

from akshare.datasets import get_ths_js
from data.classifications import ClassificationRepository, normalize

SOURCE = "ths"
KIND = "同花顺题材"
EVIDENCE = "同花顺F10概念题材"
F10 = "https://basic.10jqka.com.cn/{code}/concept.html"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/89.0.4389.90 Safari/537.36"
)


def _v_cookie() -> str:
    ctx = py_mini_racer.MiniRacer()
    ctx.eval(open(get_ths_js("ths.js"), encoding="utf-8").read())
    return str(ctx.call("v"))


def _session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    v = _v_cookie()
    session.headers.update(
        {
            "User-Agent": UA,
            "Cookie": f"v={v}",
            "hexin-v": v,
            "Referer": "https://basic.10jqka.com.cn/",
        }
    )
    return session


def _refresh_cookie(session: requests.Session) -> None:
    v = _v_cookie()
    session.headers["Cookie"] = f"v={v}"
    session.headers["hexin-v"] = v


def parse_f10_concepts(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    out: list[dict] = []
    seen: set[str] = set()
    for el in soup.find_all(attrs={"cid": True}):
        board_id = str(el.get("cid") or "").strip()
        if not board_id or board_id in seen:
            continue
        name = None
        tr = el.find_parent("tr")
        if tr is not None:
            tds = tr.find_all("td")
            if len(tds) >= 2 and tds[0].get_text(strip=True).isdigit():
                name = tds[1].get_text(strip=True)
        tag = (el.get("tag") or "").strip()
        if not name and tag:
            name = tag.split("-")[-1].strip()
        if not name:
            name = el.get_text(strip=True)
        name = (name or "").strip()
        if not name or len(name) > 40:
            continue
        seen.add(board_id)
        out.append(dict(id=board_id, name=name, kind=KIND, evidence=EVIDENCE))
    return out


def fetch_stock_themes(
    code: str,
    session: requests.Session | None = None,
    retries: int = 6,
) -> list[dict]:
    code = normalize(code)
    digit = code[2:]
    session = session or _session()
    url = F10.format(code=digit)
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            if attempt:
                _refresh_cookie(session)
            resp = session.get(url, timeout=(5, 20))
            if resp.status_code in (403, 429, 503):
                # Back off hard on rate limits; refresh cookie each time.
                time.sleep(min(30.0, 1.5 * (2**attempt)))
                _refresh_cookie(session)
                last_err = requests.HTTPError(f"{resp.status_code} for {url}")
                continue
            resp.raise_for_status()
            resp.encoding = "gbk"
            rows = parse_f10_concepts(resp.text)
            if not rows and "概念" not in resp.text:
                raise ValueError("F10 页面未包含概念题材区块")
            return rows
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(min(20.0, 0.8 * (2**attempt)))
    raise RuntimeError(f"{code} F10 题材拉取失败: {last_err}")


def sync_one(code: str, repo: ClassificationRepository | None = None, session=None) -> dict:
    repo = repo or ClassificationRepository()
    code = normalize(code)
    url = F10.format(code=code[2:])
    try:
        rows = fetch_stock_themes(code, session=session)
        if not rows:
            repo.status(code, SOURCE, "ok", "F10 无题材标签")
            return dict(code=code, themes=0, status="empty")
        repo.memberships(code, SOURCE, rows, url)
        return dict(code=code, themes=len(rows), status="ok")
    except Exception as exc:  # noqa: BLE001
        repo.status(code, SOURCE, "error", f"源站暂不可用；已保留缓存（{type(exc).__name__}）")
        return dict(code=code, themes=0, status="error", error=str(exc))


def list_universe(repo: ClassificationRepository | None = None) -> list[str]:
    repo = repo or ClassificationRepository()
    with repo.connect() as db:
        return [r[0] for r in db.execute("SELECT code FROM security ORDER BY code")]


def list_error_codes(repo: ClassificationRepository | None = None) -> list[str]:
    repo = repo or ClassificationRepository()
    with repo.connect() as db:
        return [
            r[0]
            for r in db.execute(
                "SELECT code FROM sync_state WHERE dataset=? AND status='error' ORDER BY code",
                (SOURCE,),
            )
        ]


def list_missing_codes(repo: ClassificationRepository | None = None) -> list[str]:
    """Universe codes with no successful ths sync yet."""
    repo = repo or ClassificationRepository()
    with repo.connect() as db:
        return [
            r[0]
            for r in db.execute(
                """
                SELECT s.code FROM security s
                LEFT JOIN sync_state t
                  ON t.code=s.code AND t.dataset=? AND t.status='ok'
                WHERE t.code IS NULL
                ORDER BY s.code
                """,
                (SOURCE,),
            )
        ]


def sync_ths_themes(
    codes: Iterable[str] | None = None,
    *,
    limit: int | None = None,
    sleep: float = 0.15,
    workers: int = 2,
    repo: ClassificationRepository | None = None,
    progress=None,
) -> dict:
    repo = repo or ClassificationRepository()
    universe = list(codes) if codes is not None else list_universe(repo)
    if limit is not None:
        universe = universe[:limit]
    ok = err = empty = 0
    theme_hits = 0

    def work(code: str):
        # One session per task so cookie refresh stays local.
        session = _session()
        result = sync_one(code, repo=repo, session=session)
        if sleep:
            time.sleep(sleep)
        return result

    if workers <= 1:
        for i, code in enumerate(universe, 1):
            result = work(code)
            theme_hits += result.get("themes", 0)
            if result["status"] == "ok":
                ok += 1
            elif result["status"] == "empty":
                empty += 1
            else:
                err += 1
            if progress:
                progress(i, len(universe), result)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(work, code): code for code in universe}
            for i, fut in enumerate(as_completed(futures), 1):
                result = fut.result()
                theme_hits += result.get("themes", 0)
                if result["status"] == "ok":
                    ok += 1
                elif result["status"] == "empty":
                    empty += 1
                else:
                    err += 1
                if progress:
                    progress(i, len(universe), result)

    with repo.connect() as db:
        boards = db.execute(
            "SELECT COUNT(*) FROM board WHERE source=?", (SOURCE,)
        ).fetchone()[0]
        stocks = db.execute(
            "SELECT COUNT(DISTINCT code) FROM membership WHERE source=? AND observed_to IS NULL",
            (SOURCE,),
        ).fetchone()[0]
    return dict(
        stocks=len(universe),
        ok=ok,
        empty=empty,
        error=err,
        theme_hits=theme_hits,
        boards=boards,
        mapped_stocks=stocks,
    )
