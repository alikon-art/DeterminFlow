"""圆桌会议历史保存：干预消息即时落盘与历史列表排序"""
import asyncio
import json

from src.roundtable.models import Intervention, RoundtableSession, Seat
from src.roundtable.runner import RoundtableManager, RoundtableRunner
from src.web.event_bus import event_bus


def _session() -> RoundtableSession:
    return RoundtableSession(
        topic="历史保存测试",
        seats=[
            Seat(seat_id="seat-1", role_name="研究员", system_prompt="分析"),
            Seat(seat_id="seat-2", role_name="评审", system_prompt="评审"),
        ],
    )


def test_inject_intervention_is_persisted_immediately(monkeypatch, tmp_path) -> None:
    """用户插话写入 transcript 后应立即落盘，进程崩溃也不丢失"""
    monkeypatch.setattr("src.roundtable.models.SESSIONS_DIR", tmp_path)
    emitted: list[dict] = []

    async def capture(event: dict) -> None:
        emitted.append(event)

    monkeypatch.setattr(event_bus, "emit_chat", capture)

    session = _session()
    session.status = "discussing"
    session.save()

    async def scenario() -> bool:
        await session.intervention_queue.put(
            Intervention(intervention_type="inject", content="请记住这条插话")
        )
        return await RoundtableRunner()._process_interventions(session)

    assert asyncio.run(scenario()) is False

    # 直接从磁盘验证，不依赖内存状态
    saved = json.loads(
        (tmp_path / f"{session.session_id}.json").read_text(encoding="utf-8")
    )
    contents = [t["content"] for t in saved["transcript"]]
    assert "请记住这条插话" in contents

    # 回归：重新加载后插话仍在
    restored = RoundtableSession.load(session.session_id)
    assert restored is not None
    assert any(t.content == "请记住这条插话" for t in restored.transcript)


def test_nominate_with_content_is_persisted_immediately(monkeypatch, tmp_path) -> None:
    """点名附带的用户消息写入 transcript 后应立即落盘"""
    monkeypatch.setattr("src.roundtable.models.SESSIONS_DIR", tmp_path)

    async def capture(event: dict) -> None:
        pass

    monkeypatch.setattr(event_bus, "emit_chat", capture)

    # 避免真实 LLM 调用，只验证用户消息部分的落盘
    async def fake_do_speak(self, seat, session, controller) -> None:
        return None

    monkeypatch.setattr(RoundtableRunner, "_do_speak", fake_do_speak)

    session = _session()
    session.status = "discussing"
    session.save()

    async def scenario() -> bool:
        await session.intervention_queue.put(
            Intervention(
                intervention_type="nominate",
                content="谈谈你的看法",
                target_seat_id="seat-2",
            )
        )
        return await RoundtableRunner()._process_interventions(session)

    assert asyncio.run(scenario()) is False

    saved = json.loads(
        (tmp_path / f"{session.session_id}.json").read_text(encoding="utf-8")
    )
    contents = [t["content"] for t in saved["transcript"]]
    assert "@评审 谈谈你的看法" in contents


def test_list_all_returns_sessions_sorted_by_created_at_desc() -> None:
    """历史会议列表按创建时间倒序，最新在前"""
    manager = RoundtableManager()
    older = _session()
    older.created_at = "2026-08-01T00:00:00+00:00"
    newer = _session()
    newer.created_at = "2026-08-02T00:00:00+00:00"
    middle = _session()
    middle.created_at = "2026-08-01T12:00:00+00:00"
    # 故意乱序插入，模拟内存字典顺序
    manager.sessions = {s.session_id: s for s in (older, newer, middle)}

    ordered = manager.list_all()
    assert [s["created_at"] for s in ordered] == [
        "2026-08-02T00:00:00+00:00",
        "2026-08-01T12:00:00+00:00",
        "2026-08-01T00:00:00+00:00",
    ]
