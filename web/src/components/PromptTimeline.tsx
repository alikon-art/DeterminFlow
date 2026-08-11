import { PromptHistoryEntry } from "../types";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useState } from "react";
import { formatTime } from "../lib/utils-helpers";

interface PromptTimelineProps {
  history: PromptHistoryEntry[];
}

export default function PromptTimeline({ history }: PromptTimelineProps) {
  return (
    <div className="p-4 rounded-lg bg-white border border-slate-200" role="region" aria-label="提示词进化时间线">
      <h3 className="text-sm font-medium text-muted-foreground mb-4">提示词进化时间线</h3>
      {history.length === 0 ? (
        <div className="text-center text-muted-foreground text-sm py-4" role="status">暂无修改历史</div>
      ) : (
        <div className="relative pl-6">
          {/* Timeline line */}
          <div className="absolute left-2.5 top-0 bottom-0 w-px bg-indigo-500/40" aria-hidden="true" />

          {history.map((entry, i) => (
            <TimelineEntry key={i} entry={entry} />
          ))}
        </div>
      )}
    </div>
  );
}

function TimelineEntry({ entry }: { entry: PromptHistoryEntry }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="relative pb-4" role="article" aria-label={`版本 ${entry.version} 到 ${entry.version + 1} 的变更`}>
      {/* Node dot */}
      <div className="absolute -left-3.5 w-3 h-3 rounded-full bg-white border-2 border-blue-500" aria-hidden="true" />

      <div className="ml-4">
        <button
          onClick={() => setExpanded(!expanded)}
          className="w-full text-left cursor-pointer min-h-[44px] flex flex-col justify-center"
          aria-expanded={expanded}
          aria-label={`版本变更: v${entry.version} 到 v${entry.version + 1}，点击${expanded ? '折叠' : '展开'}详情`}
        >
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-indigo-500">
              v{entry.version} → v{entry.version + 1}
            </span>
            {expanded ? <ChevronDown size={14} className="text-muted-foreground" aria-hidden="true" /> : <ChevronRight size={14} className="text-muted-foreground" aria-hidden="true" />}
          </div>
          <div className="text-xs text-muted-foreground mt-0.5">
            {formatTime(entry.timestamp)}
          </div>
          <div className="text-xs text-slate-300 mt-1">
            原因: {entry.reason || "未说明"}
          </div>
        </button>

        {expanded && (
          <div className="mt-2 space-y-2">
            <div className="bg-slate-50 border border-slate-200 rounded p-2">
              <div className="text-xs text-red-500 mb-1">旧提示词 (前100字):</div>
              <div className="text-xs text-slate-400 max-h-24 overflow-y-auto">
                {entry.old_prompt.slice(0, 200)}...
              </div>
            </div>
            <div className="bg-slate-50 border border-slate-200 rounded p-2">
              <div className="text-xs text-green-500 mb-1">新提示词 (前100字):</div>
              <div className="text-xs text-slate-400 max-h-24 overflow-y-auto">
                {entry.new_prompt.slice(0, 200)}...
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
