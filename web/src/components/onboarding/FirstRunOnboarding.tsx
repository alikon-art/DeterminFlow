import {
  ArrowLeft,
  ArrowRight,
  Bot,
  Boxes,
  Check,
  FileCheck2,
  Loader2,
  Play,
  Plug,
  RefreshCw,
  Route,
  ShieldAlert,
  Sparkles,
  Workflow,
} from "lucide-react";
import {
  type CSSProperties,
  type ReactNode,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { BRAND_MARK, PRODUCT_NAME } from "@/brand";
import { Switch } from "@/components/ui/switch";
import type { PluginRecord } from "@/extensions/plugin-types";
import type { ExtensionStatus } from "@/extensions/types";
import {
  fetchAgentDefinitions,
  fetchExtensions,
  fetchSessions,
  getModelProviders,
  getProviderSchemas,
} from "@/lib/api";
import { isDesktopRuntime } from "@/lib/desktop-update";
import { FirstRunModelScreen } from "./FirstRunModelScreen";
import { fetchPlugins, setPluginEnabled } from "@/lib/plugin-api";
import type { ModelProvider, ProviderSchema } from "@/types";
import {
  DESKTOP_ONBOARDING_PENDING_VALUE,
  getPluginChanges,
  normalizeApiError,
  requiresPluginRiskConfirmation,
  shouldStartFirstRun,
} from "./firstRunOnboardingModel";
import {
  completeDesktopOnboarding,
  ensureDesktopOnboardingStatus,
} from "./desktopOnboarding";
import "./first-run-onboarding.css";
import "./first-run-onboarding-support.css";

type ProviderMap = Record<string, Omit<ModelProvider, "id">>;

interface FirstRunData {
  providers: ProviderMap;
  schemas: Record<string, ProviderSchema>;
  extensions: ExtensionStatus[];
  plugins: PluginRecord[];
  pluginError: string;
  preferredModel: string | null;
  currentMainModel: string | null;
  currentMainSessionId: string | null;
}

interface OnboardingProps extends FirstRunData {
  onComplete: () => void | Promise<void>;
  completionError?: string;
}

interface WorkflowSceneProps {
  active: boolean;
  compact?: boolean;
  pluginCount?: number;
}

const STEP_LABELS = ["欢迎", "模型", "插件"] as const;

function readPreviewStep(): number | null {
  if (!import.meta.env.DEV) return null;
  const rawValue = new URLSearchParams(window.location.search).get("onboarding");
  if (rawValue === null) return null;
  const value = Number(rawValue);
  return Number.isInteger(value) && value >= 0 && value <= 2 ? value : 0;
}

function WorkflowScene({ active, compact = false, pluginCount = 0 }: WorkflowSceneProps) {
  return (
    <div
      className={`first-run-workflow-scene ${compact ? "is-compact" : ""} ${active ? "is-running" : ""}`}
      aria-hidden="true"
    >
      <div className="first-run-workflow-scene__grid" />
      <svg className="first-run-workflow-scene__links" viewBox="0 0 640 460" preserveAspectRatio="none">
        <path className="first-run-flow-link first-run-flow-link--one" d="M320 218 C260 178 214 139 150 112" />
        <path className="first-run-flow-link first-run-flow-link--two" d="M350 214 C430 170 472 153 525 120" />
        <path className="first-run-flow-link first-run-flow-link--three" d="M355 246 C435 282 478 302 530 347" />
        <path className="first-run-flow-link first-run-flow-link--four" d="M290 250 C240 302 207 322 138 356" />
        <path className="first-run-flow-link first-run-flow-link--return" d="M147 347 C255 411 425 411 523 352" />
      </svg>

      <div className="first-run-core-node">
        <span className="first-run-core-node__orbit first-run-core-node__orbit--outer" />
        <span className="first-run-core-node__orbit first-run-core-node__orbit--inner" />
        <span className="first-run-core-node__mark">
          <img src={BRAND_MARK} alt="" />
        </span>
        <strong>Main</strong>
        <small>协调执行</small>
      </div>

      <div className="first-run-mini-node first-run-mini-node--plan" style={{ "--node-delay": "0ms" } as CSSProperties}>
        <Route size={15} />
        <span><strong>拆解任务</strong><small>计划节点</small></span>
      </div>
      <div className="first-run-mini-node first-run-mini-node--agent" style={{ "--node-delay": "650ms" } as CSSProperties}>
        <Bot size={15} />
        <span><strong>调用 Agent</strong><small>独立上下文</small></span>
      </div>
      <div className="first-run-mini-node first-run-mini-node--verify" style={{ "--node-delay": "1300ms" } as CSSProperties}>
        <FileCheck2 size={15} />
        <span><strong>验证结果</strong><small>失败可恢复</small></span>
      </div>
      <div className="first-run-mini-node first-run-mini-node--result" style={{ "--node-delay": "1950ms" } as CSSProperties}>
        <Check size={15} />
        <span><strong>交付结果</strong><small>过程可审计</small></span>
      </div>

      {pluginCount > 0 ? (
        <div className="first-run-plugin-satellites">
          {Array.from({ length: Math.min(pluginCount, 3) }).map((_, index) => (
            <span key={index} style={{ "--satellite-index": index } as CSSProperties}>
              <Plug size={12} />
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function OnboardingChrome({
  step,
  furthestStep,
  onStepChange,
  onSkip,
  completing,
  completionError,
}: {
  step: number;
  furthestStep: number;
  onStepChange: (step: number) => void;
  onSkip: () => void | Promise<void>;
  completing: boolean;
  completionError: string;
}) {
  return (
    <header className="first-run-chrome">
      <div className="first-run-brand">
        <img src={BRAND_MARK} alt="" />
        <span>{PRODUCT_NAME}</span>
      </div>
      <nav className="first-run-progress" aria-label="首次运行进度">
        {STEP_LABELS.map((label, index) => (
          <button
            type="button"
            key={label}
            aria-current={step === index ? "step" : undefined}
            disabled={index > furthestStep}
            onClick={() => onStepChange(index)}
            className={step === index ? "is-current" : index < step ? "is-complete" : ""}
          >
            <span>{index < step ? <Check size={12} /> : index + 1}</span>
            {label}
          </button>
        ))}
      </nav>
      <div className="first-run-skip-area">
        {completionError ? <span role="alert">{completionError}</span> : null}
        <button
          type="button"
          className="first-run-skip"
          disabled={completing}
          onClick={() => void onSkip()}
        >
          {completing ? <Loader2 className="first-run-spinner" size={14} /> : null}
          {completing ? "正在保存" : "跳过引导"}
        </button>
      </div>
    </header>
  );
}

function WelcomeScreen({ active, offset, onNext }: { active: boolean; offset: number; onNext: () => void }) {
  return (
    <section
      className={`first-run-slide first-run-welcome ${active ? "is-active" : ""}`}
      aria-hidden={!active}
      {...(!active ? ({ inert: "" } as Record<string, string>) : {})}
      aria-labelledby="first-run-welcome-title"
      style={{ "--first-run-offset": offset } as CSSProperties}
    >
      <div className="first-run-slide__content first-run-welcome__layout">
        <div className="first-run-welcome__copy">
          <div className="first-run-kicker"><Sparkles size={15} />首次运行</div>
          <h1 id="first-run-welcome-title">让概率性的 AI，运行在确定的流程中。</h1>
          <p>把 Agent、工具与规则装进可恢复、可审计的 Workflow。先用两步完成必要配置。</p>
          <div className="first-run-welcome__actions">
            <button type="button" className="first-run-primary-button" onClick={onNext}>
              开始配置
              <ArrowRight size={17} />
            </button>
            <span>约 1 分钟</span>
          </div>
          <div className="first-run-promise-row" aria-label="产品能力">
            <span><Check size={13} />失败可恢复</span>
            <span><Check size={13} />过程可追踪</span>
            <span><Check size={13} />能力可扩展</span>
          </div>
        </div>
        <div className="first-run-welcome__visual">
          <WorkflowScene active={active} />
          <p className="sr-only">Main 将任务拆解为工作流节点，调用 Agent，验证并交付结果。</p>
        </div>
      </div>
    </section>
  );
}

function PluginScreen({
  active,
  offset,
  initialPlugins,
  initialError,
  requiredPluginId,
  onBack,
  onComplete,
}: {
  active: boolean;
  offset: number;
  initialPlugins: PluginRecord[];
  initialError: string;
  requiredPluginId: string | null;
  onBack: () => void;
  onComplete: () => void | Promise<void>;
}) {
  const [plugins, setPlugins] = useState(initialPlugins);
  const [selection, setSelection] = useState<Record<string, boolean>>(() => Object.fromEntries(
    initialPlugins.map((plugin) => [plugin.id, plugin.desired_enabled]),
  ));
  const [pluginError, setPluginError] = useState(initialError);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [restartRequired, setRestartRequired] = useState(initialPlugins.some((plugin) => plugin.restart_required));
  const [riskPlugin, setRiskPlugin] = useState<PluginRecord | null>(null);
  const riskConfirmRef = useRef<HTMLButtonElement>(null);

  const changes = useMemo(() => getPluginChanges(plugins, selection), [plugins, selection]);
  const selectedCount = Object.values(selection).filter(Boolean).length;

  useEffect(() => {
    if (!riskPlugin) return;
    riskConfirmRef.current?.focus();
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setRiskPlugin(null);
    };
    document.addEventListener("keydown", handleEscape);
    return () => document.removeEventListener("keydown", handleEscape);
  }, [riskPlugin]);

  useEffect(() => {
    if (!requiredPluginId) return;
    setSelection((current) => ({ ...current, [requiredPluginId]: true }));
    setSaved(false);
  }, [requiredPluginId]);

  const reloadPlugins = async () => {
    setLoading(true);
    setPluginError("");
    try {
      const result = await fetchPlugins();
      setPlugins(result.plugins);
      setSelection(Object.fromEntries(result.plugins.map((plugin) => [plugin.id, plugin.desired_enabled])));
      setRestartRequired(result.restart_required);
      setSaved(false);
    } catch (reason) {
      setPluginError(normalizeApiError(reason, "加载插件失败"));
    } finally {
      setLoading(false);
    }
  };

  const requestToggle = (plugin: PluginRecord, enabled: boolean) => {
    if (requiresPluginRiskConfirmation(plugin, enabled)) {
      setRiskPlugin(plugin);
      return;
    }
    setSelection((current) => ({ ...current, [plugin.id]: enabled }));
    setSaved(false);
  };

  const saveSelection = async () => {
    if (changes.length === 0) {
      await onComplete();
      return;
    }
    setSaving(true);
    setPluginError("");
    let nextRestartRequired = restartRequired;
    try {
      for (const change of changes) {
        const result = await setPluginEnabled(change.id, change.enabled);
        nextRestartRequired = nextRestartRequired || result.restart_required;
      }
      const refreshed = await fetchPlugins();
      setPlugins(refreshed.plugins);
      setSelection(Object.fromEntries(refreshed.plugins.map((plugin) => [plugin.id, plugin.desired_enabled])));
      setRestartRequired(nextRestartRequired || refreshed.restart_required);
      setSaved(true);
    } catch (reason) {
      setPluginError(normalizeApiError(reason, "保存插件选择失败"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <section
      className={`first-run-slide first-run-plugins ${active ? "is-active" : ""}`}
      aria-hidden={!active}
      {...(!active ? ({ inert: "" } as Record<string, string>) : {})}
      aria-labelledby="first-run-plugins-title"
      style={{ "--first-run-offset": offset } as CSSProperties}
    >
      <div className="first-run-slide__content first-run-plugins__layout">
        <div className="first-run-section-heading">
          <span>03 / 03</span>
          <h2 id="first-run-plugins-title">选择启用的插件</h2>
          <p>只选择现在需要的能力，以后可以随时在“插件”中调整。</p>
        </div>

        <div className="first-run-plugins__workspace">
          <div className="first-run-plugin-selector">
            <div className="first-run-plugin-selector__summary">
              <span>{selectedCount} / {plugins.length} 已选择</span>
              {restartRequired || changes.length > 0 ? <strong>重启后生效</strong> : null}
            </div>

            {plugins.length > 0 ? (
              <div className="first-run-plugin-list">
                {plugins.map((plugin, index) => {
                  const checked = selection[plugin.id] ?? plugin.desired_enabled;
                  const thirdParty = plugin.source.trust === "third_party";
                  const requiredByModel = plugin.id === requiredPluginId;
                  return (
                    <div
                      className={`first-run-plugin-row ${checked ? "is-selected" : ""}`}
                      key={plugin.id}
                      style={{ "--item-index": index } as CSSProperties}
                    >
                      <span className="first-run-plugin-row__icon"><Boxes size={17} /></span>
                      <span className="first-run-plugin-row__copy">
                        <span>
                          <strong>{plugin.name}</strong>
                          {thirdParty ? <em>第三方</em> : null}
                          {requiredByModel ? <em className="is-required">模型正在使用</em> : null}
                        </span>
                        <small>{plugin.description || plugin.id}</small>
                      </span>
                      <Switch
                        checked={checked}
                        onCheckedChange={(enabled) => requestToggle(plugin, enabled)}
                        disabled={saving || requiredByModel}
                        aria-label={requiredByModel ? `${plugin.name}，模型正在使用` : `${checked ? "停用" : "启用"}${plugin.name}`}
                        className="first-run-plugin-switch"
                      />
                    </div>
                  );
                })}
              </div>
            ) : (
              <div className="first-run-plugin-empty">
                <Plug size={24} />
                <strong>{pluginError ? "插件暂时不可用" : "尚未安装插件"}</strong>
                <p>{pluginError || "可以先进入 DeterminFlow，稍后从插件目录添加。"}</p>
                {pluginError ? (
                  <button type="button" onClick={() => void reloadPlugins()} disabled={loading}>
                    <RefreshCw className={loading ? "first-run-spinner" : ""} size={15} />重试
                  </button>
                ) : null}
              </div>
            )}
            {pluginError && plugins.length > 0 ? <p className="first-run-error" role="alert">{pluginError}</p> : null}
          </div>

          <div className="first-run-plugins__visual">
            <WorkflowScene active={active} compact pluginCount={selectedCount} />
            <div className="first-run-plugin-flow-caption">
              <span><Workflow size={15} />Core Workflow</span>
              <ArrowRight size={14} />
              <span><Plug size={15} />插件能力</span>
            </div>
          </div>
        </div>

        <div className="first-run-slide-actions">
          <button type="button" className="first-run-secondary-button" onClick={onBack}>
            <ArrowLeft size={16} />上一步
          </button>
          <div className="first-run-plugin-actions">
            {saved ? <span className="first-run-save-confirmation"><Check size={14} />选择已保存</span> : null}
            <button
              type="button"
              className="first-run-primary-button"
              disabled={saving || loading}
              onClick={saved ? () => void onComplete() : () => void saveSelection()}
            >
              {saving ? <Loader2 className="first-run-spinner" size={16} /> : saved || changes.length === 0 ? <Play size={16} /> : null}
              {saving ? "正在保存" : saved || changes.length === 0 ? `进入 ${PRODUCT_NAME}` : "保存插件选择"}
              {!saving && changes.length > 0 && !saved ? <ArrowRight size={16} /> : null}
            </button>
          </div>
        </div>
      </div>

      {riskPlugin ? (
        <div className="first-run-risk-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setRiskPlugin(null); }}>
          <div role="dialog" aria-modal="true" aria-labelledby="first-run-risk-title" className="first-run-risk-dialog">
            <span className="first-run-risk-dialog__icon"><ShieldAlert size={21} /></span>
            <div>
              <small>第三方插件</small>
              <h3 id="first-run-risk-title">启用 {riskPlugin.name}？</h3>
              <p>该插件与 DeterminFlow 主进程以相同权限运行，可访问本机资源。平台不提供沙箱隔离。</p>
            </div>
            <div className="first-run-risk-dialog__actions">
              <button type="button" className="first-run-secondary-button" onClick={() => setRiskPlugin(null)}>取消</button>
              <button
                ref={riskConfirmRef}
                type="button"
                className="first-run-risk-confirm"
                onClick={() => {
                  setSelection((current) => ({ ...current, [riskPlugin.id]: true }));
                  setRiskPlugin(null);
                  setSaved(false);
                }}
              >
                我了解风险，确认启用
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}

function FirstRunExperience({
  providers,
  schemas,
  extensions,
  plugins,
  pluginError,
  preferredModel,
  currentMainModel,
  currentMainSessionId,
  onComplete,
  initialStep = 0,
  completing = false,
  completionError = "",
}: OnboardingProps & { initialStep?: number; completing?: boolean; completionError?: string }) {
  const [step, setStep] = useState(initialStep);
  const [furthestStep, setFurthestStep] = useState(initialStep);
  const [requiredPluginId, setRequiredPluginId] = useState<string | null>(() => {
    const providerId = currentMainModel?.split(":", 1)[0];
    return providerId ? providers[providerId]?.managed_by || null : null;
  });
  const rootRef = useRef<HTMLDivElement>(null);

  const goTo = (nextStep: number) => {
    const bounded = Math.max(0, Math.min(2, nextStep));
    setStep(bounded);
    setFurthestStep((current) => Math.max(current, bounded));
  };

  useEffect(() => {
    rootRef.current?.focus();
  }, [step]);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const editing = target?.matches("input, select, textarea, button, [contenteditable='true']");
      if (editing) return;
      if (event.key === "ArrowLeft" && step > 0) goTo(step - 1);
      if (event.key === "ArrowRight" && step === 0) goTo(1);
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [step]);

  return (
    <div
      ref={rootRef}
      className="first-run-onboarding"
      role="dialog"
      aria-modal="true"
      aria-label="DeterminFlow 首次运行引导"
      tabIndex={-1}
    >
      <OnboardingChrome
        step={step}
        furthestStep={furthestStep}
        onStepChange={goTo}
        onSkip={onComplete}
        completing={completing}
        completionError={completionError}
      />
      {completionError ? (
        <p className="first-run-completion-error" role="alert">{completionError}</p>
      ) : null}
      <div className="first-run-track">
        <WelcomeScreen active={step === 0} offset={0 - step} onNext={() => goTo(1)} />
        <FirstRunModelScreen
          active={step === 1}
          offset={1 - step}
          providers={providers}
          schemas={schemas}
          extensions={extensions}
          preferredModel={preferredModel}
          currentMainModel={currentMainModel}
          currentMainSessionId={currentMainSessionId}
          onBack={() => goTo(0)}
          onNext={() => goTo(2)}
          onManagedProviderChange={setRequiredPluginId}
        />
        <PluginScreen
          active={step === 2}
          offset={2 - step}
          initialPlugins={plugins}
          initialError={pluginError}
          requiredPluginId={requiredPluginId}
          onBack={() => goTo(1)}
          onComplete={onComplete}
        />
      </div>
    </div>
  );
}

function FirstRunBootstrap() {
  return (
    <div className="first-run-bootstrap" role="status" aria-label="正在准备 DeterminFlow">
      <span><img src={BRAND_MARK} alt="" /></span>
      <strong>{PRODUCT_NAME}</strong>
    </div>
  );
}

export default function FirstRunOnboarding({ children }: { children: ReactNode }) {
  const [desktopRuntime] = useState(isDesktopRuntime);
  const [previewStep] = useState(readPreviewStep);
  const [mode, setMode] = useState<"checking" | "onboarding" | "app">(() => (
    desktopRuntime || previewStep !== null ? "checking" : "app"
  ));
  const [data, setData] = useState<FirstRunData | null>(null);
  const [completing, setCompleting] = useState(false);
  const [completionError, setCompletionError] = useState("");

  useEffect(() => {
    if (mode !== "checking") return;
    let active = true;

    async function loadFirstRunState() {
      try {
        let onboardingStatus: string | null = null;
        if (desktopRuntime && previewStep === null) {
          onboardingStatus = await ensureDesktopOnboardingStatus().catch(
            () => DESKTOP_ONBOARDING_PENDING_VALUE,
          );
        }
        if (!active) return;
        if (!shouldStartFirstRun({
          desktopRuntime,
          onboardingStatus,
          previewRequested: previewStep !== null,
        })) {
          setMode("app");
          return;
        }

        const [providerResult, schemaResult, agentResult, sessionResult, extensionResult, pluginResult] = await Promise.all([
          getModelProviders(),
          getProviderSchemas(),
          fetchAgentDefinitions().catch(() => ({ agent_types: [] })),
          fetchSessions().catch(() => ({ sessions: [], active_sub_count: 0, main_session_id: null })),
          fetchExtensions().catch(() => ({ extensions: [], enabled: [] })),
          fetchPlugins()
            .then((value) => ({ value, error: "" }))
            .catch((reason) => ({
              value: { plugins: [], restart_required: false, package_management_read_only: false },
              error: normalizeApiError(reason, "加载插件失败"),
            })),
        ]);
        if (!active) return;
        const mainModel = agentResult.agent_types.find((agent) => agent.agent_type === "main")?.model;
        setData({
          providers: providerResult.providers,
          schemas: schemaResult.schemas,
          extensions: extensionResult.extensions,
          plugins: pluginResult.value.plugins,
          pluginError: pluginResult.error,
          preferredModel: mainModel || providerResult.default_model,
          currentMainModel: mainModel || null,
          currentMainSessionId: sessionResult.main_session_id,
        });
        setMode("onboarding");
      } catch {
        if (active) setMode("app");
      }
    }

    void loadFirstRunState();
    return () => {
      active = false;
    };
  }, [desktopRuntime, mode, previewStep]);

  const complete = async () => {
    if (completing) return;
    setCompleting(true);
    setCompletionError("");
    try {
      await completeDesktopOnboarding({
        desktopRuntime,
        previewRequested: previewStep !== null,
        showApp: () => setMode("app"),
      });
    } catch (reason) {
      setCompletionError(normalizeApiError(reason, "无法保存引导状态，请重试"));
    } finally {
      setCompleting(false);
    }
  };

  if (mode === "app") return children;
  if (mode === "checking" || !data) return <FirstRunBootstrap />;
  return (
    <FirstRunExperience
      {...data}
      initialStep={previewStep ?? 0}
      onComplete={complete}
      completing={completing}
      completionError={completionError}
    />
  );
}
