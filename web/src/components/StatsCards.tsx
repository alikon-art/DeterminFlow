import { Layers, MessageSquare, Thermometer, Wifi, Wrench } from "lucide-react";
import { SystemStatus } from "../types";

interface StatsCardsProps {
  status: SystemStatus;
}

export default function StatsCards({ status }: StatsCardsProps) {
  const cards = [
    {
      label: "活跃会话",
      value: status.active_sub_count,
      suffix: `/ ${status.total_sessions}`,
      icon: MessageSquare,
      iconColor: "text-green-500",
      borderColor: "border-green-500/20",
      pulse: status.active_sub_count > 0,
    },
    {
      label: "工具调用",
      value: status.event_bus_stats.total_tool_calls,
      suffix: "次",
      icon: Wrench,
      iconColor: "text-amber-500",
      borderColor: "border-amber-500/20",
    },
    {
      label: "提示词版本",
      value: `v${status.prompt_version}`,
      icon: Layers,
      iconColor: "text-indigo-500",
      borderColor: "border-indigo-500/20",
    },
    {
      label: "温度",
      value: status.temperature.toFixed(1),
      icon: Thermometer,
      iconColor: "text-cyan-500",
      borderColor: "border-cyan-500/20",
    },
    {
      label: "MCP 状态",
      value: status.mcp_connected ? "已连接" : "断开",
      suffix: status.mcp_connected ? `(${status.mcp_tools_count} 工具)` : "",
      icon: Wifi,
      iconColor: status.mcp_connected ? "text-green-500" : "text-red-500",
      borderColor: status.mcp_connected ? "border-green-500/20" : "border-red-500/20",
    },
  ];

  return (
    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-4" role="region" aria-label="系统状态统计">
      {cards.map((card) => {
        const Icon = card.icon;
        return (
          <div
            key={card.label}
            className={`p-4 rounded-lg bg-white border ${card.borderColor} hover:shadow-sm transition-shadow`}
            role="article"
            aria-label={`${card.label}: ${card.value}${card.suffix || ''}`}
          >
            <div className="flex items-center justify-between mb-2">
              <Icon size={18} className={card.iconColor} aria-hidden="true" />
              {card.pulse && (
                <span className="w-2 h-2 rounded-full bg-green-500 status-running" aria-hidden="true" />
              )}
            </div>
            <div className="text-2xl font-bold text-foreground">
              {card.value}
              {card.suffix && <span className="text-sm font-normal text-muted-foreground ml-1">{card.suffix}</span>}
            </div>
            <div className="text-xs text-muted-foreground mt-1">{card.label}</div>
          </div>
        );
      })}
    </div>
  );
}
