/**
 * 工具函数集合
 */

// ============ 时间格式化 ============

export function formatTime(isoString: string): string {
  if (!isoString) return "-";
  const date = new Date(isoString);
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function formatRelativeTime(isoString: string): string {
  if (!isoString) return "-";
  const date = new Date(isoString);
  const now = new Date();
  const diff = now.getTime() - date.getTime();

  if (diff < 60000) return "刚刚";
  if (diff < 3600000) return `${Math.floor(diff / 60000)}分钟前`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}小时前`;
  return `${Math.floor(diff / 86400000)}天前`;
}

// ============ 状态颜色映射 ============

export const statusConfig: Record<string, { color: string; bg: string; dotColor: string; label: string }> = {
  running: { color: "text-green-400", bg: "bg-green-500/20", dotColor: "bg-green-400", label: "运行中" },
  streaming: { color: "text-cyan-400", bg: "bg-cyan-500/20", dotColor: "bg-cyan-400", label: "流式传输" },
  completed: { color: "text-blue-400", bg: "bg-blue-500/20", dotColor: "bg-blue-400", label: "已完成" },
  error: { color: "text-red-400", bg: "bg-red-500/20", dotColor: "bg-red-400", label: "错误" },
  waiting: { color: "text-amber-400", bg: "bg-amber-500/20", dotColor: "bg-amber-400", label: "等待中" },
  idle: { color: "text-slate-400", bg: "bg-slate-500/20", dotColor: "bg-slate-400", label: "空闲" },
};

export function getStatusConfig(status: string) {
  return statusConfig[status] || statusConfig.error;
}

// ============ JSON 格式化 ============

export function safeJsonParse(str: string): unknown {
  try {
    return JSON.parse(str);
  } catch {
    return str;
  }
}

export function prettyJson(obj: unknown): string {
  try {
    return JSON.stringify(obj, null, 2);
  } catch {
    return String(obj);
  }
}

// ============ 截断文本 ============

export function truncate(text: string, maxLength: number): string {
  if (!text || text.length <= maxLength) return text || "";
  return text.slice(0, maxLength) + "...";
}

// ============ 工具显示名映射（工具名 → 中文，展示层映射，name 仍作技术键）============
// 来源：后端 /api/tools 注册清单（39 个）。未收录的新工具回退原名显示。
export const TOOL_LABELS: Record<string, string> = {
  // config
  get_system_prompt: "查看系统提示词",
  update_system_prompt: "修改系统提示词",
  list_agent_types: "列出代理类型",
  // coding
  read_file: "读取文件",
  write_to_file: "写入文件",
  replace_in_file: "替换文件内容",
  search_files: "搜索文件内容",
  search_file: "搜索文件名",
  list_files: "列出目录文件",
  list_code_definitions: "列出代码定义",
  apply_diff: "应用差异补丁",
  execute_command: "执行命令",
  ask_user: "询问用户",
  // session_main
  create_sub_session: "创建子会话",
  check_sub_progress: "查看子会话进度",
  check_main_progress: "查看主会话进度",
  send_message: "发送消息",
  delete_session: "删除会话",
  // skills
  get_skills: "获取技能",
  skill_manage: "管理技能",
  skill_group_manage: "管理技能分组",
  // rules
  get_rules: "获取规则",
  rule_manage: "管理规则",
  rule_group_manage: "管理规则分组",
  // workflow
  set_workflow_variable: "设置工作流变量",
  start_workflow_task: "启动工作流任务",
  approve_node: "审批节点",
  list_workflows: "列出工作流",
  get_workflow: "获取工作流",
  list_tasks: "列出任务",
  get_task_status: "获取任务状态",
  stop_task: "停止任务",
  get_task_result: "获取任务结果",
  read_task_artifact: "读取任务产物",
  get_node_messages: "获取节点消息",
  retry_node: "重试节点",
  skip_node: "跳过节点",
  create_and_attach_task: "创建并附加任务",
  // cron
  cronjob: "定时任务",
};

export function getToolDisplayName(name: string): string {
  return TOOL_LABELS[name] ?? name;
}

// ============ 工具分组映射（group_id → 显示名称/颜色）============

export const toolGroupLabel: Record<string, string> = {
  memory: "记忆管理",
  coding: "编码工具",
  session_main: "主会话管理",
  communication: "子代理通信",
  config: "配置工具",
  skills: "技能工具",
  prompt: "提示词工具",
  rules: "规则工具",
  workflow: "工作流工具",
  cron: "定时任务工具",
};

export const toolGroupColor: Record<string, string> = {
  memory: "bg-purple-500/20 text-purple-400 border-purple-500/30",
  coding: "bg-green-500/20 text-green-400 border-green-500/30",
  session_main: "bg-indigo-500/20 text-indigo-400 border-indigo-500/30",
  communication: "bg-cyan-500/20 text-cyan-400 border-cyan-500/30",
  config: "bg-amber-500/20 text-amber-400 border-amber-500/30",
  skills: "bg-pink-500/20 text-pink-400 border-pink-500/30",
  rules: "bg-blue-500/20 text-blue-400 border-blue-500/30",
  workflow: "bg-violet-500/20 text-violet-400 border-violet-500/30",
  cron: "bg-teal-500/20 text-teal-400 border-teal-500/30",
};
