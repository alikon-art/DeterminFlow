import { useState, useCallback, useRef, useEffect } from "react";
import { FileText, Bot, Wrench, RefreshCw, GripVertical, User, Plus, Trash2 } from "lucide-react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useOrchestration } from "../hooks/useOrchestration";
import PromptSectionEditor from "../components/orchestration/PromptSectionEditor";
import AgentDefinitionEditor from "../components/orchestration/AgentDefinitionEditor";
import ToolListViewer from "../components/orchestration/ToolListViewer";
import UserInjectionEditor from "../components/orchestration/UserInjectionEditor";
import PreviewPanel from "../components/orchestration/PreviewPanel";
import { OrchestrationSubTab } from "../types";
import { getAgentDisplayName } from "../lib/agent-labels";

const SUB_TABS: { key: OrchestrationSubTab; label: string; icon: typeof FileText }[] = [
  { key: "prompts", label: "提示词片段", icon: FileText },
  { key: "agents", label: "代理定义", icon: Bot },
  { key: "tools", label: "工具列表", icon: Wrench },
  { key: "user-injection", label: "用户消息注入", icon: User },
];

export default function OrchestrationPage() {
  const {
    availableTemplates,
    sections, setSections,
    templateVariables, setTemplateVariables,
    userInjectionSections, setUserInjectionSections,
    promptTarget, setPromptTarget,
    agents, setAgents,
    tools, toolGroups,
    skillGroups, ruleGroups,
    activeTab, setActiveTab,
    selectedAgentType, setSelectedAgentType,
    loading, reload,
    saveSections,
    saveAgent,
    saveUserInjectionSections,
    saveTemplateVariables,
    deleteTemplate,
    defaultModelParams,
  } = useOrchestration();

  // Resizable split pane state
  const containerRef = useRef<HTMLDivElement>(null);
  const [leftRatio, setLeftRatio] = useState(0.6);
  const dragging = useRef(false);

  // Right-click context menu state for template buttons
  const [contextMenu, setContextMenu] = useState<{
    show: boolean;
    x: number;
    y: number;
    templateName: string;
  }>({ show: false, x: 0, y: 0, templateName: "" });
  const [deleteConfirmTmpl, setDeleteConfirmTmpl] = useState<string | null>(null);
  const [deleteFeedback, setDeleteFeedback] = useState<string | null>(null);

  const onMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    dragging.current = true;
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  }, []);

  const onMouseMove = useCallback((e: MouseEvent) => {
    if (!dragging.current || !containerRef.current) return;
    const rect = containerRef.current.getBoundingClientRect();
    const rawRatio = (e.clientX - rect.left) / rect.width;
    setLeftRatio(Math.max(0.25, Math.min(0.75, rawRatio)));
  }, []);

  const onMouseUp = useCallback(() => {
    dragging.current = false;
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
  }, []);

  useEffect(() => {
    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", onMouseUp);
    return () => {
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", onMouseUp);
    };
  }, [onMouseMove, onMouseUp]);

  // Close context menu on outside click / escape
  const closeContextMenu = useCallback(() => {
    setContextMenu((prev) => ({ ...prev, show: false }));
  }, []);

  useEffect(() => {
    if (!contextMenu.show) return;
    const handleClick = () => closeContextMenu();
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeContextMenu();
    };
    // Delay adding listener to avoid immediately closing
    const timer = setTimeout(() => {
      document.addEventListener("click", handleClick);
      document.addEventListener("keydown", handleEscape);
    }, 0);
    return () => {
      clearTimeout(timer);
      document.removeEventListener("click", handleClick);
      document.removeEventListener("keydown", handleEscape);
    };
  }, [contextMenu.show, closeContextMenu]);

  const handleTemplateContextMenu = (e: React.MouseEvent, tmpl: string) => {
    e.preventDefault();
    setContextMenu({ show: true, x: e.clientX, y: e.clientY, templateName: tmpl });
  };

  const handleDeleteTemplate = async () => {
    const tmpl = contextMenu.templateName;
    closeContextMenu();
    if (!tmpl) return;
    // Two-click confirmation: first click sets confirm state, second click deletes
    if (deleteConfirmTmpl !== tmpl) {
      setDeleteConfirmTmpl(tmpl);
      setTimeout(() => setDeleteConfirmTmpl((prev) => (prev === tmpl ? null : prev)), 4000);
      return;
    }
    setDeleteConfirmTmpl(null);
    const result = await deleteTemplate(tmpl);
    if (!result.success) {
      setDeleteFeedback(`删除失败: ${result.error || "未知错误"}`);
      setTimeout(() => setDeleteFeedback(null), 4000);
    } else {
      setDeleteFeedback("已删除");
      setTimeout(() => setDeleteFeedback(null), 2000);
    }
  };

  if (loading) {
    return (
      <div className="h-[calc(100dvh-3.5rem)] flex items-center justify-center" role="status" aria-label="加载编排数据">
        <div className="flex items-center gap-2 text-muted-foreground animate-pulse motion-reduce:animate-none">
          <RefreshCw size={16} className="animate-spin motion-reduce:animate-none" aria-hidden="true" />
          <span className="sr-only">加载编排数据中...</span>
          加载编排数据...
        </div>
      </div>
    );
  }

  return (
    <div ref={containerRef} className="h-[calc(100dvh-3.5rem)] flex" role="main" aria-label="代理编排配置">
      {/* Left: Editor Panel */}
      <div className="flex flex-col min-w-0 border-r border-border/30" style={{ width: `${leftRatio * 100}%` }}>
        {/* Sub Tabs */}
        <div className="px-4 py-2.5 border-b border-border/50 flex items-center gap-2">
          <div className="flex gap-1 flex-1" role="tablist" aria-label="编排子页面">
            {SUB_TABS.map(({ key, label, icon: Icon }) => (
              <button
                key={key}
                type="button"
                onClick={() => setActiveTab(key)}
                role="tab"
                aria-selected={activeTab === key}
                aria-label={label}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium transition-colors duration-200 cursor-pointer focus-visible:ring-2 focus-visible:ring-amber-500/30 focus-visible:outline-none ${
                  activeTab === key
                    ? "bg-amber-500/15 text-amber-500 border border-amber-500/30"
                    : "text-muted-foreground hover:text-foreground hover:bg-slate-800/60"
                }`}
              >
                <Icon size={13} aria-hidden="true" />
                {label}
              </button>
            ))}
          </div>
          <button
            type="button"
            onClick={reload}
            aria-label="重新加载数据"
            className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-slate-800/60 transition-colors duration-200 cursor-pointer focus-visible:ring-2 focus-visible:ring-amber-500/30 focus-visible:outline-none"
            title="重新加载数据"
          >
            <RefreshCw size={14} aria-hidden="true" />
          </button>
        </div>

        {/* Editor Content */}
        <ScrollArea className="flex-1">
          <div className="p-4">
            {activeTab === "prompts" && (
              <div className="space-y-3">
                <div className="flex gap-1 rounded-lg bg-slate-800/50 p-1 border border-border/40 w-fit flex-wrap">
                  {availableTemplates.map((tmpl) => (
                    <button
                      key={tmpl}
                      type="button"
                      onClick={() => setPromptTarget(tmpl)}
                      onContextMenu={(e) => handleTemplateContextMenu(e, tmpl)}
                      aria-label={`选择模板 ${getAgentDisplayName(tmpl)}`}
                      aria-pressed={promptTarget === tmpl}
                      className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors duration-200 cursor-pointer focus-visible:ring-2 focus-visible:ring-indigo-500/30 focus-visible:outline-none ${
                        promptTarget === tmpl
                          ? "bg-indigo-500/20 text-indigo-500"
                          : "text-muted-foreground hover:text-foreground"
                      }`}
                    >
                      {getAgentDisplayName(tmpl)}
                    </button>
                  ))}
                </div>
                {/* Template Variables Editor */}
                {promptTarget !== "main" && promptTarget !== "subagent" && promptTarget !== "compressor" && (
                  <TemplateVariablesEditor
                    variables={templateVariables}
                    onVariablesChange={setTemplateVariables}
                    onSave={(vars) => saveTemplateVariables(vars, promptTarget)}
                  />
                )}
                <PromptSectionEditor
                  sections={sections}
                  onSectionsChange={setSections}
                  onSave={(items) => saveSections(items, promptTarget)}
                  promptTarget={promptTarget}
                />
              </div>
            )}
            {activeTab === "agents" && (
              <AgentDefinitionEditor
                agents={agents}
                allTools={tools}
                groups={toolGroups}
                onAgentsChange={setAgents}
                selectedAgentType={selectedAgentType}
                onSelectAgent={setSelectedAgentType}
                skillGroups={skillGroups}
                ruleGroups={ruleGroups}
                availableTemplates={availableTemplates}
                onSave={saveAgent}
                defaultModelParams={defaultModelParams}
              />
            )}
            {activeTab === "tools" && (
              <ToolListViewer tools={tools} groups={toolGroups} agents={agents} onReload={reload} />
            )}
            {activeTab === "user-injection" && (
              <UserInjectionEditor
                sections={userInjectionSections}
                onSectionsChange={setUserInjectionSections}
                onSave={saveUserInjectionSections}
              />
            )}
          </div>
        </ScrollArea>
      </div>

      {/* Drag handle */}
      <div
        role="separator"
        aria-orientation="vertical"
        aria-label="拖拽调整面板宽度"
        tabIndex={0}
        onMouseDown={onMouseDown}
        onKeyDown={(e) => {
          if (e.key === "ArrowLeft") setLeftRatio((r) => Math.max(0.25, r - 0.05));
          if (e.key === "ArrowRight") setLeftRatio((r) => Math.min(0.75, r + 0.05));
        }}
        className="w-2 flex-shrink-0 cursor-col-resize hover:bg-indigo-500/30 active:bg-indigo-500/50 bg-transparent transition-colors flex items-center justify-center group border-x border-transparent hover:border-indigo-500/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/30"
      >
        <GripVertical size={10} aria-hidden="true" className="text-muted-foreground/40 group-hover:text-indigo-500/80 transition-colors" />
      </div>

      {/* Right: Preview Panel */}
      <div className="min-w-0 flex-1">
        <PreviewPanel
          activeTab={activeTab}
          sections={sections}
          agents={agents}
          tools={tools}
          groups={toolGroups}
          selectedAgentType={selectedAgentType}
          skillGroups={skillGroups}
          ruleGroups={ruleGroups}
          templateVariables={templateVariables}
        />
      </div>

      {/* Right-click context menu for template list */}
      {contextMenu.show && (
        <div
          role="menu"
          aria-label="模板操作"
          className="fixed z-50 min-w-[140px] rounded-lg bg-slate-900 border border-indigo-500/20 shadow-2xl shadow-black/40 overflow-hidden"
          style={{ left: contextMenu.x, top: contextMenu.y }}
        >
          <button
            type="button"
            role="menuitem"
            onClick={handleDeleteTemplate}
            className={`flex items-center gap-2.5 w-full px-3 py-2 text-xs transition-colors cursor-pointer ${
              deleteConfirmTmpl === contextMenu.templateName
                ? "text-red-500 bg-red-500/10"
                : "text-red-500 hover:bg-red-500/10"
            }`}
          >
            <Trash2 size={14} aria-hidden="true" />
            <span>{deleteConfirmTmpl === contextMenu.templateName ? "再次点击确认删除" : "删除模板"}</span>
          </button>
        </div>
      )}

      {/* Delete feedback toast */}
      {deleteFeedback && (
        <div
          role="status"
          aria-live="polite"
          className="fixed bottom-4 right-4 z-50 px-3 py-2 rounded-lg bg-slate-800 border border-border/50 text-xs text-slate-200 shadow-lg"
        >
          {deleteFeedback}
        </div>
      )}
    </div>
  );
}


// ============ Template Variables Editor ============

interface TemplateVarDef {
  key: string;
  name: string;
  description: string;
  default: string;
  required: boolean;
}

interface TemplateVariablesEditorProps {
  variables: TemplateVarDef[];
  onVariablesChange: (vars: TemplateVarDef[]) => void;
  onSave: (vars: TemplateVarDef[]) => void;
}

function TemplateVariablesEditor({ variables, onVariablesChange, onSave }: TemplateVariablesEditorProps) {
  const [editing, setEditing] = useState<string | null>(null);

  const handleSave = () => {
    onSave(variables);
  };

  const handleAdd = () => {
    const key = `var_${Date.now().toString(36)}`;
    const newVarDef: TemplateVarDef = {
      key, name: "新变量块", description: "", default: "", required: false,
    };
    setEditing(key);
    onVariablesChange([...variables, newVarDef]);
  };

  const handleRemove = (key: string) => {
    onVariablesChange(variables.filter((v) => v.key !== key));
  };

  const handleUpdate = (key: string, field: keyof TemplateVarDef, value: string | boolean) => {
    onVariablesChange(variables.map((v) => (v.key === key ? { ...v, [field]: value } : v)));
  };

  return (
    <div className="rounded-lg border border-border/40 bg-slate-800/30 p-3" role="region" aria-label="自定义变量块">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-xs font-medium text-muted-foreground">自定义变量块</h3>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={handleAdd}
            className="p-1 min-w-[44px] min-h-[44px] flex items-center justify-center rounded text-muted-foreground hover:text-amber-500 hover:bg-amber-500/10 transition-colors duration-200 cursor-pointer focus-visible:ring-2 focus-visible:ring-amber-500/30 focus-visible:outline-none"
            title="新增变量块"
            aria-label="新增变量块"
          >
            <Plus size={12} aria-hidden="true" />
          </button>
          <button
            type="button"
            onClick={handleSave}
            className="px-2 py-1 min-h-[44px] rounded text-xs font-medium bg-indigo-500/15 text-indigo-500 hover:bg-indigo-500/25 transition-colors duration-200 cursor-pointer focus-visible:ring-2 focus-visible:ring-indigo-500/30 focus-visible:outline-none"
          >
            保存变量块
          </button>
        </div>
      </div>
      {variables.length === 0 ? (
        <p className="text-xs text-muted-foreground">暂无自定义变量块，点击 + 新增</p>
      ) : (
        <div className="space-y-2">
          {variables.map((v) => (
            <div key={v.key} className="flex items-start gap-2 p-2 rounded bg-slate-900/50 border border-border/20">
              <div className="flex-1 space-y-1">
                <div className="flex items-center gap-1">
                  <code className="px-1 py-0.5 rounded bg-indigo-500/10 text-indigo-500 text-xs font-mono">
                    {`{{${v.key}}}`}
                  </code>
                  {editing === v.key ? (
                    <input
                      value={v.name}
                      onChange={(e) => handleUpdate(v.key, "name", e.target.value)}
                      className="flex-1 px-1.5 py-0.5 min-h-[44px] bg-slate-900 border border-border/30 rounded text-xs text-foreground outline-none focus:border-indigo-500/40"
                      placeholder="变量名"
                      aria-label="变量名"
                    />
                  ) : (
                    <span
                      className="text-xs text-foreground cursor-pointer hover:text-indigo-500"
                      role="button"
                      tabIndex={0}
                      onClick={() => setEditing(v.key)}
                      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") setEditing(v.key); }}
                      aria-label={`编辑变量 ${v.name}`}
                    >
                      {v.name}
                    </span>
                  )}
                </div>
                {editing === v.key && (
                  <div className="space-y-1 pl-4">
                    <div>
                      <label htmlFor={`tvar-key-${v.key}`} className="text-xs text-muted-foreground">Key</label>
                      <input
                        id={`tvar-key-${v.key}`}
                        value={v.key}
                        onChange={(e) => handleUpdate(v.key, "key", e.target.value)}
                        className="w-full px-1.5 py-0.5 min-h-[44px] bg-slate-900 border border-border/30 rounded text-xs text-foreground outline-none focus:border-indigo-500/40 font-mono"
                      />
                    </div>
                    <div>
                      <label htmlFor={`tvar-desc-${v.key}`} className="text-xs text-muted-foreground">描述</label>
                      <textarea
                        id={`tvar-desc-${v.key}`}
                        value={v.description}
                        onChange={(e) => handleUpdate(v.key, "description", e.target.value)}
                        rows={2}
                        className="w-full px-1.5 py-0.5 min-h-[56px] bg-slate-900 border border-border/30 rounded text-xs text-foreground outline-none focus:border-indigo-500/40 resize-none"
                      />
                    </div>
                    <div>
                      <label htmlFor={`tvar-default-${v.key}`} className="text-xs text-muted-foreground">默认值</label>
                      <input
                        id={`tvar-default-${v.key}`}
                        value={v.default}
                        onChange={(e) => handleUpdate(v.key, "default", e.target.value)}
                        className="w-full px-1.5 py-0.5 min-h-[44px] bg-slate-900 border border-border/30 rounded text-xs text-foreground outline-none focus:border-indigo-500/40"
                      />
                    </div>
                    <div className="flex items-center gap-1.5 min-h-[44px]">
                      <input
                        id={`tvar-required-${v.key}`}
                        type="checkbox"
                        checked={v.required}
                        onChange={(e) => handleUpdate(v.key, "required", e.target.checked)}
                        className="w-4 h-4 rounded border-border/30 bg-slate-900 text-amber-500 focus:ring-amber-500/30 cursor-pointer"
                      />
                      <label htmlFor={`tvar-required-${v.key}`} className="text-xs text-muted-foreground cursor-pointer">必填</label>
                    </div>
                  </div>
                )}
              </div>
              <button
                type="button"
                onClick={() => {
                  setEditing(editing === v.key ? null : v.key);
                }}
                className="p-1 min-w-[44px] min-h-[44px] flex items-center justify-center text-muted-foreground hover:text-indigo-500 transition-colors duration-200 cursor-pointer focus-visible:ring-2 focus-visible:ring-indigo-500/30 focus-visible:outline-none"
                title={editing === v.key ? "收起" : "编辑"}
                aria-label={editing === v.key ? "收起编辑" : "编辑变量"}
              >
                {editing === v.key ? (
                  <span className="text-xs">收起</span>
                ) : (
                  <span className="text-xs">编辑</span>
                )}
              </button>
              <button
                type="button"
                onClick={() => handleRemove(v.key)}
                className="p-1 min-w-[44px] min-h-[44px] flex items-center justify-center text-muted-foreground hover:text-red-400 transition-colors duration-200 cursor-pointer focus-visible:ring-2 focus-visible:ring-red-500/30 focus-visible:outline-none"
                title="删除"
                aria-label={`删除变量 ${v.name}`}
              >
                <Trash2 size={12} aria-hidden="true" />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
