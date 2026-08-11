import { useState } from "react";
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  DragEndEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  verticalListSortingStrategy,
  arrayMove,
} from "@dnd-kit/sortable";
import { ChevronDown, ChevronRight, Plus, Trash2, Eye, EyeOff, Info, Workflow } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import SortableCard from "./SortableCard";
import { PromptSectionData } from "../../types";
import { getSectionDisplayName } from "../../lib/agent-labels";

interface Props {
  sections: PromptSectionData[];
  onSectionsChange: (sections: PromptSectionData[]) => void;
  onSave?: (sections: PromptSectionData[]) => Promise<{ success: boolean }>;
}

const PLACEHOLDER_HELP = `可用占位符：
{{timestamp}} - 当前时间（ISO格式）
{{timezone}} - 时区信息
{{date}} - 当前日期（YYYY-MM-DD）
{{time}} - 当前时间（HH:MM:SS）
{{year}} - 年份
{{month}} - 月份
{{day}} - 日期
{{hour}} - 小时
{{minute}} - 分钟
{{second}} - 秒`;

export default function UserInjectionEditor({ sections, onSectionsChange, onSave }: Props) {
  const [expandedSection, setExpandedSection] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [editingName, setEditingName] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [showPlaceholderHelp, setShowPlaceholderHelp] = useState(false);
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);
  const [saveFeedback, setSaveFeedback] = useState<string | null>(null);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(KeyboardSensor)
  );

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const oldIdx = sections.findIndex((s) => s.name === active.id);
    const newIdx = sections.findIndex((s) => s.name === over.id);
    if (oldIdx === -1 || newIdx === -1) return;
    const reordered = arrayMove(sections, oldIdx, newIdx).map((s, i) => ({ ...s, order: i }));
    onSectionsChange(reordered);
  };

  const toggleEnabled = (name: string) => {
    onSectionsChange(
      sections.map((s) => (s.name === name ? { ...s, enabled: !s.enabled } : s))
    );
  };

  const toggleWorkflowOnly = (name: string) => {
    onSectionsChange(
      sections.map((s) => (s.name === name ? { ...s, workflow_only: !s.workflow_only } : s))
    );
  };

  const updateContent = (name: string, content: string) => {
    onSectionsChange(
      sections.map((s) => (s.name === name ? { ...s, content } : s))
    );
  };

  const addSection = () => {
    const newName = `injection_${Date.now()}`;
    onSectionsChange([
      ...sections,
      {
        name: newName,
        content: "",
        token_estimate: 0,
        cache_break: true,
        cache_break_reason: "动态内容",
        enabled: true,
        workflow_only: false,
        order: sections.length,
      },
    ]);
    setExpandedSection(newName);
  };

  const removeSection = (name: string) => {
    if (deleteConfirm === name) {
      onSectionsChange(sections.filter((s) => s.name !== name));
      setDeleteConfirm(null);
    } else {
      setDeleteConfirm(name);
      setTimeout(() => setDeleteConfirm((prev) => (prev === name ? null : prev)), 4000);
    }
  };

  const startEditingName = (name: string) => {
    setEditingName(name);
    setNewName(name);
  };

  const saveNewName = (oldName: string) => {
    if (!newName.trim() || newName === oldName) {
      setEditingName(null);
      return;
    }

    // 检查名称是否已存在
    if (sections.some((s) => s.name === newName && s.name !== oldName)) {
      setSaveFeedback("该名称已存在！");
      setTimeout(() => setSaveFeedback(null), 3000);
      return;
    }

    // 更新本地状态
    onSectionsChange(
      sections.map((s) => (s.name === oldName ? { ...s, name: newName } : s))
    );

    // 如果正在展开，更新展开状态
    if (expandedSection === oldName) {
      setExpandedSection(newName);
    }

    setEditingName(null);
  };

  const cancelEditingName = () => {
    setEditingName(null);
    setNewName("");
  };

  const handleSave = async () => {
    if (!onSave) return;
    setSaving(true);
    try {
      const result = await onSave(sections);
      if (result.success) {
        setSaveFeedback("保存成功");
        setTimeout(() => setSaveFeedback(null), 2000);
      }
    } catch (e) {
      console.error("Save failed:", e);
      setSaveFeedback("保存失败，请重试");
      setTimeout(() => setSaveFeedback(null), 3000);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-semibold text-slate-200">用户消息注入</h3>
          <Badge variant="outline" className="text-xs text-cyan-400 border-cyan-500/30">
            {sections.filter((s) => s.enabled).length}/{sections.length}
          </Badge>
          {saveFeedback && (
            <span
              role="status"
              aria-live="polite"
              className={`text-xs ${saveFeedback.includes("成功") ? "text-green-500" : "text-red-500"}`}
            >
              {saveFeedback}
            </span>
          )}
          <button
            type="button"
            onClick={() => setShowPlaceholderHelp(!showPlaceholderHelp)}
            aria-expanded={showPlaceholderHelp}
            aria-label="查看可用占位符"
            className="p-0.5 text-muted-foreground hover:text-foreground cursor-pointer focus-visible:ring-2 focus-visible:ring-indigo-500/30 focus-visible:outline-none"
          >
            <Info size={12} aria-hidden="true" />
          </button>
        </div>
        <div className="flex items-center gap-2">
          {onSave && (
            <button
              type="button"
              onClick={handleSave}
              disabled={saving}
              aria-label="保存注入 sections"
              className="flex items-center gap-1 px-2 py-1 min-h-[44px] text-xs rounded-md bg-indigo-500/15 text-indigo-500 hover:bg-indigo-500/25 transition-colors duration-200 cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed focus-visible:ring-2 focus-visible:ring-indigo-500/30 focus-visible:outline-none"
            >
              {saving ? "保存中..." : "保存"}
            </button>
          )}
          <button
            type="button"
            onClick={addSection}
            className="flex items-center gap-1 px-2 py-1 min-h-[44px] text-xs rounded-md bg-amber-500/15 text-amber-500 hover:bg-amber-500/25 transition-colors duration-200 cursor-pointer focus-visible:ring-2 focus-visible:ring-amber-500/30 focus-visible:outline-none"
          >
            <Plus size={12} aria-hidden="true" /> 新增
          </button>
        </div>
      </div>

      {/* 占位符帮助信息 */}
      {showPlaceholderHelp && (
        <div className="p-3 rounded-md bg-slate-800/60 border border-border/30 mb-3">
          <pre className="text-xs text-muted-foreground/70 whitespace-pre-wrap">
            {PLACEHOLDER_HELP}
          </pre>
        </div>
      )}

      <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
        <SortableContext items={sections.map((s) => s.name)} strategy={verticalListSortingStrategy}>
          <div className="space-y-2">
            {sections.map((section) => {
              const isExpanded = expandedSection === section.name;
              const wfActive = section.workflow_only && section.enabled;
              return (
                <SortableCard key={section.name} id={section.name}
                  className={wfActive ? "bg-violet-500/[0.06] border border-violet-500/20" : ""}>
                  <div className={`${!section.enabled ? "opacity-40" : ""} transition-opacity`}>
                    {/* Header */}
                    <div className="flex items-center gap-2">
                      <button
                        type="button"
                        onClick={() => setExpandedSection(isExpanded ? null : section.name)}
                        aria-expanded={isExpanded}
                        aria-label={`${isExpanded ? "折叠" : "展开"} ${getSectionDisplayName(section.name)}`}
                        className="p-1 min-w-[44px] min-h-[44px] flex items-center justify-center text-muted-foreground hover:text-foreground cursor-pointer transition-colors duration-200 focus-visible:ring-2 focus-visible:ring-cyan-500/30 focus-visible:outline-none"
                      >
                        {isExpanded ? <ChevronDown size={14} aria-hidden="true" /> : <ChevronRight size={14} aria-hidden="true" />}
                      </button>

                      {/* Section Name - 可编辑 */}
                      {editingName === section.name ? (
                        <input
                          type="text"
                          value={newName}
                          onChange={(e) => setNewName(e.target.value)}
                          onBlur={() => saveNewName(section.name)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") saveNewName(section.name);
                            if (e.key === "Escape") cancelEditingName();
                          }}
                          autoFocus
                          className="flex-1 text-xs font-medium bg-slate-800/60 border border-cyan-500/50 rounded px-1.5 py-0.5 text-slate-200 outline-none"
                        />
                      ) : (
                        <span
                          className="text-xs font-medium text-slate-200 flex-1 truncate cursor-pointer hover:text-cyan-400"
                          role="button"
                          tabIndex={0}
                          onClick={() => startEditingName(section.name)}
                          onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") startEditingName(section.name); }}
                          title="点击编辑名称"
                          aria-label={`编辑 ${getSectionDisplayName(section.name)} 名称`}
                        >
                          {getSectionDisplayName(section.name)}
                        </span>
                      )}

                      <Badge variant="outline" className="text-xs text-amber-500 border-amber-500/30 flex-shrink-0">
                        {section.token_estimate}t
                      </Badge>
                      <button
                        type="button"
                        onClick={() => toggleEnabled(section.name)}
                        aria-label={section.enabled ? `禁用 ${section.name}` : `启用 ${section.name}`}
                        className="p-1 min-w-[44px] min-h-[44px] flex items-center justify-center text-muted-foreground hover:text-foreground cursor-pointer transition-colors duration-200 focus-visible:ring-2 focus-visible:ring-cyan-500/30 focus-visible:outline-none"
                        title={section.enabled ? "禁用" : "启用"}
                      >
                        {section.enabled ? <Eye size={16} aria-hidden="true" /> : <EyeOff size={16} aria-hidden="true" />}
                      </button>
                      <button
                        type="button"
                        onClick={() => toggleWorkflowOnly(section.name)}
                        aria-label={section.workflow_only ? `${section.name}: 工作流专属` : `${section.name}: 通用`}
                        className={`p-1 min-w-[44px] min-h-[44px] flex items-center justify-center rounded cursor-pointer transition-colors duration-200 focus-visible:ring-2 focus-visible:ring-violet-500/30 focus-visible:outline-none ${section.workflow_only ? "text-violet-400 bg-violet-500/10 hover:bg-violet-500/20" : "text-muted-foreground hover:text-foreground hover:bg-muted/30"}`}
                        title={section.workflow_only ? "工作流专属（仅工作流中组装）" : "通用（所有场景组装）"}
                      >
                        <Workflow size={16} aria-hidden="true" />
                      </button>
                      <button
                        type="button"
                        onClick={() => removeSection(section.name)}
                        aria-label={deleteConfirm === section.name ? `确认删除 ${section.name}` : `删除 ${section.name}`}
                        className={`p-1 min-w-[44px] min-h-[44px] flex items-center justify-center rounded cursor-pointer transition-colors duration-200 focus-visible:ring-2 focus-visible:ring-red-500/30 focus-visible:outline-none ${
                          deleteConfirm === section.name
                            ? "text-red-500 bg-red-500/10"
                            : "text-red-500/60 hover:text-red-500"
                        }`}
                        title={deleteConfirm === section.name ? "再次点击确认删除" : "删除"}
                      >
                        {deleteConfirm === section.name ? (
                          <span className="text-xs font-medium">确认?</span>
                        ) : (
                          <Trash2 size={12} aria-hidden="true" />
                        )}
                      </button>
                    </div>

                    {/* Expanded Content */}
                    {isExpanded && (
                      <div className="mt-2">
                        {section.cache_break && section.cache_break_reason && (
                          <p className="text-xs text-amber-500/70 mb-1.5">
                            动态: {section.cache_break_reason}
                          </p>
                        )}
                        <textarea
                          value={section.content}
                          onChange={(e) => updateContent(section.name, e.target.value)}
                          aria-label={`${section.name} 注入内容`}
                          className="w-full min-h-[120px] bg-slate-800/60 border border-border/50 rounded-md px-2.5 py-2 text-xs text-slate-300 leading-relaxed resize-y outline-none focus:border-cyan-500/50 focus-visible:ring-2 focus-visible:ring-cyan-500/30 transition-colors"
                          placeholder="输入注入内容，支持占位符如 {{timestamp}}..."
                        />
                      </div>
                    )}
                  </div>
                </SortableCard>
              );
            })}
          </div>
        </SortableContext>
      </DndContext>
    </div>
  );
}