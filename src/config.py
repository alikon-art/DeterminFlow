import json
import os
import sys
import logging
from pathlib import Path
from typing import Any
from dotenv import load_dotenv

from src.core.default_resources import provision_core_skills
from src.environment import determinflow_env_is_set, get_determinflow_env

load_dotenv()

logger = logging.getLogger(__name__)

# 项目根目录（从 src/ 回溯到项目根）
BASE_DIR = Path(__file__).parent.parent

# 运行数据可重定向，便于测试实例、容器和多环境隔离。
DATA_DIR = Path(get_determinflow_env("DATA_DIR", str(BASE_DIR / "data"))).expanduser().resolve()
LOGS_DIR = Path(get_determinflow_env("LOGS_DIR", str(BASE_DIR / "logs"))).expanduser().resolve()
CONFIG_DIR = Path(get_determinflow_env("CONFIG_DIR", str(BASE_DIR / "config"))).expanduser().resolve()
SKILLS_DIR = DATA_DIR / "skills"
RULES_DIR = DATA_DIR / "rules"
SCRIPT_LIBRARY_DIR = DATA_DIR / "script-library"
PLUGINS_DIR = DATA_DIR / "plugins"
SKILLS_CONFIG_FILE = CONFIG_DIR / "skills_config.json"
RULES_CONFIG_FILE = CONFIG_DIR / "rules_config.json"
PROMPTS_CONFIG_FILE = CONFIG_DIR / "prompts_config.json"
AGENTS_CONFIG_FILE = CONFIG_DIR / "agents_config.json"
PRESET_PHRASES_FILE = CONFIG_DIR / "preset_phrases.json"
SYSTEM_PROMPT_FILE = DATA_DIR / "system_prompt.json"
PROMPT_HISTORY_FILE = DATA_DIR / "prompt_history.json"
SETTINGS_CONFIG_FILE = CONFIG_DIR / "settings.json"
USER_INJECTION_CONFIG_FILE = CONFIG_DIR / "user_injection_config.json"
LLM_PRICING_CONFIG_FILE = CONFIG_DIR / "llm_pricing.json"


def _load_settings() -> dict[str, Any]:
    """加载 config/settings.json 作为默认值。"""
    if not SETTINGS_CONFIG_FILE.exists():
        return {}
    try:
        with open(SETTINGS_CONFIG_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        # 展平分组结构: {"coding": {"KEY": val}} → {"KEY": val}
        flat: dict[str, Any] = {}
        for group, items in raw.items():
            if isinstance(items, dict):
                for key, val in items.items():
                    flat[key] = val
        return flat
    except json.JSONDecodeError as e:
        logger.warning(f"settings.json 格式错误，使用空配置: {e}")
        return {}
    except (PermissionError, OSError) as e:
        logger.error(f"settings.json 无法读取（权限/IO 错误）: {e}")
        return {}
    except UnicodeDecodeError as e:
        logger.error(f"settings.json 编码错误: {e}")
        return {}
    except MemoryError:
        logger.error("settings.json 文件过大，内存不足")
        return {}


def _get_config_value(key: str, default: Any) -> Any:
    """获取配置值：环境变量 > settings.json > 默认值。"""
    env_val = os.getenv(key)
    if env_val is not None:
        return env_val
    settings_val = _settings_defaults.get(key)
    if settings_val is not None:
        return settings_val
    return default


def _get_int_config(key: str, default: int) -> int:
    """获取整数配置值，解析失败时返回默认值。"""
    try:
        return int(_get_config_value(key, str(default)))
    except (ValueError, TypeError):
        logger.warning(f"配置项 {key} 不是有效整数，使用默认值 {default}")
        return default


def _get_bool_config(key: str, default: bool) -> bool:
    """获取布尔配置值，支持 true/false/1/0/yes/no（不区分大小写）。"""
    raw = _get_config_value(key, str(default).lower())
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in ("true", "1", "yes", "on")


# 加载 settings.json 默认值（module-level，只加载一次）
_settings_defaults = _load_settings()

# 多 Agent 会话管理参数
MAX_SUB_SESSIONS = _get_int_config("MAX_SUB_SESSIONS", 5)
SUB_AGENT_MAX_ROUNDS = _get_int_config("SUB_AGENT_MAX_ROUNDS", 10)
SESSION_CACHE_MAX_ENTRIES = max(1, _get_int_config("SESSION_CACHE_MAX_ENTRIES", 64))
SESSION_HISTORY_MAX_ENTRIES = max(
    0,
    _get_int_config("SESSION_HISTORY_MAX_ENTRIES", 1000),
)
SESSIONS_DIR = DATA_DIR / "sessions"

# 工作流编排
WORKFLOWS_DIR = DATA_DIR / "workflows"
WORKFLOW_WORKSPACES_DIR = DATA_DIR / "workspaces"
WORKFLOW_MAX_PARALLEL_NODES = _get_int_config("WORKFLOW_MAX_PARALLEL_NODES", 3)
WORKFLOW_NODE_TIMEOUT_SECONDS = _get_int_config("WORKFLOW_NODE_TIMEOUT_SECONDS", 1800)

AGENT_MESSAGE_HEADER = _get_config_value(
    "AGENT_MESSAGE_HEADER",
    "[来自 {source_type} {source_id} 的消息{task_info}]"
)

ROUNDTABLE_MAX_SEATS = _get_int_config("ROUNDTABLE_MAX_SEATS", 6)
ROUNDTABLE_MAX_ROUNDS = _get_int_config("ROUNDTABLE_MAX_ROUNDS", 10)
ROUNDTABLE_SEAT_MAX_TOOL_ROUNDS = _get_int_config("ROUNDTABLE_SEAT_MAX_TOOL_ROUNDS", 3)
MAX_TOOL_ROUNDS = _get_int_config("MAX_TOOL_ROUNDS", ROUNDTABLE_SEAT_MAX_TOOL_ROUNDS)
ROUNDTABLE_DEFAULT_STRATEGY = _get_config_value("ROUNDTABLE_DEFAULT_STRATEGY", "round_robin")
ROUNDTABLE_COMPRESSOR_ENABLED = _get_bool_config("ROUNDTABLE_COMPRESSOR_ENABLED", False)
ROUNDTABLE_COMPRESSOR_WINDOW = _get_int_config("ROUNDTABLE_COMPRESSOR_WINDOW", 20)
ROUNDTABLE_COMPRESSOR_INTERVAL = _get_int_config("ROUNDTABLE_COMPRESSOR_INTERVAL", 3)
ROUNDTABLE_MAX_IDLE_CYCLES = _get_int_config("ROUNDTABLE_MAX_IDLE_CYCLES", 50)

MAX_CONTEXT_TOKENS = _get_int_config("MAX_CONTEXT_TOKENS", 128000)

LANGGRAPH_RECURSION_LIMIT = _get_int_config("LANGGRAPH_RECURSION_LIMIT", 25)

CODING_TOOLS_ENABLED = _get_bool_config("CODING_TOOLS_ENABLED", True)
CODING_PATH_SANDBOX_ENABLED = _get_bool_config("CODING_PATH_SANDBOX_ENABLED", False)
if os.getenv("CODING_WORKSPACE_BASE"):
    CODING_WORKSPACE_BASE = os.environ["CODING_WORKSPACE_BASE"]
elif determinflow_env_is_set("DATA_DIR"):
    CODING_WORKSPACE_BASE = str(DATA_DIR / "workspaces")
else:
    CODING_WORKSPACE_BASE = _get_config_value("CODING_WORKSPACE_BASE", "data/workspaces")
CODING_CMD_MODE = _get_config_value("CODING_CMD_MODE", "whitelist")
CODING_CMD_BLACKLIST = _get_config_value("CODING_CMD_BLACKLIST", "rm -rf /,format,mkfs,dd if=")
CODING_CMD_WHITELIST = _get_config_value("CODING_CMD_WHITELIST", "npm,node,python,pip,git,ls,cat,echo,cd,mkdir,cp,mv")
CODING_CMD_TIMEOUT = _get_int_config("CODING_CMD_TIMEOUT", 60)
CODING_APPROVAL_TIMEOUT = _get_int_config("CODING_APPROVAL_TIMEOUT", 60)
CODING_MAX_FILE_SIZE = _get_int_config("CODING_MAX_FILE_SIZE", 1048576)
CODING_WORKSPACE_COPY_ON_CREATE = _get_bool_config("CODING_WORKSPACE_COPY_ON_CREATE", True)
CODING_WORKSPACE_COPY_EXCLUDES = _get_config_value("CODING_WORKSPACE_COPY_EXCLUDES", ".git,node_modules,__pycache__,.venv,dist,build")
CODING_WORKSPACE_MAX_SIZE = _get_int_config("CODING_WORKSPACE_MAX_SIZE", 104857600)

WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0")
WEB_PORT = _get_int_config("WEB_PORT", 8020)
SHOW_SYSTEM_PROMPT_TAB = _get_bool_config("SHOW_SYSTEM_PROMPT_TAB", False)

def ensure_dirs():
    """确保运行目录存在，并补齐缺失的 Core 内置资源。"""
    DATA_DIR.mkdir(exist_ok=True)
    LOGS_DIR.mkdir(exist_ok=True)
    CONFIG_DIR.mkdir(exist_ok=True)
    SESSIONS_DIR.mkdir(exist_ok=True)
    SKILLS_DIR.mkdir(exist_ok=True)
    RULES_DIR.mkdir(exist_ok=True)
    SCRIPT_LIBRARY_DIR.mkdir(exist_ok=True)
    PLUGINS_DIR.mkdir(exist_ok=True)
    WORKFLOWS_DIR.mkdir(exist_ok=True)
    WORKFLOW_WORKSPACES_DIR.mkdir(exist_ok=True)
    provision_core_skills(SKILLS_DIR)


# ============================================================
# 配置项元数据定义（用于 API 和前端展示）
# ============================================================

CONFIG_ITEMS: list[dict[str, Any]] = [
    # 多 Agent
    {"key": "MAX_SUB_SESSIONS", "label": "最大并发子会话数", "group": "agent", "type": "number", "min": 1, "max": 20},
    {"key": "SUB_AGENT_MAX_ROUNDS", "label": "子 Agent 最大轮次", "group": "agent", "type": "number", "min": 1, "max": 50},
    {"key": "SESSION_CACHE_MAX_ENTRIES", "label": "历史会话内存缓存数", "group": "agent", "type": "number", "min": 1, "max": 512},
    {"key": "SESSION_HISTORY_MAX_ENTRIES", "label": "Workflow 历史会话保留数", "group": "agent", "type": "number", "min": 0, "max": 100000},
    {"key": "AGENT_MESSAGE_HEADER", "label": "Agent 消息标头模板", "group": "agent", "type": "string"},
    # 圆桌
    {"key": "ROUNDTABLE_MAX_SEATS", "label": "最大席位数", "group": "roundtable", "type": "number", "min": 2, "max": 20},
    {"key": "ROUNDTABLE_MAX_ROUNDS", "label": "最大轮次", "group": "roundtable", "type": "number", "min": 1, "max": 50},
    {"key": "ROUNDTABLE_SEAT_MAX_TOOL_ROUNDS", "label": "席位单次最大工具轮次", "group": "roundtable", "type": "number", "min": 1, "max": 20},
    {"key": "ROUNDTABLE_DEFAULT_STRATEGY", "label": "默认调度策略", "group": "roundtable", "type": "select",
     "options": ["round_robin", "priority", "random"]},
    {"key": "ROUNDTABLE_COMPRESSOR_ENABLED", "label": "压缩器开关", "group": "roundtable", "type": "boolean"},
    {"key": "ROUNDTABLE_COMPRESSOR_WINDOW", "label": "压缩器窗口大小", "group": "roundtable", "type": "number", "min": 5, "max": 100},
    {"key": "ROUNDTABLE_COMPRESSOR_INTERVAL", "label": "压缩器摘要间隔", "group": "roundtable", "type": "number", "min": 1, "max": 20},
    {"key": "ROUNDTABLE_MAX_IDLE_CYCLES", "label": "Moderator 最大空闲循环", "group": "roundtable", "type": "number", "min": 10, "max": 200},
    # 上下文管理
    {"key": "MAX_CONTEXT_TOKENS", "label": "最大上下文 Token 数", "group": "system", "type": "number", "min": 1000, "max": 2000000},
    # LangGraph
    {"key": "LANGGRAPH_RECURSION_LIMIT", "label": "LangGraph 递归限制", "group": "system", "type": "number", "min": 5, "max": 100},
    {"key": "SHOW_SYSTEM_PROMPT_TAB", "label": "顶部展示系统提示词", "group": "system", "type": "boolean"},
    # Web 服务（只读）
    {"key": "WEB_HOST", "label": "Web 服务地址", "group": "system", "type": "string", "readonly": True},
    {"key": "WEB_PORT", "label": "Web 服务端口", "group": "system", "type": "number", "readonly": True},
    # 编码工具配置
    {"key": "CODING_TOOLS_ENABLED", "label": "编码工具总开关", "group": "coding", "type": "boolean"},
    {"key": "CODING_PATH_SANDBOX_ENABLED", "label": "路径沙箱开关", "group": "coding", "type": "boolean"},
    {"key": "CODING_WORKSPACE_BASE", "label": "Workspace 存储根目录", "group": "coding", "type": "string"},
    {"key": "CODING_CMD_MODE", "label": "命令审批模式", "group": "coding", "type": "select",
     "options": ["allow_all", "approve_all", "blacklist", "whitelist"]},
    {"key": "CODING_CMD_BLACKLIST", "label": "命令黑名单（逗号分隔）", "group": "coding", "type": "string"},
    {"key": "CODING_CMD_WHITELIST", "label": "命令白名单（逗号分隔）", "group": "coding", "type": "string"},
    {"key": "CODING_CMD_TIMEOUT", "label": "命令执行超时（秒）", "group": "coding", "type": "number", "min": 5, "max": 300},
    {"key": "CODING_APPROVAL_TIMEOUT", "label": "审批等待超时（秒）", "group": "coding", "type": "number", "min": 10, "max": 300},
    {"key": "CODING_MAX_FILE_SIZE", "label": "最大文件大小（字节）", "group": "coding", "type": "number", "min": 1024, "max": 10485760},
    {"key": "CODING_WORKSPACE_COPY_ON_CREATE", "label": "创建子会话时复制 Main Workspace", "group": "coding", "type": "boolean"},
    {"key": "CODING_WORKSPACE_COPY_EXCLUDES", "label": "复制排除目录（逗号分隔）", "group": "coding", "type": "string"},
    {"key": "CODING_WORKSPACE_MAX_SIZE", "label": "Workspace 最大大小（字节）", "group": "coding", "type": "number", "min": 1048576, "max": 1073741824},
]

# 配置项 key 到元数据的快速映射
_CONFIG_META = {item["key"]: item for item in CONFIG_ITEMS}


def get_all_config() -> dict[str, Any]:
    """返回所有配置项的当前运行时值。

    Returns:
        {key: value} 的配置字典
    """
    config_module = sys.modules[__name__]
    result = {}
    for item in CONFIG_ITEMS:
        key = item["key"]
        value = getattr(config_module, key, None)
        if value is None and not hasattr(config_module, key):
            logger.warning(f"配置项 {key} 在模块中未定义（CONFIG_ITEMS 与模块变量不一致）")
        result[key] = value
    return result


def update_config(updates: dict[str, Any], persist: bool = True) -> dict[str, Any]:
    """更新运行时配置，可选持久化到 .env 文件。

    Args:
        updates: {key: new_value} 需要更新的配置项
        persist: 是否同时写回 .env 文件，默认 True

    Returns:
        更新后的完整配置（脱敏）
    """
    config_module = sys.modules[__name__]
    readonly_keys = {item["key"] for item in CONFIG_ITEMS if item.get("readonly")}
    valid_keys = {item["key"] for item in CONFIG_ITEMS}

    updated_keys = []
    for key, value in updates.items():
        if key not in valid_keys:
            logger.warning(f"忽略未知配置项: {key}")
            continue
        if key in readonly_keys:
            logger.warning(f"忽略只读配置项: {key}")
            continue

        meta = _CONFIG_META[key]
        config_type = meta.get("type", "string")

        # 类型转换
        if config_type == "number":
            value = float(value) if "." in str(value) else int(value)
            # 值域校验
            min_val = meta.get("min")
            max_val = meta.get("max")
            if min_val is not None and value < min_val:
                logger.warning(f"配置项 {key} 值 {value} 低于最小值 {min_val}，已自动修正")
                value = min_val
            if max_val is not None and value > max_val:
                logger.warning(f"配置项 {key} 值 {value} 超过最大值 {max_val}，已自动修正")
                value = max_val
        elif config_type == "boolean":
            if isinstance(value, str):
                value = value.lower() in ("true", "1", "yes")
            else:
                value = bool(value)
        else:
            value = str(value)

        setattr(config_module, key, value)
        updated_keys.append(key)
        logger.info(f"配置已更新: {key} = {value}")

    # 持久化到 config/settings.json
    if persist and updated_keys:
        _persist_settings(updates, updated_keys)

    return get_all_config()


def _persist_settings(updates: dict[str, Any], keys: list[str]):
    """将配置写回 config/settings.json（按分组结构组织）。"""
    # 加载现有文件（保留结构和已有值）
    settings: dict[str, Any] = {}
    if SETTINGS_CONFIG_FILE.exists():
        try:
            with open(SETTINGS_CONFIG_FILE, "r", encoding="utf-8") as f:
                settings = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"读取现有 settings.json 失败，将覆盖重写: {e}")

    # 从 CONFIG_ITEMS 构建 key → group 映射
    key_to_group: dict[str, str | None] = {
        item["key"]: item.get("group") for item in CONFIG_ITEMS
    }

    for key in keys:
        value = updates[key]
        # 布尔值转字符串
        if isinstance(value, bool):
            value = "true" if value else "false"
        # 找到对应的 group
        group = key_to_group.get(key)
        if not group:
            logger.warning(f"配置项 {key} 无 group 定义，跳过持久化")
            continue
        if group not in settings:
            settings[group] = {}
        if not isinstance(settings[group], dict):
            settings[group] = {}
        settings[group][key] = value

    settings.setdefault("version", "1.0")

    tmp_path = SETTINGS_CONFIG_FILE.with_suffix(".tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
        os.replace(str(tmp_path), str(SETTINGS_CONFIG_FILE))
        logger.info(f"settings.json 已更新: {keys}")
    except OSError as e:
        logger.error(f"settings.json 写入失败，配置变更未持久化: {e}")
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
