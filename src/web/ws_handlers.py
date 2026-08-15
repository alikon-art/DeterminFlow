"""
WebSocket 处理模块 - 实现对话流式推送和系统事件广播

两个 WebSocket 端点：
- /ws/chat: 对话通道，接收用户消息，委托 session.send_message() 处理，推送流式事件
- /ws/events: 事件通道，推送会话状态变更、子会话通知等系统事件

消息处理改为后台任务 + event_bus 推送，支持多 session 并发通信。
"""
import asyncio
import json
import logging
import uuid

from fastapi import WebSocket, WebSocketDisconnect

from src.web.event_bus import event_bus
from src.core.utils import is_visible_to_frontend

logger = logging.getLogger(__name__)

_MAX_MESSAGE_ATTACHMENTS = 64
_MAX_ATTACHMENT_NAME_LENGTH = 255
_MAX_ATTACHMENT_PATH_LENGTH = 4096


def _validate_message_attachments(raw_attachments, content: str) -> list[dict[str, str]]:
    """校验 UI 附件元数据；正文中的绝对路径仍是 LLM 的唯一输入。"""
    if raw_attachments is None:
        return []
    if not isinstance(raw_attachments, list):
        raise ValueError("attachments 必须是数组")
    if len(raw_attachments) > _MAX_MESSAGE_ATTACHMENTS:
        raise ValueError(f"单条消息最多包含 {_MAX_MESSAGE_ATTACHMENTS} 个文件")

    attachments: list[dict[str, str]] = []
    for raw_attachment in raw_attachments:
        if not isinstance(raw_attachment, dict):
            raise ValueError("附件信息格式无效")
        name = raw_attachment.get("name")
        path = raw_attachment.get("absolute_path")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("附件名称无效")
        if not isinstance(path, str) or not path:
            raise ValueError("附件路径无效")
        if len(name) > _MAX_ATTACHMENT_NAME_LENGTH:
            raise ValueError("附件名称过长")
        if len(path) > _MAX_ATTACHMENT_PATH_LENGTH:
            raise ValueError("附件路径过长")
        is_absolute = path.startswith("/") or (
            len(path) >= 3
            and path[0].isalpha()
            and path[1] == ":"
            and path[2] in ("/", "\\")
        )
        if not is_absolute or path not in content:
            raise ValueError("附件绝对路径必须存在于消息正文中")
        attachments.append({"name": name, "absolute_path": path})
    return attachments


# ============ 后台消息处理 ============

def _build_session_snapshot(session, *, bus=event_bus) -> dict:
    """构造一次权威会话快照，包括空历史和当前生成草稿。"""
    session_id = str(session.session_id)
    active_stream = bus.get_active_stream(session_id)
    record = session.record
    if active_stream is not None:
        baseline = active_stream.get("baseline_record_length")
        if isinstance(baseline, int) and baseline >= 0:
            record = record[:baseline]
    snapshot: dict = {
        "type": "snapshot",
        "session_id": session_id,
        "messages": [
            message for message in record
            if is_visible_to_frontend(message)
        ],
        "status": str(getattr(session, "status", "running") or "running"),
        "revision": bus.get_session_revision(session_id),
        "active_stream": active_stream,
    }
    token_usage = getattr(session, "token_usage", None)
    if token_usage:
        snapshot["token_usage"] = token_usage
    last_error = getattr(session, "last_error", None)
    if snapshot["status"] == "error" and isinstance(last_error, dict):
        snapshot["last_error"] = dict(last_error)
    return snapshot


def _build_unavailable_snapshot(session_id: str, *, bus=event_bus) -> dict:
    """显式目标不存在时返回空快照，禁止错误回退到其他主会话。"""
    if session_id:
        bus.clear_session(session_id)
    return {
        "type": "snapshot",
        "session_id": session_id,
        "messages": [],
        "status": "error" if session_id else "idle",
        "revision": 0,
        "active_stream": None,
        **({"error": f"未找到会话 {session_id}"} if session_id else {}),
    }


def _resolve_session_snapshot(session_mgr, session_id: str | None, *, bus=event_bus) -> dict:
    """按明确 session_id 解析快照；仅无目标时使用当前主会话。"""
    if session_id:
        session = session_mgr.get_session(session_id)
        if session is None:
            return _build_unavailable_snapshot(session_id, bus=bus)
        return _build_session_snapshot(session, bus=bus)

    main_session = session_mgr.get_main_session()
    if main_session is None:
        return _build_unavailable_snapshot("", bus=bus)
    return _build_session_snapshot(main_session, bus=bus)

async def _validate_session(session_mgr, session_id: str, action_label: str):
    """通用会话前置校验：存在性、graph 就绪、状态检查。

    冷加载/legacy error 会话会先重建运行依赖（ensure_graph_ready），
    使 error 会话的自动恢复入口可达；error 状态下用户主动操作视为重试，
    重置为可运行状态。

    返回 (session, None) 校验通过；返回 (None, error_dict) 校验失败。
    """
    session = session_mgr.get_session(session_id)
    if not session:
        return None, {"type": "error", "message": f"未找到会话 {session_id}", "session_id": session_id, "terminal": False}
    # 冷加载重建运行依赖（评审：编译图检查前先恢复）
    if session.compiled_graph is None:
        ensure = getattr(session_mgr, "ensure_graph_ready", None)
        ready = await ensure(session) if callable(ensure) else False
        if not ready:
            return None, {"type": "error", "message": f"会话 {session_id} 无法初始化运行依赖", "session_id": session_id, "terminal": False}
    if session.status == "error":
        # error 会话允许用户显式重试（自动恢复入口）
        session.status = "completed"
    if session.status == "streaming" or session.invocation_active:
        return None, {"type": "error", "message": f"会话 {session_id} 正在处理中，请稍后再试", "session_id": session_id, "terminal": False}
    return session, None


def _make_stream_callback(session_id: str, session):
    """构造流式事件回调：chat 通道推送 token/tool 事件，events 通道推送状态变更。"""
    async def callback(event: dict):
        event["session_id"] = session_id
        event_type = event.get("type", "")
        if event_type in ("stream_start", "stream_end", "token", "reasoning_token",
                          "tool_call_delta", "error", "tool_start", "tool_end",
                          "llm_usage"):
            await event_bus.emit_chat(event)
        if event_type == "stream_start":
            await event_bus.emit_event({
                "type": "session_update", "action": "status_changed",
                "session_id": session_id, "status": "streaming",
            })
        elif event_type == "stream_end":
            await event_bus.emit_event({
                "type": "session_update", "action": "status_changed",
                "session_id": session_id, "status": session.status,
            })
    return callback


async def _execute_with_events(session, session_id: str, action_coro, action_label: str):
    """通用执行包装：执行 action_coro → 推送 chain_end → 统一异常/清理处理。"""
    from datetime import datetime, timezone

    try:
        await action_coro
        # 错误或降级结束（递归上限/内容过滤/可恢复 Provider 错误）时，
        # 不得再推送 chain_end——保证单一、可判定的结束事件。
        if session.status == "error" or getattr(session, "_degraded", False):
            return
        serialized = [m for m in session.record if is_visible_to_frontend(m)]
        chain_end_event: dict = {
            "type": "chain_end",
            "messages": serialized,
            "session_id": session_id,
        }
        if session.token_usage:
            chain_end_event["token_usage"] = session.token_usage
        await event_bus.emit_chat(chain_end_event)
    except asyncio.CancelledError:
        session.status = "completed"
        logger.info(f"会话 {session_id} {action_label}后台任务被取消，标记为 completed")
        if not getattr(session, "termination_requested", False):
            await event_bus.emit_chat({
                "type": "error",
                "message": f"会话{action_label}已取消",
                "session_id": session_id,
                "terminal": True,
            })
    except Exception as e:
        logger.error(f"会话 {session_id} {action_label}失败: {e}", exc_info=True)
        terminal_already_emitted = session.status == "error"
        session.status = "error"
        if not terminal_already_emitted:
            error_message = session._record_terminal_error(e)
            await event_bus.emit_chat({
                "type": "error",
                "message": error_message,
                "session_id": session_id,
                "terminal": True,
                "messages": [
                    message for message in session.record
                    if is_visible_to_frontend(message)
                ],
            })
    finally:
        session.updated_at = datetime.now(timezone.utc).isoformat()
        await session.async_save()


async def _process_session_message(
    session_mgr,
    session_id: str,
    content: str,
    attachments: list[dict[str, str]] | None = None,
):
    """后台任务：处理会话消息并通过 event_bus 推送所有事件。"""
    session, err = await _validate_session(session_mgr, session_id, "发送消息")
    if err:
        await event_bus.emit_chat(err)
        return
    callback = _make_stream_callback(session_id, session)
    await _execute_with_events(
        session, session_id,
        session.send_message(
            content=content,
            event_callback=callback,
            source="human",
            attachments=attachments,
        ),
        action_label="处理消息",
    )


async def _process_edit_message(session_mgr, session_id: str, message_id: str, new_content: str):
    """后台任务：编辑消息后重新发送，通过 event_bus 推送所有事件。"""
    session, err = await _validate_session(session_mgr, session_id, "编辑消息")
    if err:
        await event_bus.emit_chat(err)
        return
    callback = _make_stream_callback(session_id, session)
    await _execute_with_events(
        session, session_id,
        session.edit_message_and_resend(
            message_id=message_id,
            new_content=new_content,
            event_callback=callback,
        ),
        action_label="编辑消息",
    )


# ============ 对话 WebSocket ============

async def handle_chat_ws(ws: WebSocket, app_state):
    """
    处理 /ws/chat WebSocket 连接

    协议：
    - 客户端发送:
      - {"type": "message", "content": "用户消息"} — 发送到默认主会话
      - {"type": "message", "content": "...", "session_id": "xxx"} — 发送到指定会话
    - 服务端推送（通过 event_bus 自动广播）:
      - {"type": "token", "content": "...", "session_id": "..."} 流式 token
      - {"type": "chain_end", "messages": [...], "session_id": "..."}
      - {"type": "stream_start" / "stream_end", "session_id": "..."}
      - {"type": "error", "message": "..."}
      - {"type": "notification", "data": {...}}
    """
    await ws.accept()
    session_mgr = app_state.session_manager

    # 提取查询参数中的 session_id（如 /ws/chat?session_id=xxx）
    target_session_id = ws.query_params.get("session_id")
    target_roundtable_id = ws.query_params.get("roundtable_id")
    roundtable_only = (
        target_roundtable_id is not None
        or ws.query_params.get("roundtable_only") == "1"
    )

    # 明确目标连接只订阅该 session；无目标连接作为全局兼容观察者。
    subscribed_session_ids: set[str] = set()
    if roundtable_only:
        await event_bus.subscribe_roundtable(target_roundtable_id or "", ws)
    elif target_session_id:
        await event_bus.subscribe_session(target_session_id, ws)
        subscribed_session_ids.add(target_session_id)
    else:
        await event_bus.subscribe("chat", ws)

    # 订阅和 snapshot 入队之间没有 I/O await，确保实时事件不会越过基线快照。
    if not roundtable_only:
        try:
            event_bus.enqueue_to_ws(
                ws,
                _resolve_session_snapshot(
                    session_mgr,
                    target_session_id,
                    bus=event_bus,
                ),
            )
        except Exception as e:
            logger.error(f"推送会话快照失败: {e}")

    # 启动后台任务：实时推送 notification 到 Chat WS（使用广播器）
    notif_queue = None if roundtable_only else session_mgr.notification_broadcaster.subscribe()

    async def _push_sub_notifications():
        if notif_queue is None:
            return
        while True:
            try:
                notif = await asyncio.wait_for(
                    notif_queue.get(), timeout=1.0
                )
                event_bus.enqueue_to_ws(ws, {
                    "type": "notification",
                    "data": notif,
                })
            except asyncio.TimeoutError:
                continue
            except (asyncio.CancelledError, WebSocketDisconnect):
                break
            except Exception:
                await asyncio.sleep(1)

    notif_push_task = asyncio.create_task(_push_sub_notifications())
    # 跟踪后台消息处理任务
    bg_tasks: set[asyncio.Task] = set()

    async def _spawn_and_track(coro):
        """创建后台任务并自动清理。"""
        task = asyncio.create_task(coro)
        bg_tasks.add(task)
        task.add_done_callback(bg_tasks.discard)

    try:
        while True:
            raw = await ws.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await _safe_send(ws, {"type": "error", "message": "无效的 JSON", "terminal": False})
                continue

            if not isinstance(data, dict):
                await _safe_send(ws, {"type": "error", "message": "消息必须是 JSON 对象", "terminal": False})
                continue

            msg_type = data.get("type", "")
            if not isinstance(msg_type, str):
                await _safe_send(ws, {"type": "error", "message": "type 必须是字符串", "terminal": False})
                continue
            if roundtable_only and msg_type in {
                "message", "edit_message", "resync", "diagnose_content_safety",
            }:
                await _safe_send(ws, {
                    "type": "error",
                    "message": "圆桌连接不能执行普通会话命令",
                    "terminal": False,
                })
                continue

            requested_session_id = data.get("session_id")
            if requested_session_id is not None and not isinstance(requested_session_id, str):
                await _safe_send(ws, {"type": "error", "message": "session_id 必须是字符串", "terminal": False})
                continue
            if (
                target_session_id
                and requested_session_id
                and requested_session_id != target_session_id
                and msg_type in {"message", "edit_message", "resync", "diagnose_content_safety"}
            ):
                await _safe_send(ws, {
                    "type": "error",
                    "message": "当前连接不能访问其他会话",
                    "session_id": target_session_id,
                    "terminal": False,
                })
                continue

            if msg_type == "message":
                raw_content = data.get("content", "")
                if not isinstance(raw_content, str):
                    await _safe_send(ws, {"type": "error", "message": "content 必须是字符串", "terminal": False})
                    continue
                content = raw_content.strip()
                if not content:
                    continue
                try:
                    attachments = _validate_message_attachments(
                        data.get("attachments"),
                        content,
                    )
                except ValueError as exc:
                    await _safe_send(ws, {
                        "type": "error",
                        "message": str(exc),
                        "terminal": False,
                    })
                    continue

                msg_session_id = requested_session_id or target_session_id

                if msg_session_id:
                    # 向指定会话发消息（后台异步，不阻塞消息循环）
                    # 同时订阅该 session 的事件（如果尚未订阅）
                    if msg_session_id not in subscribed_session_ids:
                        await event_bus.subscribe_session(msg_session_id, ws)
                        subscribed_session_ids.add(msg_session_id)
                    await _spawn_and_track(
                        _process_session_message(
                            session_mgr,
                            msg_session_id,
                            content,
                            attachments,
                        )
                    )
                else:
                    # 向默认主会话发消息（后台异步）
                    main = session_mgr.get_main_session()
                    if main:
                        await _spawn_and_track(
                            _process_session_message(
                                session_mgr,
                                main.session_id,
                                content,
                                attachments,
                            )
                        )

            elif msg_type == "edit_message":
                message_id = data.get("message_id", "")
                raw_edit_content = data.get("content", "")
                if not isinstance(message_id, str) or not isinstance(raw_edit_content, str):
                    await _safe_send(ws, {"type": "error", "message": "message_id 和 content 必须是字符串", "terminal": False})
                    continue
                edit_content = raw_edit_content.strip()
                if not message_id or not edit_content:
                    await _safe_send(ws, {"type": "error", "message": "缺少 message_id 或 content", "terminal": False})
                    continue

                target_sid = requested_session_id or target_session_id
                if target_sid:
                    await _spawn_and_track(
                        _process_edit_message(session_mgr, target_sid, message_id, edit_content)
                    )
                else:
                    main = session_mgr.get_main_session()
                    if main:
                        await _spawn_and_track(
                            _process_edit_message(session_mgr, main.session_id, message_id, edit_content)
                        )

            elif msg_type == "ping":
                await _safe_send(ws, {"type": "pong"})

            elif msg_type == "resync":
                resync_session_id = requested_session_id or target_session_id
                if resync_session_id and resync_session_id not in subscribed_session_ids:
                    await event_bus.subscribe_session(resync_session_id, ws)
                    subscribed_session_ids.add(resync_session_id)
                event_bus.enqueue_to_ws(
                    ws,
                    _resolve_session_snapshot(
                        session_mgr,
                        resync_session_id,
                        bus=event_bus,
                    ),
                )

            elif msg_type == "rt_start":
                rt_id = data.get("roundtable_id", "")
                if rt_id and hasattr(app_state, "roundtable_manager"):
                    result = await app_state.roundtable_manager.start(rt_id)
                    await _safe_send(ws, {"type": "rt_start_result", **result})

            elif msg_type == "rt_inject":
                rt_id = data.get("roundtable_id", "")
                inject_content = data.get("content", "").strip()
                if rt_id and inject_content and hasattr(app_state, "roundtable_manager"):
                    result = await app_state.roundtable_manager.inject(rt_id, inject_content)
                    await _safe_send(ws, {
                        "type": "rt_inject_result",
                        "success": result["success"],
                        "message": result.get("message", ""),
                    })
                else:
                    await _safe_send(ws, {"type": "error", "message": "缺少 roundtable_id 或 content"})

            elif msg_type == "rt_nominate":
                rt_id = data.get("roundtable_id", "")
                target_seat_id = data.get("target_seat_id")
                target_name = data.get("target_name")
                nominate_content = data.get("content", "")
                if rt_id and (target_seat_id or target_name) and hasattr(app_state, "roundtable_manager"):
                    result = await app_state.roundtable_manager.nominate(
                        rt_id, target_seat_id=target_seat_id, target_name=target_name, content=nominate_content,
                    )
                    await _safe_send(ws, {"type": "rt_nominate_result", **result})
                else:
                    await _safe_send(ws, {"type": "error", "message": "缺少 roundtable_id 或目标席位信息"})

            elif msg_type == "rt_pause":
                rt_id = data.get("roundtable_id", "")
                if rt_id and hasattr(app_state, "roundtable_manager"):
                    result = await app_state.roundtable_manager.pause(rt_id)
                    await _safe_send(ws, {"type": "rt_pause_result", **result})

            elif msg_type == "rt_resume":
                rt_id = data.get("roundtable_id", "")
                if rt_id and hasattr(app_state, "roundtable_manager"):
                    result = await app_state.roundtable_manager.resume(rt_id)
                    await _safe_send(ws, {"type": "rt_resume_result", **result})

            elif msg_type == "diagnose_content_safety":
                target_id = data.get("session_id", "")
                request_id = str(data.get("request_id") or uuid.uuid4().hex)

                async def _diagnostic_result(success: bool, message: str):
                    await _safe_send(ws, {
                        "type": "content_safety_diagnostic_result",
                        "session_id": target_id,
                        "request_id": request_id,
                        "success": success,
                        "message": message,
                    })

                if not target_id:
                    await _diagnostic_result(False, "缺少 session_id")
                    continue

                session = session_mgr.get_session(target_id)
                if not session:
                    await _diagnostic_result(False, f"未找到会话 {target_id}")
                    continue
                if session.status == "streaming" or session.invocation_active:
                    await _diagnostic_result(False, f"会话 {target_id} 正在生成，无法同时运行诊断")
                    continue

                await _safe_send(ws, {
                    "type": "content_safety_diagnostic_accepted",
                    "session_id": target_id,
                    "request_id": request_id,
                })

                # 运行诊断（后台异步，不阻塞消息循环）
                async def _run_diagnostic(sess, sid, diagnostic_request_id):
                    try:
                        result = await sess.run_content_safety_diagnostic()
                        if result.get("success"):
                            await event_bus.emit_chat(_build_session_snapshot(sess))
                            await _safe_send(ws, {
                                "type": "content_safety_diagnostic_result",
                                "session_id": sid,
                                "request_id": diagnostic_request_id,
                                "success": True,
                                "message": result.get("message", "诊断完成"),
                            })
                        else:
                            await _safe_send(ws, {
                                "type": "content_safety_diagnostic_result",
                                "request_id": diagnostic_request_id,
                                "message": result.get("message", "诊断失败"),
                                "session_id": sid,
                                "success": False,
                            })
                    except Exception as e:
                        logger.error(f"会话 {sid} 诊断异常: {e}", exc_info=True)
                        await _safe_send(ws, {
                            "type": "content_safety_diagnostic_result",
                            "request_id": diagnostic_request_id,
                            "message": f"诊断执行异常: {e}",
                            "session_id": sid,
                            "success": False,
                        })

                await _spawn_and_track(_run_diagnostic(session, target_id, request_id))

    except WebSocketDisconnect:
        logger.info("Chat WebSocket 客户端断开")
    except Exception as e:
        logger.error(f"Chat WebSocket 错误: {e}", exc_info=True)
    finally:
        notif_push_task.cancel()
        if notif_queue is not None:
            session_mgr.notification_broadcaster.unsubscribe(notif_queue)
        # 不取消 bg_tasks（_process_session_message 使用 event_bus 推送事件，
        # event_bus 已自动处理 WS 断线清理，取消会导致运行中 session 被错误标记为 error）
        await event_bus.unsubscribe("chat", ws)


async def _safe_send(ws: WebSocket, data: dict):
    """通过连接队列串行发送控制消息，避免和流事件并发写 WebSocket。"""
    event_bus.enqueue_to_ws(ws, data)


# ============ 事件 WebSocket ============

async def handle_events_ws(ws: WebSocket, app_state):
    """
    处理 /ws/events WebSocket 连接

    持续推送系统事件（会话状态变更、子会话通知等）
    客户端只需保持连接即可接收事件
    """
    await ws.accept()
    await event_bus.subscribe("events", ws)

    session_mgr = app_state.session_manager

    notification_task = None
    status_task = None
    try:
        notification_task = asyncio.create_task(
            _consume_notifications(ws, session_mgr)
        )

        status_task = asyncio.create_task(
            _push_status_updates(ws, app_state)
        )

        while True:
            try:
                raw = await ws.receive_text()
                data = json.loads(raw)
                if data.get("type") == "ping":
                    event_bus.enqueue_to_ws(ws, {"type": "pong"})
            except json.JSONDecodeError:
                continue

    except WebSocketDisconnect:
        logger.info("Events WebSocket 客户端断开")
    except Exception as e:
        logger.error(f"Events WebSocket 错误: {e}", exc_info=True)
    finally:
        for task in (notification_task, status_task):
            if task:
                task.cancel()
        await asyncio.gather(
            *(task for task in (notification_task, status_task) if task),
            return_exceptions=True,
        )
        await event_bus.unsubscribe("events", ws)


async def _consume_notifications(ws: WebSocket, session_mgr):
    """消费 notification_queue 并推送到 WebSocket（使用广播器）"""
    notif_queue = session_mgr.notification_broadcaster.subscribe()
    try:
        while True:
            try:
                notif = await asyncio.wait_for(
                    notif_queue.get(), timeout=1.0
                )
                event_bus.enqueue_to_ws(ws, {
                    "type": "notification",
                    "data": notif,
                })
            except asyncio.TimeoutError:
                continue
            except (asyncio.CancelledError, WebSocketDisconnect):
                break
            except Exception:
                await asyncio.sleep(1)
    finally:
        session_mgr.notification_broadcaster.unsubscribe(notif_queue)


async def _push_status_updates(ws: WebSocket, app_state):
    """定期推送系统状态到 Events WebSocket"""
    while True:
        try:
            sm = app_state.session_manager
            status = {
                "type": "status_update",
                "data": {
                    "sessions": sm.get_session_summaries(),
                    "active_sub_count": sm.get_active_sub_count(),
                    "total_sessions": sm.get_total_session_count(),
                    "main_session_id": sm.main_session_id,
                    "main_sessions": sm.get_main_session_summaries(),
                },
            }
            if not event_bus.enqueue_to_ws(ws, status):
                break
            await asyncio.sleep(2)
        except (asyncio.CancelledError, WebSocketDisconnect, asyncio.TimeoutError):
            break
        except Exception:
            await asyncio.sleep(5)
