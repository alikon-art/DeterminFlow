import { lazy, Suspense, useMemo } from "react";
import { MessageSquare, LayoutDashboard, GitBranch, Users, Layers, Settings, BookOpen, Wifi, WifiOff, FileText, Workflow, Clock, Boxes, Loader2, type LucideIcon } from "lucide-react";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ToastProvider } from "@/components/ui/toast-provider";
import { CORE_TAB_IDS, isCoreTabId, type CoreTabId } from "@/core-tabs";
import { BRAND_MARK, PRODUCT_NAME } from "@/brand";
import { useGlobalEvents } from "./hooks/useGlobalEvents";
import { patchSearchParams, useUrlParam } from "./hooks/useUrlParam";
import { useExtensions } from "./extensions/context-value";
import { ExtensionHeaderStatusSlot } from "./extensions/ExtensionHeaderStatusSlot";
import { DesktopUpdateProvider } from "./desktop-updater/context";
import { DesktopUpdateNotice } from "./desktop-updater/DesktopUpdateNotice";
import { useNavigationSettings } from "./hooks/useNavigationSettings";
import FirstRunOnboarding from "./components/onboarding/FirstRunOnboarding";

const ChatPage = lazy(() => import("./pages/ChatPage"));
const DashboardPage = lazy(() => import("./pages/DashboardPage"));
const GraphPage = lazy(() => import("./pages/GraphPage"));
const RoundtablePage = lazy(() => import("./pages/RoundtablePage"));
const OrchestrationPage = lazy(() => import("./pages/OrchestrationPage"));
const WorkflowPage = lazy(() => import("./pages/WorkflowPage"));
const SettingsPage = lazy(() => import("./pages/SettingsPage"));
const SkillsPage = lazy(() => import("./pages/SkillsPage"));
const RulesPage = lazy(() => import("./pages/RulesPage"));
const SystemPromptPage = lazy(() => import("./pages/SystemPromptPage"));
const CronPage = lazy(() => import("./pages/CronPage"));
const ExtensionsPage = lazy(() => import("./pages/ExtensionsPage"));

/** Tab 配置：value + 图标 + 标签 + active 样式 */
interface TabConfig {
  value: string;
  icon: LucideIcon;
  label: string;
  activeClass: string;
}

const CORE_TAB_METADATA: Record<CoreTabId, Omit<TabConfig, "value">> = {
  chat: { icon: MessageSquare, label: "对话", activeClass: "data-[state=active]:bg-indigo-500/20 data-[state=active]:text-indigo-400" },
  dashboard: { icon: LayoutDashboard, label: "看板", activeClass: "data-[state=active]:bg-purple-500/20 data-[state=active]:text-purple-400" },
  graph: { icon: GitBranch, label: "图谱", activeClass: "data-[state=active]:bg-cyan-500/20 data-[state=active]:text-cyan-400" },
  roundtable: { icon: Users, label: "圆桌", activeClass: "data-[state=active]:bg-green-500/20 data-[state=active]:text-green-400" },
  orchestration: { icon: Layers, label: "编排", activeClass: "data-[state=active]:bg-amber-500/20 data-[state=active]:text-amber-400" },
  workflow: { icon: Workflow, label: "工作流", activeClass: "data-[state=active]:bg-purple-500/20 data-[state=active]:text-purple-400" },
  cron: { icon: Clock, label: "定时", activeClass: "data-[state=active]:bg-amber-500/20 data-[state=active]:text-amber-400" },
  skills: { icon: BookOpen, label: "技能", activeClass: "data-[state=active]:bg-pink-500/20 data-[state=active]:text-pink-400" },
  rules: { icon: BookOpen, label: "规则", activeClass: "data-[state=active]:bg-red-500/20 data-[state=active]:text-red-400" },
  "system-prompt": { icon: FileText, label: "系统提示词", activeClass: "data-[state=active]:bg-teal-500/20 data-[state=active]:text-teal-400" },
  settings: { icon: Settings, label: "配置", activeClass: "data-[state=active]:bg-indigo-500/20 data-[state=active]:text-indigo-400" },
  extensions: { icon: Boxes, label: "插件", activeClass: "data-[state=active]:bg-cyan-500/20 data-[state=active]:text-cyan-400" },
};

const CORE_TAB_CONFIG: TabConfig[] = CORE_TAB_IDS.map((value) => ({
  value,
  ...CORE_TAB_METADATA[value],
}));

/** 页面路由映射 */
const CORE_PAGE_MAP: Record<CoreTabId, React.ComponentType> = {
  chat: ChatPage,
  dashboard: DashboardPage,
  graph: GraphPage,
  roundtable: RoundtablePage,
  orchestration: OrchestrationPage,
  workflow: WorkflowPage,
  cron: CronPage,
  skills: SkillsPage,
  rules: RulesPage,
  "system-prompt": SystemPromptPage,
  settings: SettingsPage,
  extensions: ExtensionsPage,
};

function GlobalConnectionStatus() {
  const { connected } = useGlobalEvents();

  return (
    <div className="flex shrink-0 items-center gap-3" aria-live="polite">
      <div className="flex items-center gap-2 text-sm">
        {connected ? (
          <Wifi size={14} className="text-green-400" aria-hidden="true" />
        ) : (
          <WifiOff size={14} className="text-red-400" aria-hidden="true" />
        )}
        <span className={`hidden xl:inline ${connected ? "text-green-400" : "text-red-400"}`}>
          {connected ? "已连接" : "断开"}
        </span>
        <span className="sr-only">
          {connected ? "WebSocket 已连接" : "WebSocket 连接断开"}
        </span>
      </div>
    </div>
  );
}

function PageLoadingFallback() {
  return (
    <div className="flex min-h-[calc(100dvh-3.5rem)] items-center justify-center" role="status" aria-live="polite">
      <div className="flex items-center gap-2 text-sm text-slate-400">
        <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
        <span>正在加载页面...</span>
      </div>
    </div>
  );
}

function App() {
  const extensions = useExtensions();
  const showSystemPromptTab = useNavigationSettings();
  const [requestedTab, setRequestedTab] = useUrlParam("tab");
  const extensionPages = useMemo(() => extensions.flatMap((extension) => extension.pages || []), [extensions]);
  const tabs = useMemo<TabConfig[]>(() => [
    ...CORE_TAB_CONFIG.filter((tab) => tab.value !== "system-prompt" || showSystemPromptTab),
    ...extensionPages.map((page) => ({
      value: page.id,
      icon: page.icon,
      label: page.label,
      activeClass: page.activeClass,
    })),
  ], [extensionPages, showSystemPromptTab]);
  const activeTab = tabs.some((tab) => tab.value === requestedTab)
    ? requestedTab!
    : "chat";
  const ExtensionPage = extensionPages.find((page) => page.id === activeTab)?.component;
  const CorePage = isCoreTabId(activeTab) ? CORE_PAGE_MAP[activeTab] : undefined;

  const handleTabChange = (value: string) => {
    setRequestedTab(value === "chat" ? null : value);
  };

  const handleManageExtension = (extensionId: string) => {
    const nextSearch = patchSearchParams(window.location.search, {
      tab: "extensions",
      plugin: extensionId,
    });
    window.history.pushState(window.history.state, "", `${window.location.pathname}${nextSearch}${window.location.hash}`);
    window.dispatchEvent(new PopStateEvent("popstate"));
  };

  return (
    <DesktopUpdateProvider>
      <ToastProvider>
        <FirstRunOnboarding>
          <div className="theme-light flex h-dvh flex-col overflow-hidden bg-stone-50 text-slate-900">
            {/* Top Navigation Bar */}
            <header className="fixed top-0 left-0 right-0 z-50 h-14 border-b border-slate-200 bg-white">
              <div className="h-full flex items-center gap-3 px-4">
                {/* Brand */}
                <div className="flex shrink-0 items-center gap-3">
                  <img
                    src={BRAND_MARK}
                    alt={PRODUCT_NAME}
                    className="h-8 w-8 shrink-0"
                  />
                  <h1
                    className="hidden text-sm font-semibold tracking-tight text-slate-950 2xl:block"
                    aria-hidden="true"
                  >
                    {PRODUCT_NAME}
                  </h1>
                </div>

                {/* Tabs */}
                <Tabs className="min-w-0 flex-1" value={activeTab} onValueChange={handleTabChange}>
                  <div className="overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
                    <TabsList className="h-10 w-max justify-start gap-1 rounded-md bg-slate-50 p-1" role="tablist" aria-label="主导航">
                      {tabs.map((tab) => {
                        const Icon = tab.icon;
                        return (
                          <TabsTrigger
                            key={tab.value}
                            value={tab.value}
                            className={`h-8 gap-2 rounded px-2.5 text-xs text-slate-600 shadow-none hover:bg-white hover:text-slate-950 data-[state=active]:bg-white data-[state=active]:text-blue-700 data-[state=active]:shadow-sm ${tab.activeClass}`}
                            role="tab"
                            aria-selected={activeTab === tab.value}
                          >
                            <Icon size={16} aria-hidden="true" />
                            <span>{tab.label}</span>
                          </TabsTrigger>
                        );
                      })}
                    </TabsList>
                  </div>
                </Tabs>

                <ExtensionHeaderStatusSlot onManage={handleManageExtension} />
                <GlobalConnectionStatus />
              </div>
            </header>

            {/* Main Content */}
            <main className="min-h-0 flex-1 overflow-y-auto overscroll-none pt-14" role="main" aria-label="主内容区域">
              <Suspense fallback={<PageLoadingFallback />}>
                {CorePage ? <CorePage /> : ExtensionPage ? <ExtensionPage /> : null}
              </Suspense>
            </main>
            <DesktopUpdateNotice onOpenSettings={() => handleTabChange("settings")} />
          </div>
        </FirstRunOnboarding>
      </ToastProvider>
    </DesktopUpdateProvider>
  );
}

export default App;
