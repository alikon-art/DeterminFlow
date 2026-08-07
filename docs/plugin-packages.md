# Plugin Package 规范

本文定义 DeterminFlow Plugin Package v1。Extension 仍是运行时扩展契约，Plugin
Package 是它的 Git 分发、安装、版本锁定和管理层。

## 边界

- Plugin 从本地或互联网 Git 仓库安装。
- 每次安装、更新和回退都解析为精确 commit，并记录内容 SHA256。
- 安装、更新、回退、启用、停用、卸载和配置修改都只改变目标状态，重启主进程后生效。
- Plugin 与 Core 共享 Python 环境和操作系统权限，不提供进程或权限隔离。
- 官方来源按完整 canonical URL 精确匹配并视为可信。
- 非官方来源必须由用户显式确认风险；Plugin 代码、依赖和子进程均可能取得与
  DeterminFlow 相同的权限。
- Git URL 不得携带 userinfo 凭据、query token 或 fragment；私有仓库应使用
  SSH agent、Git credential helper 或宿主机已有的安全凭据机制。
- 卸载只移除目标启用状态，不删除 Plugin 配置、数据和当前进程仍在使用的 checkout。

## 仓库布局

单 Plugin 仓库可以把 `extension.toml` 放在仓库根目录。一个仓库包含多个 Plugin
时，使用 subdirectory 指向 Plugin 根目录：

```text
plugin-repository.toml
plugins/
  example-plugin/
    extension.toml
    requirements.txt
    settings.schema.json
    backend/
    resources/
    ui/
```

`plugin-repository.toml` 是可选的目录索引：

```toml
schema_version = "1"
name = "Example Plugins"

[[plugins]]
id = "example-plugin"
subdirectory = "plugins/example-plugin"
```

安装时仍以 Plugin 根目录中的 `extension.toml` 为最终契约。
Core 通过 `config/plugin-sources.json` 中的官方 Git 地址按需读取该索引，并在插件
页面提供快捷安装入口；索引只负责发现，安装仍会重新预检清单并锁定精确 commit。
官方来源可以配置 `mirrors` 镜像地址数组。Core 会并行探测主地址与镜像：主地址可达时
只允许返回同一 commit 的镜像参与传输，并按 `mirrors` 配置顺序优先使用镜像，避免镜像
尚未同步时安装旧版本；首选镜像不可用时再回退其他镜像或主地址。Plugin 锁仍记录主地址，
镜像只作为传输通道。
未提供索引的仓库仍可通过 Plugin ID、Git URL、ref 和 subdirectory 手工安装。
官方来源在主进程启动时完成 canonicalization（规范化）并冻结；运行中修改来源文件
不会改变信任判断或 Catalog 响应。Catalog 使用 TTL cache（有效期缓存）和
single-flight（单次并发刷新），避免一次页面刷新触发多个 Git clone。

## extension.toml

```toml
[extension]
id = "example-plugin"
name = "Example Plugin"
version = "1.0.0"
api_version = "1"
description = "Example package."
backend = "example_plugin.extension:create_extension"
dependencies = []
capabilities = ["api.routes", "resources.workflows"]

[resource_namespace]
prefix = "example"

[resources]
agents = "resources/agents.json"
prompts = "resources/prompts.json"
skills = "resources/skills.json"
skill_bundles = "resources/skill-bundles"
rules = "resources/rules.json"
rule_bundles = "resources/rule-bundles"
workflows = "resources/workflows"
script_libraries = "resources/script-library"

[installation]
requirements = "requirements.txt"

[settings]
schema = "settings.schema.json"

[lifecycle]
migrate_command = [
  "${PYTHON}",
  "-m",
  "example_plugin.migrations",
  "migrate",
  "--revision",
  "${PLUGIN_REVISION}",
]
verify_command = [
  "${PYTHON}",
  "-m",
  "example_plugin.migrations",
  "verify",
  "--revision",
  "${PLUGIN_REVISION}",
]
timeout_seconds = 300

[page]
label = "Example 配置"
static_dir = "ui"
entrypoint = "index.html"

[[processes]]
id = "example-api"
command = ["${PYTHON}", "-m", "example_plugin.api"]
working_directory = "."
environment = { EXAMPLE_CONFIG = "${CONFIG_FILE}" }
healthcheck_url = "http://127.0.0.1:8090/health"
start_timeout_seconds = 30
stop_timeout_seconds = 10
```

### `[extension]`

| 字段 | 必填 | 说明 |
|---|---:|---|
| `id` | 是 | 全局唯一，使用小写 kebab-case |
| `name` | 是 | 显示名称 |
| `version` | 是 | Plugin 自报版本；安装版本仍以锁定 commit 为准 |
| `api_version` | 是 | Core Extension API 版本，当前为 `1` |
| `backend` | 否 | `python.module:attribute`，attribute 返回 Extension 实例 |
| `dependencies` | 否 | 其他 Plugin ID |
| `capabilities` | 否 | 用于展示和审计的能力声明，不是权限系统 |

Python package 名必须全局唯一。禁止使用 `backend`、`plugin` 等通用顶层 package
名，也不得依赖 Plugin checkout 在 DeterminFlow 仓库中的相对位置。

### `[resource_namespace]`

`prefix` 由 Plugin 开发者声明，使用小写 kebab-case。普通用户不需要理解或填写它；
Core 在资源列表中保留有效 ID，让用户能弱感知资源来自哪个 Plugin。安装页面只在
高级选项中允许覆盖 Prefix，用于解决多个 Plugin 的命名冲突。

Core 把 `(plugin_id, resource kind, local_id)` 作为真实身份，在冷启动时生成显式
local-to-effective mapping（本地 ID 到有效 ID 映射）和只读运行快照。Plugin 源码
继续使用本地 ID，结构化的内部引用由 Core 重写；不会通过拆字符串反推 owner，也
不会改写 Prompt 正文、脚本正文、Workflow node ID、配置键、数据库表或公开 API
路径。Backend 通过以下接口解析本 Plugin 或依赖 Plugin 的资源：

```python
own_prompt = runtime.resolve_resource("prompt", "writer")
build_workflow = runtime.resolve_resource(
    "workflow",
    "build",
    plugin_id="shared-writing-resources",
)
```

跨 Plugin owner 必须先列入调用方 Manifest 的 `dependencies`，未声明依赖会失败
关闭。Plugin 不应自行拼接 Prefix，也不应通过裁剪 Prefix 反推 owner。

最终 Prefix 和来源写入 `plugins.lock.json`。使用开发者默认值时，更新或回退会采用
目标版本 Manifest 的 Prefix；安装时手动覆盖后，该覆盖值在更新和回退时保持不变。
若要修改覆盖值，应卸载后重新安装并迁移外部持久引用。

修改 local ID 或首次把历史全局 ID 迁入命名空间会产生新的资源身份，不做静默
alias（别名）。旧 Workflow 目录、Task 和 Run 历史会保留并标记为 inactive
（非活动），可继续读取审计，但不会以新 ID 恢复执行；发布这类升级前应先完成或
停止在途 Task，并在 release note 中列出 ID mapping。

### `[resources]`

沿用 Extension API v1，支持：

- `agents`
- `prompts`
- `skills`
- `skill_bundles`
- `rules`
- `rule_bundles`
- `preset_phrases`
- `workflows`
- `script_libraries`

值可以是相对 Plugin 根目录的单一路径或路径数组。路径和符号链接解析后都不得逃出
Plugin 根目录；预检会拒绝资源树中的符号链接。`skills`、`rules` 是结构化配置
JSON，`skill_bundles`、`rule_bundles` 分别是带 `SKILL.md`、`RULE.md` 的完整目录
资源。Plugin Bundle 在运行时只读，编辑、删除或覆盖请求会被拒绝。

Workflow Node 类型不属于 Plugin 扩展面。Plugin 可以捆绑 Workflow 模板并使用
Core 已提供的 Node，但不能注册新的 Node 类型。

### `[installation]`

`requirements` 可声明一个 Plugin 根目录内的 pip requirements 文件。依赖安装到
DeterminFlow 当前 Python 环境，不创建独立虚拟环境；版本冲突会影响 Core 和其他
Plugin。依赖变更后必须重启。

### `[settings]`

`schema` 指向本地 JSON Schema。v1 只支持：

- `object` 和嵌套对象
- `string`
- `number`
- `integer`
- `boolean`
- `enum`
- `array<string>`
- `required`、`default`、`description`
- `minimum`、`maximum`
- `format: password | uri | multiline`

不支持远程 `$ref`、`oneOf`、条件 Schema 和自定义 React 组件。Core 是最终校验方。
配置写入 `data/plugins/config/<plugin-id>.json`，卸载时保留。该文件只保存用户
显式提交的字段，不展开 Schema 默认值；每次冷启动会生成独立 applied snapshot，
Backend、脚本和子进程在当前进程存活期间始终读取该快照。
Core 另存不含配置值的历史敏感字段路径；字段在新版 Schema 中移除、改名或取消
`password` 标记后，旧值仍不会通过管理状态接口回显。

Backend Extension 可通过 `runtime.get_service("plugin_config")` 读取本次启动时
已应用的配置。Plugin 自带的 Script Library 脚本会收到符合环境变量命名规则的
顶层配置；子进程通过 `${CONFIG_FILE}` 读取同一配置文件。运行中保存的新配置不会
热应用。对顶层大写配置项，优先级为“已显式保存的 Plugin 配置 > 同名进程环境变量
> Schema 默认值”；这样容器 Secret 注入不会被未保存的 UI 默认值覆盖。

### `[lifecycle]`

可选的 forward-only（只向前）生命周期用于数据库等外部状态升级。`migrate_command`
先执行，成功后再执行 `verify_command`；任一失败或超时都会让 Plugin 进入
`degraded` 并阻断依赖它的 Plugin。命令使用 argv，不经过 Shell，Host 不提供
自动 down migration（向下迁移）。

支持 `working_directory`、`timeout_seconds` 以及进程占位符，并额外支持
`${PLUGIN_REVISION}`。生命周期命令会收到当前 applied Plugin settings 对应的显式
环境变量，不会继承宿主的任意环境或把 stdout/stderr 写入错误响应。命令必须幂等，
因为每次主进程冷启动都会执行。

### `[page]`

可选页面只承担轻量可视化配置。`static_dir` 和 `entrypoint` 必须位于 Plugin 根目录
内，构建产物必须使用相对资源 URL。Core 在 Plugin 管理详情中通过 iframe 加载，
不把页面加入顶层导航，也不把 iframe 作为安全隔离。

### `[[processes]]`

Plugin 可声明零个或多个子进程：

- `command` 必须是非空 argv 数组，不经过 Shell。
- `working_directory` 必须位于 Plugin 根目录内。
- `environment` 只增加或覆盖子进程环境变量。
- 未声明 `healthcheck_url` 时，以进程存活作为启动成功。
- 运行中异常退出会主动降级 owner、撤销 Tool、停止同 owner 进程并逆序阻断依赖者。
- 关闭时先发送 terminate，超过 `stop_timeout_seconds` 后 kill。

支持以下占位符：

| 占位符 | 值 |
|---|---|
| `${PYTHON}` | Core 当前 Python executable |
| `${PLUGIN_DIR}` | 当前锁定 Plugin 根目录 |
| `${CONFIG_FILE}` | Plugin 配置 JSON |
| `${DATA_DIR}` | Plugin 持久数据目录 |
| `${BASE_DIR}` | DeterminFlow Core 根目录 |

## 安装锁与状态

Core 把 checkout 存入 `data/plugins/checkouts/<plugin-id>/<revision>`，把锁写入
`data/plugins/plugins.lock.json`。锁至少记录：

- source URL、requested ref、subdirectory、trust
- resolved commit、内容 SHA256、Plugin version
- 当前目标 revision、历史 revision、pending action
- 当前生效的 `resource_prefix` 及其来源；开发者默认随目标版本变化，安装覆盖值固定

安装内容不得包含已提交的 `__pycache__`、`.pyc` 或 `.pyo`。运行时生成的
`__pycache__` 不计入内容摘要，其余 checkout 内容发生变化时，下一次冷启动会拒绝
加载。

管理请求只获取并完整预检包，不执行 pip，也不 import Backend 或执行脚本。预检覆盖
Manifest、JSON、Workflow、Skill/Rule Bundle、Script Library、资源 ID、路径逃逸和
符号链接。共享依赖只在主进程冷启动应用目标 revision
时安装；失败时 non-strict 模式仅降级该 Plugin，并阻断依赖它的 Plugin。

Script Library 在 Task 创建时冻结 owner、Plugin revision、entrypoint 和相关文件
摘要，执行前重新核验；缺失、重复或漂移时 fail-closed（失败即拒绝执行）。

运行中的 applied state（已应用状态）在主进程启动时固定；磁盘上的 desired state
（目标状态）可由管理 API 修改。两者任一不同即返回 `restart_required=true`。
更新或卸载不得覆盖、删除当前进程仍在使用的 checkout。

## 管理 API

| 方法 | 路径 | 作用 |
|---|---|---|
| `GET` | `/api/plugins` | 列出运行状态、目标状态、来源、版本和配置契约 |
| `GET` | `/api/plugins/catalog` | 按需读取官方仓库的可选目录索引 |
| `POST` | `/api/plugins/install` | 从 Git URL/ref/subdirectory 安装；可选高级 `resource_prefix` 覆盖 |
| `PUT` | `/api/plugins/{id}/enabled` | 修改目标启用状态 |
| `POST` | `/api/plugins/{id}/update` | 获取当前 tracking ref 或请求体指定 ref 的新精确 commit |
| `POST` | `/api/plugins/{id}/rollback` | 切回历史 revision |
| `DELETE` | `/api/plugins/{id}` | 标记重启后卸载并停用 |
| `PUT` | `/api/plugins/{id}/config` | 校验并保存配置 |
| `DELETE` | `/api/plugins/{id}/config` | 清空显式配置，重启后重新使用环境与默认值 |
| `GET` | `/api/plugins/{id}/ui/{path}` | 提供已运行 Plugin 的静态页面 |

所有写操作都不得在当前进程中加载、卸载或替换 Plugin。

不可变 Release 可以设置 `DETERMINFLOW_PLUGIN_PACKAGES_READ_ONLY=true`。此模式下
安装、更新、回退、卸载和 Catalog 获取被关闭，`GET /api/plugins` 会返回
`package_management_read_only=true`；启用、停用和配置仍写入持久目标状态，并在
主进程重启后生效。它用于不在运行容器内提供 Git 和可写 checkout 的生产发布，不是
通用本地部署的默认行为。

写操作默认只接受本机回环连接。经局域网或反向代理管理时，服务端必须配置
`DETERMINFLOW_PLUGIN_ADMIN_TOKEN`，客户端通过 `Authorization: Bearer <token>`
提交；建议使用至少 32 bytes 的随机值。状态查询和已启用 Plugin 的静态页面不要求
该管理令牌。

旧版 `AI_COMPANY_*` 环境变量仍作为兼容别名读取；新旧名称同时存在时，
`DETERMINFLOW_*` 优先。
