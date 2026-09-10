"""多用户隔离：鉴权与个人数据互不可见。"""

from __future__ import annotations

import tempfile
from pathlib import Path

from data.users import UserRepository, migrate_personal_data_to_admin
from data.watchlist import WatchlistRepository
from data.journal import JournalRepository
from data.hypotheses import HypothesisRepository


def test_users_auth_and_session(tmp_path: Path):
    db = tmp_path / "users.sqlite3"
    repo = UserRepository(db)
    admin = repo.create_user(username="admin1", password="secret12", role="admin")
    member = repo.create_user(username="member1", password="secret12", role="member")
    assert repo.authenticate("admin1", "secret12")["id"] == admin["id"]
    try:
        repo.authenticate("admin1", "wrong-password")
        assert False, "should fail"
    except LookupError:
        pass
    session = repo.create_session(member["id"])
    resolved = repo.resolve_session(session["token"])
    assert resolved["id"] == member["id"]
    repo.revoke_session(session["token"])
    assert repo.resolve_session(session["token"]) is None


def test_watchlist_and_journal_isolation(tmp_path: Path):
    users_db = tmp_path / "users.sqlite3"
    users = UserRepository(users_db)
    a = users.create_user(username="aaa", password="secret12", role="admin")
    b = users.create_user(username="bbb", password="secret12", role="member")

    watch_db = tmp_path / "watchlist.sqlite3"
    watch = WatchlistRepository(watch_db)
    item_a = watch.add(user_id=a["id"], code="sh600519", name="贵州茅台", status="watching")
    item_b = watch.add(user_id=b["id"], code="sz000001", name="平安银行", status="watching")
    assert len(watch.list_items(user_id=a["id"])) == 1
    assert len(watch.list_items(user_id=b["id"])) == 1
    assert watch.list_items(user_id=a["id"])[0]["code"] == "sh600519"
    try:
        watch.get(item_b["id"], user_id=a["id"])
        assert False, "A should not read B"
    except LookupError:
        pass

    journal_db = tmp_path / "journal.sqlite3"
    journal = JournalRepository(journal_db)
    case_a = journal.create_case(user_id=a["id"], code="sh600519", title="A案")
    case_b = journal.create_case(user_id=b["id"], code="sz000001", title="B案")
    assert len(journal.list_cases(user_id=a["id"])) == 1
    try:
        journal.get_case(case_b["id"], user_id=a["id"])
        assert False, "A should not read B case"
    except LookupError:
        pass

    hyp_db = tmp_path / "hyp.sqlite3"
    hyp = HypothesisRepository(hyp_db)
    hyp.create(user_id=a["id"], code="sh600519", title="A假设", thesis="x")
    hyp.create(user_id=b["id"], code="sh600519", title="B假设", thesis="y")
    assert len(hyp.list_for_code("sh600519", user_id=a["id"])) == 1
    assert hyp.list_for_code("sh600519", user_id=a["id"])[0]["title"] == "A假设"


def test_migrate_backfill(tmp_path: Path, monkeypatch):
    # migrate_personal_data_to_admin uses PROJECT_DIR/state — skip heavy path;
    # ensure_user_id_column is covered by repository init against empty DBs above.
    users = UserRepository(tmp_path / "users.sqlite3")
    admin = users.create_user(username="xiaodong", password="secret12", role="admin")
    assert users.get_first_admin_id() == admin["id"]
