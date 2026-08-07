# DeterminFlow Windows Desktop

本目录只服务于桌面发行构建。服务版仍从仓库根目录执行 `python run.py`，无需加载这里的 Tauri、PyInstaller 或 NSIS 配置。

## 架构

| 部分 | 实现 | 运行职责 |
|---|---|---|
| 桌面壳 | Tauri 2 + Windows WebView2 | 创建原生窗口、启动和关闭本地后端 |
| 后端 | PyInstaller `onedir` | 冻结现有 Python/FastAPI 服务，不要求用户安装 Python |
| 前端 | 现有 `web/dist` | 由本地 FastAPI 服务提供，入口和服务版一致 |
| 桌面适配 | `ui/desktop-adapter.js` | 仅由 Tauri 注入，提供错误弹窗和 Provider Key 保存前连通性检查；服务版不加载 |
| 安装包 | NSIS `currentUser` | 安装到当前用户目录，不申请管理员权限；向导使用正式品牌图 |
| 更新 | Tauri Updater + GitHub/Gitee Releases | Gitee 优先，失败时回退 GitHub；用户确认后下载签名更新并重启 |
| 构建 | GitHub Actions `windows-2025` | 在真实 x64 Windows Runner 上生成、安装、启动并卸载验证安装包 |

同一版本生成两个安装包：

| 安装包 | 内容 | 后续更新 |
|---|---|---|
| Core | 纯净 DeterminFlow Core | 通过 `latest.json` 更新 Core |
| Full | 同一 Core，加构建时全部公开官方 Plugin 快照 | Core 仍走同一 `latest.json`；Plugin 由 Plugin 页面独立更新 |

Full 不是单独 Edition，也不使用第二套应用标识、数据目录或 Core 更新通道。Full 首次
启动会把快照中精确锁定的 Plugin 合并进用户数据并启用一次；已存在的 Plugin 记录不会
被覆盖。之后安装普通 Core 更新包不会删除 Plugin，用户手动停用的 Plugin 也不会在日常
启动时被重新启用。

桌面进程每次选择一个空闲的 `127.0.0.1` 端口。Windows Release 使用 GUI Subsystem，只显示主界面，不额外打开 CMD 窗口；应用、安装器和卸载器统一使用 `web/public/brand/determinflow-mark.svg` 对应的正式图标。窗口在 `/api/system/status` 返回成功后才进入现有 Web UI；重复打开只会唤起已有窗口；窗口退出时会终止内置后端及其子进程。

## 数据边界

运行数据位于 `%LOCALAPPDATA%\\io.determinflow.desktop`：

```text
io.determinflow.desktop/
├── config/  # 用户配置；升级时不覆盖
├── data/    # 会话、工作流、Workspace、Skills、Rules、Plugins
└── logs/    # 服务日志与 backend-console.log
```

构建只读取 Git `HEAD` 中的白名单配置。模型配置由 `models_config.example.json` 生成；MCP Server 和 Extension 默认关闭；Plugin Source 固定为公开仓库。忽略的 `config/models_config.json`、工作区数据、本地 Plugin 状态和凭据不会进入安装包。

升级不会覆盖模型、会话、Workflow、Workspace、Plugin 锁或用户自定义 Plugin 仓库；
Core 拥有且 UI 中不可编辑的官方 Plugin Source 会随桌面 Runtime 刷新，因此 Plugin
Catalog 可以在不发布新 Core 的情况下继续跟踪官方仓库 `main`。

## 本地验证

macOS 可以完成平台无关测试、Web 构建、PyInstaller 后端冒烟测试和 Rust 编译检查；NSIS 安装、WebView2 和卸载行为必须由 Windows CI 验证。

```bash
python -m pytest tests/test_desktop_packaging.py -q
python desktop/scripts/stage_defaults.py
(cd web && npm ci && npm run build)
python -m pip install pyinstaller==6.21.0
python desktop/scripts/build_backend.py
python desktop/scripts/smoke_backend.py
python desktop/scripts/verify_bundle.py
(cd desktop && npm ci)
(cd desktop && npm test)
(cd desktop/src-tauri && cargo test)
```

GitHub 临时分支 `codex/desktop-tauri-poc` 会运行 `.github/workflows/desktop-windows.yml`，分别上传 14 天有效的 Core/Full 候选 Artifact，不创建 Tag 或 Release。`v*` Tag 则在两种安装包全部通过 Windows 安装、启动和卸载验证后，创建正式 GitHub Release。

## 桌面更新发布

桌面端并行检查 GitHub 与 Gitee 的最新 Release。版本不同时选择较新版本；版本与签名一致时优先从 Gitee 下载，下载失败后回退 GitHub。清单始终指向 Core 安装包，两个来源最终都必须通过同一 Tauri 公钥验签。正式发布时，GitHub Release 同时上传 Core/Full NSIS 安装包、各自同名 `.sig`、SHA-256 文件和 `latest.json`；清单可通过 `desktop/scripts/create_update_manifest.py` 生成。更新私钥不得进入 Git，只通过 GitHub Actions Secret `TAURI_SIGNING_PRIVATE_KEY` 注入构建。

Full 构建从官方 Plugin 仓库的 `main` Catalog 解析当时全部公开 Plugin，执行声明式资源
预检后锁定精确 Commit 与内容摘要，再写入安装包。Core 自动更新不重置 Plugin 状态。
当前 Plugin 在线安装和后续更新仍调用系统 Git；没有 Git 的用户可以使用 Full 的内置
快照，但要从 UI 更新到未来 Plugin 版本仍需先安装 Git。

服务版仍按原入口运行，不初始化 Tauri 更新插件，也不显示更新 UI。若 GitHub 与 Gitee 的最新 Release 都没有可用 `latest.json`，桌面端会保留当前版本并提示更新服务尚未发布，不影响应用本身使用。

## 首版限制

- 安装包尚未做 Authenticode（Windows 代码签名），因此不同 Windows 设备上的 SmartScreen 表现可能不同。
- 正式发布前必须在 Windows Runner 验证正常关窗、重复启动、Updater 安装、覆盖安装与卸载
  都不会遗留 `determinflow-backend.exe`，并完成一次真实跨版本升级验收。
- 不内置 Node.js、npm、Git 或 Git Bash。`execute_command` 使用 Windows `cmd.exe`；Python Workflow 由冻结后端兼容执行；Shell Workflow 需要用户另行安装 Git Bash。
- `downloadBootstrapper` 保持安装包较小。Windows 10/11 通常已有 WebView2；缺失时安装器需要联网下载。
