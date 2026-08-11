import { useMemo } from "react";
import { FileText, Bot, Wrench, Hash, Zap, Eye, EyeOff, AlertCircle, BookOpen } from "lucide-react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Badge } from "@/components/ui/badge";
import { DEFAULT_MAX_CONTEXT_TOKENS } from "../../lib/modelDefaults";
import {
  PromptSectionData,
  AgentDefinitionData,
  ToolInfo,
  ToolGroup,
  OrchestrationSubTab,
  SkillGroup,
  RuleGroup,
  TemplateVariable,
} from "../../types";
import { getAgentDisplayName, getSectionDisplayName } from "../../lib/agent-labels";

const PLACEHOLDER_RE = /\{\{([\w-]+)\}\}/g;

interface Props {
  activeTab: OrchestrationSubTab;
  sections: PromptSectionData[];
  agents: AgentDefinitionData[];
  tools: ToolInfo[];
  groups: ToolGroup[];
  selectedAgentType: string | null;
  skillGroups: SkillGroup[];
  ruleGroups: RuleGroup[];
  templateVariables?: TemplateVariable[];
}

function estimateTokens(text: string): number {
  if (!text) return 0;
  let cn = 0;
  for (const c of text) {
    if (c >= "\u4e00" && c <= "\u9fff") cn++;
  }
  const other = text.length - cn;
  return Math.floor(cn / 1.5) + Math.floor(other / 4) + 1;
}

const SEC_COLORS = [
  "bg-indigo-500/[0.06] border border-indigo-500/20",
  "bg-purple-500/[0.06] border border-purple-500/20",
  "bg-cyan-500/[0.06] border border-cyan-500/20",
  "bg-green-500/[0.06] border border-green-500/20",
  "bg-amber-500/[0.06] border border-amber-500/20",
];

// 前端预定义调色板（按 group_id 映射）
const GROUP_BG_COLORS: Record<string, string> = {
  memory: "bg-purple-500",
  coding: "bg-green-500",
  session_main: "bg-indigo-500",
  communication: "bg-cyan-500",
  config: "bg-amber-500",
  skills: "bg-pink-500",
};
const DEFAULT_BG_COLOR = "bg-muted-foreground";

function getBgColor(groupId: string): string {
  return GROUP_BG_COLORS[groupId] || DEFAULT_BG_COLOR;
}

export default function PreviewPanel({
  activeTab, sections, agents, tools, groups, selectedAgentType,
  skillGroups, ruleGroups, templateVariables = [],
}: Props) {
  // === Prompt Preview ===
  const enabledSections = useMemo(() => sections.filter((s) => s.enabled), [sections]);
  const effectivePrompt = useMemo(() => enabledSections.map((s) => s.content).join("\n\n"), [enabledSections]);
  const totalTokens = useMemo(() => estimateTokens(effectivePrompt), [effectivePrompt]);

  // === Agent Preview ===
  const selectedAgent = useMemo(
    () => agents.find((a) => a.agent_type === selectedAgentType),
    [agents, selectedAgentType]
  );

  // 修复：对所有工具进行名称匹配，不再限制 source
  const agentToolList = useMemo(() => {
    if (!selectedAgent) return [];
    if (!selectedAgent.tools) return [];
    if (selectedAgent.tools.includes("*")) {
      return tools.filter((t) => !selectedAgent.disallowed_tools?.includes(t.name));
    }
    const allowed = new Set(selectedAgent.tools);
    return tools.filter((t) => allowed.has(t.name) && !selectedAgent.disallowed_tools?.includes(t.name));
  }, [selectedAgent, tools]);

  // 禁用工具列表
  const disallowedToolList = useMemo(() => {
    if (!selectedAgent?.disallowed_tools) return [];
    return tools.filter((t) => selectedAgent.disallowed_tools?.includes(t.name));
  }, [selectedAgent, tools]);

  // 可见的 Skill 组和成员技能
  const visibleSkillGroups = useMemo(() => {
    if (!selectedAgent?.visible_skill_group_ids) return [];
    const ids = selectedAgent.visible_skill_group_ids;
    return skillGroups.filter((g) => ids.includes(g.id));
  }, [selectedAgent, skillGroups]);

  const visibleRuleGroups = useMemo(() => {
    if (!selectedAgent?.visible_rule_group_ids) return [];
    const ids = selectedAgent.visible_rule_group_ids;
    return ruleGroups.filter((g) => ids.includes(g.id));
  }, [selectedAgent, ruleGroups]);

  // === Tools Overview ===
  // 按 group_id 统计，查找 group 名称进行展示
  const groupNames = useMemo(() => {
    const map: Record<string, string> = {};
    groups.forEach((g) => { map[g.id] = g.name; });
    return map;
  }, [groups]);

  const toolStats = useMemo(() => {
    const byGroup: Record<string, number> = {};
    tools.forEach((t) => {
      const gid = t.group_id || "__ungrouped__";
      byGroup[gid] = (byGroup[gid] || 0) + 1;
    });
    return byGroup;
  }, [tools]);

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="px-4 py-3 border-b border-border/50 flex-shrink-0">
        <div className="flex items-center gap-2 mb-1">
          <Zap size={14} className="text-amber-500" aria-hidden="true" />
          <h3 className="text-sm font-semibold text-slate-200">实时预览</h3>
        </div>
        {activeTab === "prompts" && (
          <div className="flex items-center gap-3 text-xs text-muted-foreground">
            <span className="flex items-center gap-1"><Hash size={12} aria-hidden="true" /> {enabledSections.length} 个片段</span>
            <span className="flex items-center gap-1"><FileText size={12} aria-hidden="true" /> 约 {totalTokens} 个令牌</span>
          </div>
        )}
        {activeTab === "agents" && (
          <p className="text-xs text-muted-foreground">
            {selectedAgent
              ? `${getAgentDisplayName(selectedAgent.agent_type)} — ${agentToolList.length} 个可用工具 · ${selectedAgent.disallowed_tools?.length || 0} 个禁用`
              : "← 点击左侧代理卡片查看详情"}
          </p>
        )}
        {activeTab === "tools" && (
          <p className="text-xs text-muted-foreground">
            共 {tools.length} 个工具 · {Object.keys(toolStats).length} 个分组
          </p>
        )}
      </div>

      {/* Token progress bar for prompts */}
      {activeTab === "prompts" && (
        <div className="px-4 py-2 border-b border-border/30 flex-shrink-0">
          <div className="flex items-center justify-between text-xs text-muted-foreground mb-1">
            <span>令牌使用</span>
            <span className="tabular-nums">
              {totalTokens} / {DEFAULT_MAX_CONTEXT_TOKENS}
            </span>
          </div>
          <div className="h-1.5 bg-slate-800 rounded-full overflow-hidden" role="progressbar" aria-valuenow={totalTokens} aria-valuemin={0} aria-valuemax={DEFAULT_MAX_CONTEXT_TOKENS} aria-label={`令牌使用: ${totalTokens}/${DEFAULT_MAX_CONTEXT_TOKENS}`}>
            <div
              className={`h-full rounded-full transition-all duration-300 ${
                totalTokens > DEFAULT_MAX_CONTEXT_TOKENS * 0.75
                  ? "bg-red-500"
                  : totalTokens > DEFAULT_MAX_CONTEXT_TOKENS * 0.5
                    ? "bg-amber-500"
                    : "bg-green-500"
              }`}
              style={{ width: `${Math.min((totalTokens / DEFAULT_MAX_CONTEXT_TOKENS) * 100, 100)}%` }}
            />
          </div>
        </div>
      )}

      {/* Main Preview Content */}
      <ScrollArea className="flex-1 [&>[data-radix-scroll-area-viewport]>div]:!block [&>[data-radix-scroll-area-viewport]>div]:!min-w-0 [&>[data-radix-scroll-area-viewport]>div]:!w-full">
        <div className="px-4 py-3">
          {/* === Prompt Preview === */}
          {activeTab === "prompts" && (
            <div className="space-y-1.5" role="list" aria-label="已启用的提示词片段">
              {enabledSections.map((sec, i) => {
                const defaultColor = SEC_COLORS[i % SEC_COLORS.length];
                const wfColor = "bg-violet-500/[0.06] border border-violet-500/20";
                return (
                  <div
                    key={sec.name}
                    role="listitem"
                    className={`rounded-md px-3 py-2 ${sec.workflow_only ? wfColor : defaultColor}`}
                  >
                    <div className="flex items-center justify-between mb-1">
                      <span className="flex items-center gap-1.5">
                        <span className="text-xs font-medium text-slate-400">{getSectionDisplayName(sec.name)}</span>
                        {sec.workflow_only && (
                          <Badge variant="outline" className="text-xs border-violet-500/40 text-violet-400">
                            工作流专属
                          </Badge>
                        )}
                      </span>
                      <Badge variant="outline" className="text-xs">{sec.token_estimate}t</Badge>
                    </div>
                    <pre className="text-xs text-slate-300 leading-relaxed whitespace-pre-wrap break-words font-sans" aria-label={`${getSectionDisplayName(sec.name)} 内容预览`}>
                      <HighlightedContent
                        content={sec.content}
                        templateVariables={templateVariables}
                      />
                    </pre>
                  </div>
                );
              })}
              {enabledSections.length === 0 && (
                <p className="text-sm text-muted-foreground text-center py-8">没有启用的 Section</p>
              )}
            </div>
          )}

          {/* === Agent Preview (增强版) === */}
          {activeTab === "agents" && (
            <div role="region" aria-label="代理预览">
              {selectedAgent ? (
                <div className="space-y-4">
                  {/* Agent 基本信息卡片 */}
                  <div className="bg-slate-800/80 border border-border/40 rounded-lg px-3 py-2.5">
                    <div className="flex items-center gap-2 mb-1">
                      <Bot size={14} className="text-indigo-500" aria-hidden="true" />
                      <span className="text-xs font-semibold text-slate-200">{getAgentDisplayName(selectedAgent.agent_type)}</span>
                      <Badge variant="outline" className="text-xs ml-auto">
                        {selectedAgent.tools?.includes("*") ? "全部工具" : selectedAgent.tools ? `${selectedAgent.tools.length} 个工具` : "仅通信"}
                      </Badge>
                    </div>
                    <p className="text-xs text-muted-foreground">{selectedAgent.description}</p>
                    <div className="grid grid-cols-2 gap-x-4 gap-y-1 mt-2 text-xs text-muted-foreground">
                      <span>轮次: <span className="text-slate-300">{selectedAgent.max_turns}</span></span>
                      <span>模型: <span className="text-slate-300">{selectedAgent.model || "继承主代理"}</span></span>
                      <span>工作区: <span className="text-slate-300">
                        {selectedAgent.copy_main_workspace === null ? "继承全局" : selectedAgent.copy_main_workspace ? "强制复制" : "不复制"}
                      </span></span>
                    </div>
                  </div>

                  {/* 可用工具列表 */}
                  <div>
                    <h4 className="text-xs text-muted-foreground mb-2 font-medium flex items-center gap-1">
                      <Wrench size={12} aria-hidden="true" />
                      可用工具（解析后）
                      <Badge variant="outline" className="text-xs">{agentToolList.length}</Badge>
                    </h4>
                    {agentToolList.length > 0 ? (
                      <div className="space-y-1">
                        {agentToolList.map((t) => {
                          const groupName = groupNames[t.group_id] || t.group_id;
                          return (
                            <div key={t.name} className="flex items-center gap-2 px-2 py-1.5 bg-slate-800/40 rounded transition-colors duration-200">
                              <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${getBgColor(t.group_id)}`} aria-hidden="true" />
                              <span className="text-xs font-mono text-cyan-500">{t.name}</span>
                              <Badge variant="outline" className="text-xs text-muted-foreground">{groupName}</Badge>
                              <span className="text-xs text-muted-foreground truncate flex-1" title={t.description}>{t.description}</span>
                            </div>
                          );
                        })}
                      </div>
                    ) : (
                      <div className="bg-slate-800/40 rounded px-2 py-2">
                        <p className="text-xs text-muted-foreground flex items-center gap-1">
                          <AlertCircle size={12} className="text-amber-500" aria-hidden="true" />
                          当前工具列表为空，请在代理定义中配置工具白名单
                        </p>
                        {!selectedAgent.tools && (
                          <p className="text-xs text-muted-foreground/60 mt-1">提示：tools 为 null 时仅含通信工具，设为 ["*"] 可使用全部工具</p>
                        )}
                      </div>
                    )}
                  </div>

                  {/* 禁用工具列表 */}
                  {disallowedToolList.length > 0 && (
                    <div>
                      <h4 className="text-xs text-muted-foreground mb-2 font-medium flex items-center gap-1">
                        <EyeOff size={12} className="text-red-500" aria-hidden="true" />
                        禁用工具
                        <Badge variant="outline" className="text-xs">{disallowedToolList.length}</Badge>
                      </h4>
                      <div className="space-y-1">
                        {disallowedToolList.map((t) => (
                          <div key={t.name} className="flex items-center gap-2 px-2 py-1.5 bg-slate-800/40 rounded opacity-60">
                            <span className="w-1.5 h-1.5 rounded-full flex-shrink-0 bg-red-500" aria-hidden="true" />
                            <span className="text-xs font-mono text-red-500">{t.name}</span>
                            <span className="text-xs text-muted-foreground truncate flex-1" title={t.description}>{t.description}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* 可见 Skill 组 */}
                  <div>
                    <h4 className="text-xs text-muted-foreground mb-2 font-medium flex items-center gap-1">
                      <Eye size={12} className="text-cyan-500" aria-hidden="true" />
                      可见 Skill 组
                      <Badge variant="outline" className="text-xs">{visibleSkillGroups.length}</Badge>
                    </h4>
                    {visibleSkillGroups.length > 0 ? (
                      <div className="space-y-1.5">
                        {visibleSkillGroups.map((g) => (
                          <div key={g.id} className="px-2 py-1.5 bg-cyan-500/[0.06] border border-cyan-500/20 rounded">
                            <div className="flex items-center gap-1">
                              <BookOpen size={12} className="text-cyan-500" aria-hidden="true" />
                              <span className="text-xs font-medium text-slate-300">{g.name}</span>
                            </div>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <p className="text-xs text-muted-foreground">未配置可见的 Skill 组</p>
                    )}
                  </div>

                  {/* 可见 Rule 组 */}
                  <div>
                    <h4 className="text-xs text-muted-foreground mb-2 font-medium flex items-center gap-1">
                      <Eye size={12} className="text-purple-500" aria-hidden="true" />
                      可见 Rule 组
                      <Badge variant="outline" className="text-xs">{visibleRuleGroups.length}</Badge>
                    </h4>
                    {visibleRuleGroups.length > 0 ? (
                      <div className="space-y-1.5">
                        {visibleRuleGroups.map((g) => (
                          <div key={g.id} className="px-2 py-1.5 bg-purple-500/[0.06] border border-purple-500/20 rounded">
                            <div className="flex items-center gap-1">
                              <BookOpen size={12} className="text-purple-500" aria-hidden="true" />
                              <span className="text-xs font-medium text-slate-300">{g.name}</span>
                            </div>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <p className="text-xs text-muted-foreground">未配置可见的 Rule 组</p>
                    )}
                  </div>

                  {/* Prompt 模板 */}
                  {selectedAgent.system_prompt_template && (
                    <div>
                      <h4 className="text-xs text-muted-foreground mb-1 font-medium">Prompt 模板:</h4>
                      <pre className="text-xs text-slate-300 bg-slate-800/40 rounded px-2 py-1.5 whitespace-pre-wrap">
                        {selectedAgent.system_prompt_template}
                      </pre>
                    </div>
                  )}
                </div>
              ) : (
                <div className="flex flex-col items-center justify-center py-12 text-muted-foreground" role="status">
                  <Bot size={32} className="mb-3 opacity-30" aria-hidden="true" />
                  <p className="text-xs">点击左侧代理卡片查看详情</p>
                  <p className="text-xs opacity-50 mt-1">可查看完整配置、工具列表、可见性等信息</p>
                </div>
              )}
            </div>
          )}

          {/* === Tools Overview === */}
          {activeTab === "tools" && (
            <div className="space-y-3" role="region" aria-label="工具概览">
              <div className="grid grid-cols-2 gap-2">
                {Object.entries(toolStats).map(([gid, count]) => {
                  const groupName = groupNames[gid] || gid;
                  return (
                    <div key={gid} className="bg-slate-800/80 border border-border/40 rounded-lg px-3 py-2 text-center">
                      <p className="text-lg font-bold text-cyan-500 tabular-nums">{count}</p>
                      <p className="text-xs text-muted-foreground">{groupName}</p>
                    </div>
                  );
                })}
              </div>
              <div>
                <h4 className="text-xs text-muted-foreground mb-2 font-medium">所有工具一览:</h4>
                <div className="space-y-1">
                  {tools.map((t) => (
                    <div key={t.name} className="flex items-center gap-2 text-xs min-w-0">
                      <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${getBgColor(t.group_id)}`} aria-hidden="true" />
                      <span className="font-mono text-slate-300 truncate flex-shrink-0 max-w-[40%]">{t.name}</span>
                      <span className="text-muted-foreground truncate min-w-0 flex-1" title={t.description}>{t.description}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}
        </div>
      </ScrollArea>
    </div>
  );
}


// ============ 占位符高亮组件 ============

// 内置变量中文名映射
const BUILT_IN_VAR_NAMES: Record<string, string> = {
  session_meta: "会话元信息",
  workflow_overview: "工作流概览",
  workflow_structure: "工作流结构",
  workflow_definition_json: "工作流定义",
  memory_context: "记忆上下文",
  tools_section: "工具列表",
  skills_section: "技能列表",
  rules_section: "规则列表",
  rules_reminder: "规则提醒",
  extra_tools: "额外工具",
  custom_append: "自定义追加",
  upstream_summary: "上游摘要",
};

function HighlightedContent({
  content,
  templateVariables,
}: {
  content: string;
  templateVariables: TemplateVariable[];
}) {
  // 构建自定义变量查找表：key → {name, description}
  const customVarMap = useMemo(() => {
    const map: Record<string, { name: string; description: string }> = {};
    for (const v of templateVariables) {
      map[v.key] = { name: v.name, description: v.description };
    }
    return map;
  }, [templateVariables]);

  const parts = useMemo(() => {
    type Part = string | { key: string; type: "builtin" | "custom" | "unknown" };
    const result: Part[] = [];
    let lastIndex = 0;
    let match: RegExpExecArray | null;
    const re = new RegExp(PLACEHOLDER_RE.source, "g");

    while ((match = re.exec(content)) !== null) {
      if (match.index > lastIndex) {
        result.push(content.slice(lastIndex, match.index));
      }

      const key = match[1];
      let type: "builtin" | "custom" | "unknown";
      if (BUILT_IN_VAR_NAMES[key]) {
        type = "builtin";
      } else if (customVarMap[key]) {
        type = "custom";
      } else {
        type = "unknown";
      }

      result.push({ key, type });
      lastIndex = match.index + match[0].length;
    }

    if (lastIndex < content.length) {
      result.push(content.slice(lastIndex));
    }

    return result;
  }, [content, customVarMap]);

  return (
    <>
      {parts.map((part, i) => {
        if (typeof part === "string") {
          return <span key={i}>{part}</span>;
        }
        const { key, type } = part;
        const colors = {
          builtin: "bg-green-500/15 text-green-500 border border-green-500/30",
          custom: "bg-amber-500/15 text-amber-500 border border-amber-500/30",
          unknown: "bg-red-500/10 text-red-500 border border-red-500/20",
        };

        let label: string;
        let tooltip: string;
        if (type === "builtin") {
          label = BUILT_IN_VAR_NAMES[key] || key;
          tooltip = `{{${key}}} — 系统内置变量，自动渲染`;
        } else if (type === "custom") {
          const info = customVarMap[key];
          label = info?.name || key;
          tooltip = `{{${key}}} — ${info?.description || "自定义变量块"}\n在节点属性面板中填写内容`;
        } else {
          label = "未定义变量";
          tooltip = `{{${key}}} — 未在 template_variables 中声明`;
        }

        return (
          <span
            key={i}
            className={`inline-flex items-center gap-1 px-1 rounded text-xs font-mono ${colors[type]}`}
            title={tooltip}
          >
            <code className="text-xs">{`{{${key}}}`}</code>
            <span className="text-xs opacity-70">{label}</span>
          </span>
        );
      })}
    </>
  );
}
