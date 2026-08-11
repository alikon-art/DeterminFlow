import { useState, useEffect, useMemo, useCallback } from "react";
import { FileText, RefreshCw, Eye, Code, Wrench, ChevronDown, ChevronRight, Terminal, AlertCircle, CheckCircle, Copy, Download } from "lucide-react";
import { fetchSessionSystemPrompt, fetchSessions } from "../lib/api";
import { Session } from "../types";
import MarkdownRenderer from "../components/MarkdownRenderer";
import { getAgentDisplayName } from "../lib/agent-labels";

interface ToolParameter {
  type: string;
  description: string;
  required: boolean;
}

interface ToolData {
  name: string;
  description: string;
  parameters?: Record<string, ToolParameter>;
}

interface SystemPromptData {
  session_id: string;
  agent_type: string;
  system_prompt: string;
  tools: ToolData[];
  tools_count: number;
  message_counts: {
    system: number;
    user: number;
    assistant: number;
    tool: number;
  };
  token_estimate: {
    system_prompt: number;
    messages: number;
    total: number;
  };
  model_config: {
    model: string;
    temperature: number;
    max_context_tokens: number;
    max_tool_rounds: number;
  };
}

export default function SystemPromptPage() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [promptData, setPromptData] = useState<SystemPromptData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<"preview" | "raw">("preview");
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    loadSessions();
  }, []);

  useEffect(() => {
    if (selectedSessionId) {
      loadSystemPrompt(selectedSessionId);
    }
  }, [selectedSessionId]);

  const loadSessions = async () => {
    try {
      const data = await fetchSessions();
      setSessions(data.sessions);
      if (data.main_session_id) {
        setSelectedSessionId(data.main_session_id);
      } else if (data.sessions.length > 0) {
        setSelectedSessionId(data.sessions[0].session_id);
      }
    } catch (err) {
      console.error("加载会话列表失败:", err);
      setError("加载会话列表失败");
    }
  };

  const loadSystemPrompt = async (sessionId: string) => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchSessionSystemPrompt(sessionId);
      setPromptData(data);
    } catch (err) {
      console.error("加载系统提示词失败:", err);
      setError("加载系统提示词失败");
      setPromptData(null);
    } finally {
      setLoading(false);
    }
  };

  // 生成完整的 markdown 内容（包含工具列表），用 useMemo 避免每次渲染重新生成
  const fullMarkdown = useMemo(() => {
    if (!promptData) return "";

    let markdown = promptData.system_prompt;

    // 添加元信息
    markdown += "\n---\n\n";
    markdown += "## 会话元信息\n\n";
    markdown += `- **Session ID**: \`${promptData.session_id}\`\n`;
    markdown += `- **Agent Type**: ${promptData.agent_type}\n`;
    markdown += `- **Model**: ${promptData.model_config.model}\n`;
    markdown += `- **Temperature**: ${promptData.model_config.temperature}\n`;
    markdown += `- **Max Context Tokens**: ${promptData.model_config.max_context_tokens.toLocaleString()}\n`;
    markdown += `- **Max Tool Rounds**: ${promptData.model_config.max_tool_rounds}\n`;
    markdown += `- **Token Estimate (System Prompt)**: ${promptData.token_estimate.system_prompt.toLocaleString()}\n`;
    markdown += `- **Token Estimate (Messages)**: ${promptData.token_estimate.messages.toLocaleString()}\n`;
    markdown += `- **Token Estimate (Total)**: ${promptData.token_estimate.total.toLocaleString()}\n`;
    markdown += `- **Message Counts**: User: ${promptData.message_counts.user}, Assistant: ${promptData.message_counts.assistant}, Tool: ${promptData.message_counts.tool}\n`;

    return markdown;
  }, [promptData]);

  const handleRefresh = useCallback(() => {
    if (selectedSessionId) {
      loadSystemPrompt(selectedSessionId);
    }
  }, [selectedSessionId]);

  const handleCopy = useCallback(async () => {
    if (!fullMarkdown) return;
    if (!navigator.clipboard) {
      setError("当前浏览器不支持剪贴板 API，请手动复制");
      return;
    }
    try {
      await navigator.clipboard.writeText(fullMarkdown);
      setCopied(true);
      setTimeout(() => setCopied(false), 3000);
    } catch (err) {
      console.error("复制失败:", err);
      setError("复制到剪贴板失败，请手动复制");
    }
  }, [fullMarkdown]);

  const handleDownload = useCallback(() => {
    if (!fullMarkdown || !selectedSessionId) return;
    try {
      const blob = new Blob([fullMarkdown], { type: "text/markdown" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `system-prompt-${selectedSessionId.slice(0, 8)}.md`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (err) {
      console.error("下载失败:", err);
      setError("下载文件失败，请重试");
    }
  }, [fullMarkdown, selectedSessionId]);

  return (
    <div className="h-[calc(100dvh-3.5rem)] overflow-y-auto" role="main" aria-label="System Prompt 预览页面">
      <div className="max-w-7xl mx-auto px-6 py-6 space-y-6">
        {/* 标题栏 */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-lg bg-cyan-500/10 border border-cyan-500/20">
              <FileText size={22} className="text-cyan-400" />
            </div>
            <div>
              <h2 className="text-xl font-bold text-slate-100">System Prompt 预览</h2>
              <p className="text-xs text-slate-400 mt-0.5">
                查看代理实际获取到的完整系统提示词（包含模型上下文协议、技能、规则等）
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {/* 视图模式切换 */}
            <div className="flex items-center gap-1 bg-slate-800/80 rounded-lg p-1 border border-slate-700/50">
              <button
                type="button"
                onClick={() => setViewMode("preview")}
                className={`flex items-center gap-2 px-3 py-1.5 rounded text-sm transition-all duration-200 min-h-[44px] focus-visible:ring-2 focus-visible:ring-cyan-500/30 ${
                  viewMode === "preview"
                    ? "bg-cyan-500/20 text-cyan-400"
                    : "text-slate-400 hover:text-slate-200"
                }`}
                aria-label="预览模式"
                aria-pressed={viewMode === "preview"}
              >
                <Eye size={14} aria-hidden="true" />
                <span>预览</span>
              </button>
              <button
                type="button"
                onClick={() => setViewMode("raw")}
                className={`flex items-center gap-2 px-3 py-1.5 rounded text-sm transition-all duration-200 min-h-[44px] focus-visible:ring-2 focus-visible:ring-purple-500/30 ${
                  viewMode === "raw"
                    ? "bg-purple-500/20 text-purple-400"
                    : "text-slate-400 hover:text-slate-200"
                }`}
                aria-label="原格式模式"
                aria-pressed={viewMode === "raw"}
              >
                <Code size={14} aria-hidden="true" />
                <span>原格式</span>
              </button>
            </div>

            {/* 操作按钮组 */}
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={handleCopy}
                disabled={loading || !promptData}
                className="p-2 rounded-lg border border-slate-600 text-slate-400
                  hover:bg-slate-700 hover:text-slate-200 transition-all duration-200 cursor-pointer
                  focus-visible:ring-2 focus-visible:ring-cyan-500/30
                  disabled:opacity-50 disabled:cursor-not-allowed min-h-[44px] min-w-[44px] flex items-center justify-center"
                title="复制到剪贴板"
                aria-label="复制到剪贴板"
              >
                {copied ? (
                  <CheckCircle size={16} className="text-green-400" />
                ) : (
                  <Copy size={16} />
                )}
              </button>
              <button
                type="button"
                onClick={handleDownload}
                disabled={loading || !promptData}
                className="p-2 rounded-lg border border-slate-600 text-slate-400
                  hover:bg-slate-700 hover:text-slate-200 transition-all duration-200 cursor-pointer
                  focus-visible:ring-2 focus-visible:ring-cyan-500/30
                  disabled:opacity-50 disabled:cursor-not-allowed min-h-[44px] min-w-[44px] flex items-center justify-center"
                title="下载 Markdown 文件"
                aria-label="下载 Markdown 文件"
              >
                <Download size={16} />
              </button>
              <button
                type="button"
                onClick={handleRefresh}
                disabled={loading}
                className="p-2 rounded-lg border border-slate-600 text-slate-400
                  hover:bg-slate-700 hover:text-slate-200 transition-all duration-200 cursor-pointer
                  focus-visible:ring-2 focus-visible:ring-cyan-500/30
                  disabled:opacity-50 disabled:cursor-not-allowed min-h-[44px] min-w-[44px] flex items-center justify-center"
                title="刷新"
                aria-label="刷新数据"
              >
                <RefreshCw size={16} className={loading ? "animate-spin motion-reduce:animate-none" : ""} aria-hidden="true" />
              </button>
            </div>
          </div>
        </div>

        {/* 会话选择器 */}
        <section className="bg-slate-800/80 rounded-xl border border-slate-700/50 p-4" aria-label="会话选择">
          <label htmlFor="session-select" className="text-sm text-slate-300 font-medium mb-2 block">
            选择会话
          </label>
          <div className="relative">
            <select
              id="session-select"
              value={selectedSessionId || ""}
              onChange={(e) => setSelectedSessionId(e.target.value)}
              className="w-full bg-slate-800 border border-slate-600 rounded-lg pl-3 pr-10 py-2 text-sm text-slate-200
                focus:border-cyan-500/50 focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500/30
                cursor-pointer transition-all duration-200 min-h-[44px] appearance-none"
              aria-label="选择会话"
            >
              {sessions.length === 0 ? (
                <option value="" disabled>暂无可用会话</option>
              ) : (
                sessions.map((session) => (
                  <option key={session.session_id} value={session.session_id}>
                    {session.type === "main" ? "[主] " : "[子] "}
                    {session.session_id.slice(0, 8)} - {session.task || "无任务描述"} ({getAgentDisplayName(session.agent_type || "default")})
                  </option>
                ))
              )}
            </select>
            <ChevronDown size={16} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none" aria-hidden="true" />
          </div>
        </section>

        {/* 错误提示 */}
        {error && (
          <div
            className="rounded-xl border border-red-500/30 bg-red-500/10 p-4 flex items-start gap-3"
            role="alert"
            aria-live="polite"
          >
            <AlertCircle size={18} className="text-red-400 mt-0.5 flex-shrink-0" aria-hidden="true" />
            <div className="flex-1">
              <p className="text-sm text-red-400">{error}</p>
              <button
                type="button"
                onClick={handleRefresh}
                disabled={!selectedSessionId}
                className="mt-2 text-xs text-red-400 hover:text-red-300 underline underline-offset-2
                  cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed min-h-[44px] flex items-center
                  focus-visible:ring-2 focus-visible:ring-red-500/30 rounded"
                aria-label="重试加载"
              >
                重试
              </button>
            </div>
          </div>
        )}

        {/* 加载中 */}
        {loading && (
          <div
            className="flex items-center justify-center py-12"
            role="status"
            aria-label="正在加载 System Prompt 数据"
          >
            <div className="flex items-center gap-3 text-slate-400">
              <RefreshCw size={20} className="animate-spin motion-reduce:animate-none" aria-hidden="true" />
              <span>加载中...</span>
            </div>
          </div>
        )}

        {/* 复制成功提示 */}
        {copied && (
          <div
            className="fixed bottom-4 right-4 z-50"
            role="status"
            aria-live="polite"
          >
            <div className="bg-slate-800/90 rounded-lg border border-green-500/30 p-3 flex items-center gap-2 shadow-lg">
              <CheckCircle size={16} className="text-green-400" aria-hidden="true" />
              <span className="text-sm text-green-400">已复制到剪贴板</span>
              <button
                type="button"
                onClick={() => setCopied(false)}
                className="ml-1 p-0.5 rounded text-slate-400 hover:text-slate-200 focus-visible:ring-2 focus-visible:ring-green-500/30"
                aria-label="关闭提示"
              >
                <span aria-hidden="true">&times;</span>
              </button>
            </div>
          </div>
        )}

        {/* System Prompt 内容 */}
        {!loading && promptData && (
          <section
            className="bg-slate-800/80 rounded-xl border border-slate-700/50 overflow-hidden"
            aria-label="System Prompt 内容"
          >
            <div className="bg-slate-800/50 px-5 py-3 border-b border-slate-700/50">
              <h3 className="text-base font-semibold text-slate-100">
                System Prompt 完整内容
              </h3>
              <p className="text-xs text-slate-400 mt-1">
                {viewMode === "preview"
                  ? "以下是渲染后的 Markdown 预览"
                  : "以下是原始 Markdown 文本"}
              </p>
            </div>
            <div className="p-6">
              {viewMode === "preview" ? (
                <MarkdownRenderer content={fullMarkdown} />
              ) : (
                <pre
                  className="text-xs text-slate-300 whitespace-pre-wrap font-mono bg-slate-900/60 rounded-lg p-4 overflow-x-auto max-h-[60vh]"
                  role="textbox"
                  aria-readonly="true"
                  aria-label="原始 Markdown 内容"
                  tabIndex={0}
                >
                  {fullMarkdown}
                </pre>
              )}
            </div>
          </section>
        )}

        {/* Tools 列表（LLM 通过 bind_tools 看到的工具定义） */}
        {!loading && promptData && promptData.tools.length > 0 && (
          <ToolsSection
            tools={promptData.tools}
            toolsCount={promptData.tools_count}
          />
        )}
      </div>
    </div>
  );
}

// ===== Tools Section 组件：展示 LLM 通过 bind_tools 看到的完整工具定义 =====

function ToolsSection({ tools, toolsCount }: { tools: ToolData[]; toolsCount: number }) {
  const [expandedTool, setExpandedTool] = useState<string | null>(null);

  return (
    <section
      className="bg-slate-800/80 rounded-xl border border-slate-700/50 overflow-hidden"
      aria-label="LLM 工具定义"
    >
      {/* Header */}
      <div className="bg-slate-800/50 px-5 py-3 border-b border-slate-700/50 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="p-1.5 rounded-lg bg-cyan-500/10 border border-cyan-500/20">
            <Terminal size={18} className="text-cyan-400" aria-hidden="true" />
          </div>
          <div>
            <h3 className="text-base font-semibold text-slate-100">
              LLM 工具定义 <span className="text-xs text-slate-400 font-normal">(bind_tools)</span>
            </h3>
            <p className="text-xs text-slate-400 mt-0.5">
              以下工具通过 OpenAI Function Calling API 作为结构化参数传递给模型，包含完整的 JSON Schema 参数定义
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <div
            className="px-3 py-1 rounded-full bg-cyan-500/10 border border-cyan-500/20 text-cyan-400 text-xs font-medium tabular-nums"
            aria-label={`共 ${toolsCount || tools.length} 个工具`}
          >
            {toolsCount || tools.length} 个工具
          </div>
        </div>
      </div>

      {/* Tool List */}
      <div className="p-4 space-y-3" role="list" aria-label="工具列表">
        {tools.map((tool) => {
          const isExpanded = expandedTool === tool.name;
          const paramCount = tool.parameters ? Object.keys(tool.parameters).length : 0;
          const requiredCount = tool.parameters
            ? Object.values(tool.parameters).filter((p) => p.required).length
            : 0;

          return (
            <div
              key={tool.name}
              className={`rounded-xl border transition-all duration-200 ${
                isExpanded
                  ? "border-cyan-500/40 bg-slate-800/80"
                  : "border-slate-700/40 bg-slate-800/40 hover:bg-slate-800/60 hover:border-slate-600/60"
              }`}
              role="listitem"
            >
              {/* Tool Header */}
              <button
                type="button"
                onClick={() => setExpandedTool(isExpanded ? null : tool.name)}
                className="w-full flex items-center gap-3 px-4 py-3 cursor-pointer text-left min-h-[44px] focus-visible:ring-2 focus-visible:ring-cyan-500/30 rounded-xl"
                aria-expanded={isExpanded}
                aria-controls={`tool-details-${tool.name}`}
              >
                <div className="w-8 h-8 rounded-lg bg-cyan-500/15 flex items-center justify-center flex-shrink-0">
                  <Wrench size={15} className="text-cyan-400" aria-hidden="true" />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-semibold text-slate-100 font-mono">{tool.name}</span>
                    {paramCount > 0 && (
                      <span className="text-xs px-1.5 py-0.5 rounded bg-slate-700/80 text-slate-400 border border-slate-600/40 tabular-nums">
                        {paramCount} 参数
                      </span>
                    )}
                    {requiredCount > 0 && (
                      <span className="text-xs px-1.5 py-0.5 rounded bg-red-500/10 text-red-400 border border-red-500/20 tabular-nums">
                        {requiredCount} 必填
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-slate-400 mt-0.5 line-clamp-1" title={tool.description || undefined}>{tool.description || "无描述"}</p>
                </div>
                <div className="text-slate-500 flex-shrink-0" aria-hidden="true">
                  {isExpanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                </div>
              </button>

              {/* Expanded Parameters */}
              {isExpanded && tool.parameters && Object.keys(tool.parameters).length > 0 && (
                <div
                  id={`tool-details-${tool.name}`}
                  className="px-4 pb-4 pt-0 border-t border-slate-700/30 mx-4"
                  role="region"
                  aria-label={`${tool.name} 的参数详情`}
                >
                  <div className="mt-3 mb-2 text-xs font-medium text-slate-400 flex items-center gap-2">
                    <Code size={12} aria-hidden="true" />
                    参数 JSON Schema
                    <span className="text-xs text-slate-500" aria-hidden="true">(从 args_schema 提取)</span>
                  </div>
                  <div className="space-y-1.5" role="list" aria-label={`${tool.name} 的参数列表`}>
                    {Object.entries(tool.parameters).map(([paramName, param]) => (
                      <div
                        key={paramName}
                        className="flex items-start gap-3 px-3 py-2 rounded-lg bg-slate-900/60 border border-slate-700/30"
                        role="listitem"
                      >
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="text-xs font-mono font-semibold text-amber-400">{paramName}</span>
                            <span className="text-xs px-1.5 py-0.5 rounded bg-slate-800/80 text-slate-400 border border-slate-600/30 font-mono">
                              {param.type || "string"}
                            </span>
                            {param.required && (
                              <span className="text-xs text-red-400 font-medium">*必填</span>
                            )}
                          </div>
                          {param.description && (
                            <p className="text-xs text-slate-400 mt-0.5">{param.description}</p>
                          )}
                        </div>
                        <div
                          className={`w-1.5 h-1.5 rounded-full mt-1.5 flex-shrink-0 ${
                            param.required ? "bg-red-400" : "bg-slate-500"
                          }`}
                          aria-label={param.required ? "必填参数" : "可选参数"}
                        />
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* 无参数时的提示 */}
              {isExpanded && (!tool.parameters || Object.keys(tool.parameters).length === 0) && (
                <div
                  id={`tool-details-${tool.name}`}
                  className="px-4 pb-4 pt-0 border-t border-slate-700/30 mx-4"
                  role="region"
                  aria-label={`${tool.name} 无参数`}
                >
                  <div className="mt-3 text-xs text-slate-400 text-center py-3 bg-slate-900/40 rounded-lg">
                    此工具无参数
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}
