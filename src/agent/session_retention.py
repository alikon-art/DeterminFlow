"""在主 Runtime 启动前裁剪持久化 Workflow Session 历史。"""
from __future__ import annotations

import logging

from src.agent.session_manager import SessionManager
from src.config import SESSIONS_DIR

logger = logging.getLogger(__name__)


def main() -> None:
    manager = SessionManager()
    scan_result = manager._session_catalog.scan(SESSIONS_DIR)
    prune_result = manager._prune_terminal_workflow_history()
    logger.info(
        "Session 启动前裁剪完成: scanned=%s retained=%s pruned=%s "
        "freed_bytes=%s errors=%s",
        scan_result["scanned"],
        len(manager._session_catalog),
        len(prune_result["deleted"]),
        prune_result["deleted_bytes"],
        scan_result["errors"] + prune_result["errors"],
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
