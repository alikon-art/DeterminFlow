import { ToolInfo, EventBusStats } from "../types";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";
import { Badge } from "@/components/ui/badge";
import { toolGroupLabel, toolGroupColor, getToolDisplayName } from "../lib/utils-helpers";
import { Wrench, BarChart3 } from "lucide-react";

interface ToolStatsPanelProps {
  tools: ToolInfo[];
  stats: EventBusStats;
}

export default function ToolStatsPanel({ tools, stats }: ToolStatsPanelProps) {
  // Tool call frequency data
  const freqData = Object.entries(stats.tool_call_counts)
    .sort(([, a], [, b]) => b - a)
    .map(([name, count]) => ({ name: getToolDisplayName(name), count }));

  return (
    <section aria-label="工具统计" className="grid grid-cols-1 md:grid-cols-2 gap-4">
      {/* Tool Call Frequency */}
      <div className="bg-white border border-slate-200 rounded-lg p-4">
        <h3 className="text-sm font-medium text-muted-foreground mb-3 flex items-center gap-2">
          <BarChart3 className="w-4 h-4" aria-hidden="true" />
          工具调用频率
        </h3>
        {freqData.length > 0 ? (
          <div className="h-64" role="img" aria-label="工具调用频率柱状图">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={freqData} layout="vertical">
                <XAxis type="number" tick={{ fontSize: 12, fill: "#94a3b8" }} />
                <YAxis type="category" dataKey="name" tick={{ fontSize: 12, fill: "#94a3b8" }} width={120} />
                <Tooltip
                  contentStyle={{
                    background: "#ffffff",
                    border: "1px solid #e2e8f0",
                    borderRadius: "8px",
                    color: "#1f2937",
                    fontSize: "12px",
                    boxShadow: "0 8px 24px rgb(15 23 42 / 0.08)",
                  }}
                />
                <Bar dataKey="count" fill="#F59E0B" radius={[0, 4, 4, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        ) : (
          <div className="h-64 flex items-center justify-center text-muted-foreground text-sm" role="status">
            暂无工具调用记录
          </div>
        )}
      </div>

      {/* Registered Tools List */}
      <div className="bg-white border border-slate-200 rounded-lg p-4">
        <h3 className="text-sm font-medium text-muted-foreground mb-3 flex items-center gap-2">
          <Wrench className="w-4 h-4" aria-hidden="true" />
          已注册工具 ({tools.length})
        </h3>
        <div className="space-y-2 max-h-64 overflow-y-auto" role="list" aria-label="已注册工具列表">
          {tools.map((tool) => (
            <div
              key={tool.name}
              role="listitem"
              className="flex items-start gap-2 rounded-md border border-slate-200 bg-white px-3 py-2 hover:border-blue-200 hover:bg-blue-50/40 transition-colors duration-200"
            >
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-mono font-medium text-foreground">{getToolDisplayName(tool.name)}</span>
                  <Badge
                    variant="outline"
                    className={`text-xs px-1 py-0 ${toolGroupColor[tool.group_id] || ""}`}
                  >
                    {toolGroupLabel[tool.group_id] || tool.group_id}
                  </Badge>
                </div>
                <p className="text-xs text-muted-foreground mt-0.5 truncate" title={tool.description}>
                  {tool.description}
                </p>
              </div>
              {stats.tool_call_counts[tool.name] && (
                <span className="text-xs text-amber-400 font-medium">
                  ×{stats.tool_call_counts[tool.name]}
                </span>
              )}
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
