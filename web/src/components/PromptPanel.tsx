import { useState } from "react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Badge } from "@/components/ui/badge";
import { RefreshCw, Lightbulb } from "lucide-react";
import { fetchSessionSystemPrompt } from "../lib/api";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import MessageItem from "./MessageItem";

type LlmContextData = Awaited<ReturnType<typeof fetchSessionSystemPrompt>>;

interface PromptPanelProps {
  llmContext: LlmContextData | null;
  loading: boolean;
  onRefresh: () => void;
  sessionId: string;
}

export default function PromptPanel({ llmContext, loading, onRefresh, sessionId }: PromptPanelProps) {
  const [activeTab, setActiveTab] = useState<"system_prompt" | "messages" | "tools">("system_prompt");

  if (loading && !llmContext) {
    return (
      <div className="p-4 flex items-center justify-center text-muted-foreground text-sm gap-2" role="status" aria-label="加载中">
        <RefreshCw size={14} className="animate-spin" aria-hidden="true" />
        加载中...
      </div>
    );
  }

  if (!llmContext) {
    return <div className="p-4 text-center text-muted-foreground text-sm" role="status" aria-label="暂无 LLM 上下文数据">暂无数据</div>;
  }

  const { system_prompt, agent_type, tools, tools_count, message_counts, token_estimate, model_config, messages } = llmContext;

  return (
    <div className="h-full flex flex-col bg-white">
      {/* Header */}
      <div className="px-4 py-3 border-b border-slate-200 bg-white flex-shrink-0">
        <div className="flex items-center justify-between mb-1.5">
          <div className="flex items-center gap-1.5">
            <span className="text-xs font-medium text-purple-500">LLM 完整上下文</span>
            <Badge variant="outline" className="text-xs text-muted-foreground border-muted-foreground/30">
              {agent_type}
            </Badge>
          </div>
          <button
            onClick={onRefresh}
            disabled={loading}
            className="p-1 rounded-md text-muted-foreground hover:text-foreground hover:bg-slate-800/60 transition-colors cursor-pointer disabled:opacity-40 min-h-[44px] min-w-[44px] flex items-center justify-center"
            aria-label="刷新 LLM 上下文"
          >
            <RefreshCw size={12} className={loading ? "animate-spin" : ""} aria-hidden="true" />
          </button>
        </div>
        {/* 摘要统计条 */}
        <div className="flex items-center gap-2 text-[11px] text-slate-500 flex-wrap">
          <span className="font-mono text-blue-600">{sessionId}</span>
          <span aria-hidden="true">·</span>
          <span>{model_config.model}</span>
          <span aria-hidden="true">·</span>
          <span>T={model_config.temperature}</span>
          <span aria-hidden="true">·</span>
          <span>~{token_estimate.total}t</span>
        </div>
      </div>

      {/* Sub Tabs */}
      <div className="flex border-b border-border/50 flex-shrink-0" role="tablist" aria-label="LLM 上下文视图">
        {([
          { key: "system_prompt" as const, label: "System Prompt", count: `${token_estimate.system_prompt}t` },
          { key: "messages" as const, label: "会话历史", count: `${message_counts.user + message_counts.assistant + message_counts.tool}条` },
          { key: "tools" as const, label: "Tools", count: String(tools_count) },
        ]).map(({ key, label, count }) => (
          <button
            key={key}
            onClick={() => setActiveTab(key)}
            role="tab"
            aria-selected={activeTab === key}
            aria-controls={`tabpanel-${key}`}
            className={`flex-1 py-1.5 text-xs font-medium transition-colors cursor-pointer min-h-[44px] ${
              activeTab === key
                ? "text-indigo-500 border-b border-indigo-500"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            {label} <span className="opacity-60">({count})</span>
          </button>
        ))}
      </div>

      {/* Content */}
      <ScrollArea className="flex-1">
        <div className="px-4 py-4 space-y-3">
          {/* === System Prompt Tab === */}
          {activeTab === "system_prompt" && (
            <div id="tabpanel-system_prompt" role="tabpanel" aria-label="System Prompt 内容">
              {system_prompt ? (
                <div className="p-4 rounded-lg bg-slate-800/80 border border-slate-700/50 prose prose-sm max-w-none break-words
                  prose-headings:text-purple-500 prose-headings:text-sm prose-headings:font-semibold prose-headings:mt-3 prose-headings:mb-1.5
                  prose-p:text-xs prose-p:text-slate-300 prose-p:leading-relaxed prose-p:my-1
                  prose-li:text-xs prose-li:text-slate-300 prose-li:my-0.5
                  prose-strong:text-amber-500 prose-strong:font-semibold
                  prose-code:text-cyan-500 prose-code:text-xs prose-code:bg-slate-800/60 prose-code:px-1 prose-code:py-0.5 prose-code:rounded
                  prose-hr:border-border/50 prose-hr:my-2
                  prose-ul:my-1 prose-ol:my-1">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {system_prompt}
                  </ReactMarkdown>
                </div>
              ) : (
                <div className="text-center text-muted-foreground text-sm py-8" role="status">该会话无 system prompt</div>
              )}
            </div>
          )}

          {/* === Messages Tab === */}
          {activeTab === "messages" && (
            <div id="tabpanel-messages" role="tabpanel" aria-label="会话历史内容" className="space-y-2">
              <div className="text-xs text-muted-foreground mb-2 bg-slate-800/40 rounded p-2">
                <div className="font-medium text-amber-500 mb-1 flex items-center gap-1.5">
                  <Lightbulb size={14} className="flex-shrink-0" aria-hidden="true" />
                  这是 LLM 实际看到的会话历史
                </div>
                <div>包含所有 system/user/assistant/tool 消息，如果触发了压缩（完整对话压缩/工具结果压缩/尾部轮次删除），这里会显示压缩后的实时数据。</div>
              </div>

              {/* Token 使用概览 */}
              <div className="p-3 rounded-lg bg-slate-800/80 border border-slate-700/50 mb-2">
                <h4 className="text-xs text-muted-foreground font-medium mb-2">Token 估算</h4>
                <div className="space-y-1.5">
                  <div className="flex justify-between text-xs">
                    <span className="text-slate-400">System Prompt</span>
                    <span className="text-purple-500 font-mono">{token_estimate.system_prompt}</span>
                  </div>
                  <div className="flex justify-between text-xs">
                    <span className="text-slate-400">对话消息</span>
                    <span className="text-cyan-500 font-mono">{token_estimate.messages}</span>
                  </div>
                  <div className="border-t border-border/30 pt-1 flex justify-between text-xs">
                    <span className="text-slate-300 font-medium">总计（不含 tools schema）</span>
                    <span className="text-foreground font-mono font-bold">{token_estimate.total}</span>
                  </div>
                  <div className="h-1.5 bg-slate-800 rounded-full overflow-hidden mt-1" role="progressbar" aria-valuenow={Math.min((token_estimate.total / model_config.max_context_tokens) * 100, 100)} aria-valuemin={0} aria-valuemax={100} aria-label={`Token 使用率 ${Math.round((token_estimate.total / model_config.max_context_tokens) * 100)}%`}>
                    <div
                      className={`h-full rounded-full transition-all ${
                        token_estimate.total > model_config.max_context_tokens * 0.8 ? "bg-red-500" :
                        token_estimate.total > model_config.max_context_tokens * 0.5 ? "bg-amber-500" : "bg-green-500"
                      }`}
                      style={{ width: `${Math.min((token_estimate.total / model_config.max_context_tokens) * 100, 100)}%` }}
                    />
                  </div>
                  <div className="text-xs text-muted-foreground text-right">
                    {token_estimate.total} / {model_config.max_context_tokens} (截断阈值)
                  </div>
                </div>
              </div>

              {/* 消息统计 */}
              <div className="p-3 rounded-lg bg-slate-800/80 border border-slate-700/50 mb-2">
                <h4 className="text-xs text-muted-foreground font-medium mb-2">消息组成</h4>
                <div className="grid grid-cols-2 gap-2" role="list" aria-label="消息类型统计">
                  {([
                    { label: "用户消息", count: message_counts.user, color: "text-green-500" },
                    { label: "助手回复", count: message_counts.assistant, color: "text-indigo-500" },
                    { label: "工具结果", count: message_counts.tool, color: "text-amber-500" },
                    { label: "系统消息", count: message_counts.system, color: "text-purple-500" },
                  ]).map(({ label, count, color }) => (
                    <div key={label} className="flex justify-between text-xs" role="listitem">
                      <span className="text-slate-400">{label}</span>
                      <span className={`font-mono ${color}`}>{count}</span>
                    </div>
                  ))}
                </div>
              </div>

              {/* 完整消息列表 */}
              <div className="space-y-1.5">
                <h4 className="text-xs text-muted-foreground font-medium mb-1">完整消息列表 ({messages?.length || 0} 条)</h4>
                {messages && messages.length > 0 ? (
                  messages.map((msg, idx) => (
                    <MessageItem key={idx} message={msg} index={idx} />
                  ))
                ) : (
                  <div className="text-center text-muted-foreground text-sm py-8" role="status">暂无消息</div>
                )}
              </div>
            </div>
          )}

          {/* === Tools Tab === */}
          {activeTab === "tools" && (
            <div id="tabpanel-tools" role="tabpanel" aria-label="工具列表内容" className="space-y-1.5">
              <div className="text-xs text-muted-foreground mb-2">
                以下 {tools_count} 个工具通过 <code className="text-cyan-500 bg-slate-800/60 px-1 rounded">bind_tools</code> 绑定，
                以独立的 <code className="text-cyan-500 bg-slate-800/60 px-1 rounded">tools</code> 参数传给 API（不在 system message 中）
              </div>
              {tools.map((tool) => (
                <div key={tool.name} className="px-2.5 py-2 rounded-lg bg-slate-800/80 border border-slate-700/50" role="article" aria-label={`工具: ${tool.name}`}>
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-xs font-mono text-cyan-500 font-semibold">{tool.name}</span>
                  </div>
                  <p className="text-xs text-slate-400 mb-1">{tool.description}</p>
                  {tool.parameters && Object.keys(tool.parameters).length > 0 && (
                    <div className="flex flex-wrap gap-1" role="list" aria-label={`${tool.name} 参数列表`}>
                      {Object.entries(tool.parameters).map(([name, info]) => (
                        <span
                          key={name}
                          className={`text-xs px-1.5 py-0.5 rounded ${
                            info.required
                              ? "bg-amber-500/10 text-amber-500 border border-amber-500/20"
                              : "bg-slate-800/60 text-muted-foreground"
                          }`}
                          title={`${info.type}${info.required ? " (必填)" : " (可选)"}: ${info.description}`}
                          role="listitem"
                        >
                          {name}: {info.type}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              ))}
              {tools.length === 0 && (
                <div className="text-center text-muted-foreground text-sm py-8" role="status">该会话无绑定工具</div>
              )}
            </div>
          )}
        </div>
      </ScrollArea>
    </div>
  );
}
