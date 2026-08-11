// ============ 会话相关类型 ============

export interface Session {
  session_id: string;
  type: "main" | "sub";
  parent_id: string | null;
  status: "running" | "streaming" | "completed" | "error" | "waiting" | "idle";
  task: string;
  message_count: number;
  created_at: string;
  updated_at: string;
  last_message: string;
  agent_type?: string;
  workspace_path?: string;
  project_name?: string;
}

export interface SessionDetail extends Session {
  system_prompt: string;
  messages: Message[];
  has_graph?: boolean;
  runtime_scope?: "interactive" | "workflow";
  model_id?: string | null;
  model_params?: Record<string, unknown>;
  workspace_path?: string;
  token_usage?: TokenUsage;
  last_error?: {
    code: string;
    message: string;
    occurred_at: string;
  };
}

export interface ModelProvider {
  id: string;
  name: string;
  provider_type: string;
  category?: string;
  base_url: string;
  api_key: string;
  models: string[];
  maxContextTokens?: number;
  models_config?: Record<string, {
    maxContextTokens?: number;
    provider_type?: string;
  }>;
  hyperparameter_values: Record<string, unknown>;
  managed_by?: string;
  error_messages?: Record<string, string>;
  is_managed?: boolean;
  capabilities?: {
    provider_type: string;
    reasoning_efforts: string[];
    model_params: Record<string, ModelParamFieldSchema>;
  };
}

export interface ModelParamFieldSchema {
  type: "boolean" | "select" | "number" | "range" | "json_mode";
  label: string;
  default: unknown;
  options?: string[];
  option_labels?: Record<string, string>;
  min?: number;
  max?: number;
  step?: number;
  nullable?: boolean;
}

export interface ProviderSchema {
  provider_type: string;
  display_name: string;
  default_base_url: string;
  api_format: "openai" | "openai_responses" | "anthropic";
  reasoning_efforts: string[];
  model_params: Record<string, ModelParamFieldSchema>;
  hyperparams: Record<string, {
    type: "boolean" | "select" | "number";
    default: unknown;
    label: string;
    options?: string[];
    min?: number;
    max?: number;
  }>;
}

export interface SessionTree {
  main: Session;
  children: Session[];
}

export interface SessionTrees {
  trees: SessionTree[];
}

// ============ 消息相关类型 ============

export interface ToolCallFunction {
  name: string;
  arguments: string;
  result?: string;  // 预合并后的工具执行结果（供 AssistantMsg 的 ToolCallCard 展示）
}

export interface ToolCall {
  id: string;
  type: "function";
  function: ToolCallFunction;
}

export interface CompressionEventData {
  type: "full" | "micro" | "reactive";
  summary?: string;
  original_count: number;
  compressed_count: number;
}

export interface MessageAttachment {
  name: string;
  absolute_path: string;
}

export interface Message {
  id?: string;             // 递增消息 ID（"msg_00001"）
  type?: string;           // 路由字段：user/assistant/tool/compression_divider/plan_progress 等
  content?: string;
  role?: string;           // 向后兼容旧格式 role: "user"/"assistant"/"tool"/"system"
  name?: string;           // OpenAI name 字段，区分消息来源（如 "agent_abc123"）
  source?: string;         // 消息来源标识: "human" | "agent:<session_id>" | "system"
  reasoning_content?: string;  // DeepSeek V4 思维链内容
  tool_calls?: ToolCall[];
  tool_call_id?: string;
  compression_event?: CompressionEventData;  // 压缩标记（旧格式兼容）
  display?: string;        // 前端渲染路由（旧格式兼容）
  event?: CompressionEventData;  // 压缩事件（新格式，与 type="compression_divider" 搭配）
  strategy?: string;       // 压缩策略（"full" / "micro" / "reactive"，消息级别字段）
  injection_meta?: InjectionMeta[];  // 用户消息注入元信息
  attachments?: MessageAttachment[]; // UI 附件元数据；正文仍保留发给 LLM 的绝对路径
  // Recursion Limit 相关字段
  tool_rounds?: number;              // recursion_limit_reached: 已执行工具轮数
  limit?: number;                    // recursion_limit_reached: 递归上限值
  // Content Safety 相关字段
  detail?: string;                    // content_safety_warning: 错误详情
  session_id?: string;                // content_safety_warning: 所属会话 ID
  diagnostic_result?: {               // content_safety_diagnostic: 诊断结果
    triggered_by: string;
    identified_message_type: string;
    message_preview: string;
    summary: string;
    diagnostic_steps: Array<{
      step: number;
      subset: string;
      result: string;
    }>;
  };
}

export interface InjectionMeta {
  name: string;           // 注入section名称
  content: string;        // 注入内容
  token_estimate: number; // 估算token数
}

// ============ 提示词相关类型 ============

export interface PromptData {
  prompt: string;
  version: number;
  last_modified: string;
}

export interface PromptHistoryEntry {
  version: number;
  old_prompt: string;
  new_prompt: string;
  reason: string;
  timestamp: string;
}

// ============ 系统状态类型 ============

export interface SystemStatus {
  main_session: Session | null;
  main_sessions?: Session[];
  active_sub_count: number;
  total_sessions: number;
  prompt_version: number;
  prompt_last_modified: string;
  temperature: number;
  mcp_connected: boolean;
  mcp_servers?: string[];
  mcp_tools_count: number;
  event_bus_stats: EventBusStats;
}

export interface EventBusStats {
  total_tool_calls: number;
  total_llm_calls: number;
  tool_call_counts: Record<string, number>;
  connected_clients: Record<string, number>;
  event_log_size: number;
}

// ============ 工具类型 ============

export interface ToolGroup {
  id: string;
  name: string;
  description: string;
}

export interface ToolInfo {
  name: string;
  description: string;
  group_id: string;
  parameters: Record<string, unknown>;
}

// ============ 图结构类型 ============

export interface GraphNode {
  id: string;
  label: string;
  type: "llm" | "tool" | "check";
  description: string;
}

export interface GraphEdge {
  source: string;
  target: string;
  label: string;
  conditional?: boolean;
}

export interface GraphStructure {
  name: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
}

// ============ Token 监控类型 ============

export interface TokenUsage {
  api: {
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
    cached_tokens: number;
    reasoning_tokens: number;
  };
  estimated: {
    system_prompt_tokens: number;
    tool_result_tokens: number;
    total_tokens: number;
  };
  max_context_tokens: number;
  model_id: string;
  llm_call_count: number;
  updated_at: string;
}

// ============ WebSocket 事件类型 ============

export type WSChatEvent =
  | { type: "stream_start"; session_id?: string }
  | { type: "token"; content: string; session_id?: string }
  | { type: "reasoning_token"; content: string; session_id?: string }
  | { type: "tool_call_delta"; index: number; id?: string; name?: string; args_delta: string; session_id?: string }
  | { type: "tool_start"; name: string; args: Record<string, unknown>; run_id: string; index?: number; session_id?: string }
  | { type: "tool_end"; name: string; result: string; run_id: string; status?: "completed" | "failed" | "cancelled"; session_id?: string }
  | { type: "chain_end"; messages: Message[]; session_id?: string; token_usage?: TokenUsage }
  | { type: "stream_end"; session_id?: string }
  | { type: "history"; messages: Message[]; session_id?: string; token_usage?: TokenUsage }
  | { type: "notification"; data: NotificationData }
  | { type: "error"; message: string; terminal?: boolean; session_id?: string }
  | { type: "llm_usage"; data: TokenUsage; session_id?: string }
  | { type: "pong" };

export type WSEventData =
  | { type: "session_update"; action: string; session_id: string; [key: string]: unknown }
  | { type: "notification"; data: NotificationData }
  | { type: "status_update"; data: StatusUpdateData }
  | { type: "tool_start"; name: string; args: Record<string, unknown>; run_id: string }
  | { type: "tool_end"; name: string; result: string; run_id: string }
  | { type: "llm_start"; session_id: string }
  | { type: "approval_request"; request_id: string; session_id: string; tool_name: string; command: string; workspace: string; created_at: string; expires_at: string }
  | { type: "approval_resolved"; request_id: string; result: string; resolved_at: string }
  | { type: "pong" };

export interface NotificationData {
  type: string;
  from: string;
  content: string;
  status?: string;
  task?: string;
  timestamp: string;
}

export interface StatusUpdateData {
  sessions: Session[];
  active_sub_count: number;
  total_sessions: number;
  main_session_id: string | null;
  event_stats: EventBusStats;
}

// ============ 页签类型 ============

export type TabType = "chat" | "dashboard" | "graph" | "roundtable" | "orchestration" | "workflow" | "skills" | "rules" | "system-prompt" | "settings" | "compression-config" | "cron";

// ============ 配置管理类型 ============

export interface ConfigItemMeta {
  key: string;
  label: string;
  group: string;
  type: "string" | "number" | "boolean" | "select";
  sensitive?: boolean;
  readonly?: boolean;
  min?: number;
  max?: number;
  step?: number;
  options?: string[];
}

export interface ConfigResponse {
  config: Record<string, string | number | boolean>;
  meta: ConfigItemMeta[];
}

export interface UpdateConfigResponse {
  success: boolean;
  config: Record<string, string | number | boolean>;
}

// ============ 编排相关类型 ============

export interface PromptSectionData {
  name: string;
  content: string;
  token_estimate: number;
  cache_break: boolean;
  cache_break_reason: string;
  enabled: boolean;
  workflow_only: boolean;
  order: number;
}

export interface TemplateVariable {
  key: string;
  name: string;
  description: string;
  default: string;
  required: boolean;
}

export interface AgentDefinitionData {
  agent_type: string;
  description: string;
  prompt_template: string;
  tools: string[] | null;
  disallowed_tools: string[] | null;
  model: string | null;
  max_turns: number;
  system_prompt_template: string;
  copy_main_workspace: boolean | null;
  visible_skill_group_ids?: string[] | null;
  visible_rule_group_ids?: string[] | null;
  extension_options?: Record<string, Record<string, unknown>> | null;
  model_params?: Record<string, unknown> | null;
}

export type OrchestrationSubTab = "prompts" | "agents" | "tools" | "user-injection";

export interface OrchestrationPreviewResult {
  effective_prompt: string;
  total_tokens: number;
  sections: { name: string; token_estimate: number; cache_break: boolean }[];
  sections_count: number;
}

// ============ 技能/规则组类型 ============

export interface SkillGroup {
  id: string;
  name: string;
  description: string;
  skill_ids: string[];
}

export interface RuleGroup {
  id: string;
  name: string;
  description: string;
  rule_ids: string[];
}

// ============ 圆桌会议类型 ============

export interface Seat {
  seat_id: string;
  role_name: string;
  system_prompt: string;
  temperature: number;
  model_name: string | null;
  allowed_tools: string[] | null;
  is_moderator: boolean;
  status: "idle" | "speaking" | "thinking" | "done";
}

export interface TranscriptEntry {
  speaker_seat_id: string;
  speaker_name: string;
  content: string;
  round_number: number;
  timestamp: string;
  entry_type: "statement" | "moderator_note" | "summary" | "conclusion";
}

export interface SharedMemory {
  conclusions: { content: string; source: string; timestamp: string }[];
  consensus: string[];
  controversies: string[];
  summaries: { round: number; content: string; timestamp: string }[];
  structured_conclusion?: StructuredConclusion | null;
}

export interface StructuredConclusion {
  summary: string;
  consensus: string[];
  disagreements: string[];
  pending_verification: string[];
  action_items: string[];
}

export interface CompressorConfig {
  enabled: boolean;
  window_size: number;
  summary_interval: number;
}

export interface RoundtableSession {
  session_id: string;
  topic: string;
  status: "waiting" | "discussing" | "paused" | "ended";
  seats: Seat[];
  current_round: number;
  max_rounds: number;
  current_speaker: string | null;
  current_speaker_seat_id: string | null;
  transcript: TranscriptEntry[];
  transcript_count: number;
  seat_count: number;
  created_at: string;
  ended_at: string | null;
  strategy: string;
  shared_memory?: SharedMemory;
  compressor?: CompressorConfig;
  event_revision?: number;
  active_turn?: {
    seat_id: string;
    speaker_name: string;
    content: string;
    round: number;
  } | null;
}

export interface RoundtableSummary {
  session_id: string;
  topic: string;
  status: "waiting" | "discussing" | "paused" | "ended";
  seat_count: number;
  current_round: number;
  max_rounds: number;
  current_speaker: string | null;
  transcript_count: number;
  created_at: string;
  ended_at: string | null;
  strategy: string;
}

export interface CreateRoundtableRequest {
  topic: string;
  seats: {
    role_name: string;
    system_prompt: string;
    temperature?: number;
    model_name?: string | null;
    is_moderator?: boolean;
  }[];
  max_rounds?: number;
  strategy?: string;
  compressor?: {
    enabled: boolean;
    window_size?: number;
    summary_interval?: number;
  } | null;
}

// ============ 圆桌会议 WebSocket 事件类型 ============

export interface ModeratorDecision {
  action: "select_speaker" | "new_round" | "conclude" | "summarize";
  speaker_id?: string;
  reason?: string;
}

export type WSRoundtableEvent = { roundtable_revision?: number } & (
  | { type: "rt_started"; roundtable_id: string; topic: string; seats: Seat[]; max_rounds: number; strategy?: string }
  | { type: "rt_turn_start"; roundtable_id: string; seat_id: string; speaker_name: string; round: number; is_moderator_thinking?: boolean }
  | { type: "rt_token"; roundtable_id: string; seat_id: string; content: string }
  | { type: "rt_turn_end"; roundtable_id: string; seat_id: string; speaker_name: string; round: number; full_content: string }
  | { type: "rt_round_end"; roundtable_id: string; round: number }
  | { type: "rt_ended"; roundtable_id: string; total_rounds: number; transcript_count: number }
  | { type: "rt_start_result"; success: boolean; message: string }
  | { type: "speaker_selected"; roundtable_id: string; seat_id: string; speaker_name: string; round: number; reason: string }
  | { type: "moderator_decision"; roundtable_id: string; decision: ModeratorDecision }
  | { type: "roundtable_summary"; roundtable_id: string; round: number; content: string; source: string }
  | { type: "roundtable_conclusion"; roundtable_id: string; content: string; source: string; total_rounds: number; structured?: StructuredConclusion }
  | { type: "rt_seat_added"; roundtable_id: string; seat: Seat }
  | { type: "rt_seat_removed"; roundtable_id: string; seat_id: string; role_name: string }
  | { type: "rt_paused"; roundtable_id: string; round: number }
  | { type: "rt_resumed"; roundtable_id: string; round: number }
  | { type: "rt_inject_result"; success: boolean; message?: string }
  | { type: "rt_nominate_result"; success: boolean; message?: string }
);

// ============ 审批请求类型 ============

export interface ApprovalRequest {
  request_id: string;
  session_id: string;
  tool_name: string;
  command: string;
  workspace: string;
  status: "pending" | "approved" | "rejected" | "timeout";
  created_at: string;
  expires_at: string;
  reason?: string;
}

// ============ 预设短语类型 ============

export interface PresetPhrase {
  id: string;
  label: string;
  content: string;
}

// ============ 工作流编排类型 ============

/** 工作流节点定义（与后端 WorkflowNode 对齐） */
export interface WorkflowNodeDef {
  id: string;
  label: string;
  node_type: string;               // "agent" | "approval" | "script" | "subprocess"
  agent_type: string;              // 仅 node_type="agent" 时有效
  system_prompt_template: string;
  first_message: string;
  position: { x: number; y: number };
  var_bindings?: Record<string, { original_value: string; var_key?: string }>;
  node_params?: Record<string, unknown>; // 节点类型特有参数，可包含 script_argv 等结构化值
  auto_flow?: boolean;              // Agent 自动流转
  enable_complete_node_task?: boolean;  // 是否注入 complete_node_task 工具
  output_variable?: string;         // 输出变量 key
  enable_reject_upstream?: boolean; // 是否注入 reject_upstream 工具
  max_reject_count?: number;        // 最大拒绝次数
  save_output_to_file?: boolean;    // 是否将LLM最后输出保存到文件
  output_file_path?: string;        // 保存路径（支持绝对/相对/{{key}}占位符）
  require_non_empty_output?: boolean; // 是否要求最后一条 LLM 输出非空
  json_output_field?: string;       // 要求最小字数的 JSON 字符串字段路径
  json_output_field_min_chars?: number; // JSON 字段字数必须严格大于此值
  model_override?: string;          // 模型覆盖（格式 "provider_id:model_name"，空则使用 agent 类型默认模型，支持 {{key}} 占位符）
  sub_workflow_id?: string | null;  // 子流程节点：引用的目标流程 ID
  sub_scheme_id?: string | null;    // 子流程节点：使用的执行方案 ID（空=全部执行）
  sub_workflow_params?: Record<string, { value: string; use_default: boolean }>;  // 子流程节点：参数值映射
  auto_retry_count?: number;         // 节点失败后的自动重试次数（0=关闭）
  auto_retry_interval_seconds?: number; // 自动重试固定间隔（秒）
  fail_auto_skip?: boolean;         // 失败自动跳过：开启后节点执行失败自动继续下一节点
}

export type NodeExecutionStatus =
  | "pending"
  | "running"
  | "retry_waiting"
  | "completed"
  | "failed"
  | "waiting_approval"
  | "skipped";

/** 单次节点执行尝试，由 Core 持久化并按执行顺序返回。 */
export interface NodeAttemptHistoryEntry {
  attempt_id?: string;
  attempt_number?: number;
  status?: string;
  trigger?: "initial" | "auto_retry" | "manual_retry" | string;
  started_at?: string | null;
  completed_at?: string | null;
  error?: string;
  session_id?: string;
  automatic_retry_count?: number;
  input_snapshot?: unknown;
}

/** 工作流连线定义（与后端 WorkflowEdge 对齐） */
export interface WorkflowEdgeDef {
  id: string;
  source: string;
  target: string;
  /** 条件表达式（仅条件网关的出边携带） */
  condition?: {
    expression: string;
    label: string;
    is_default: boolean;
  } | null;
}

/** 工作流网关定义（与后端 WorkflowGateway 对齐） */
export interface WorkflowGatewayDef {
  id: string;
  gateway_type: "parallel" | "converge" | "condition" | "loop";
  label: string;
  position: { x: number; y: number };
  converge_gateway_id?: string | null;
}

/** 单节点运行时状态 */
export interface NodeExecutionInfo {
  node_id: string;
  status: NodeExecutionStatus;
  session_id: string;
  summary: string;
  error?: string;
  rejection_count?: number;
  rejection_reason?: string;
  started_at?: string | null;
  completed_at?: string | null;
  outputs?: Record<string, string>;
  stdout?: string;
  stderr?: string;
  attempt_count?: number;
  automatic_retry_count?: number;
  next_retry_at?: string | null;
  attempt_history?: NodeAttemptHistoryEntry[];
  input_snapshot?: unknown;
  available_actions?: string[];
  parent_node_id?: string;
  /** 子流程内部节点状态（node_type="subprocess" 时有效） */
  child_states?: Record<string, NodeExecutionInfo>;
  /** 是否因失败策略被跳过（兼容旧任务数据） */
  is_skipped?: boolean;
}

/** 工作流运行时整体状态 */
export interface WorkflowState {
  workflow_id: string;
  status: "idle" | "running" | "completed" | "failed" | "stopped";
  current_node_id: string | null;
  node_states: Record<string, NodeExecutionInfo>;
  current_run_id?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
}

/** 工作流参数变量定义（与后端 WorkflowVariable 对齐，参照 bk-sops） */
export interface WorkflowVariableOption {
  name: string;   // 展示名
  value: string;  // 填充值
}

/** 节点参数 schema 选项（select 类型） */
export interface ParamSchemaOption {
  name: string;
  value: string;
}

export interface WorkflowVariable {
  key: string;                          // 唯一标识（用于 {{key}} 占位符引用）
  name: string;                         // 前端展示名
  type: "text" | "textarea" | "select" | "file" | "list" | "dict";  // 变量类型
  default: string;                      // 默认值
  required: boolean;                    // 是否必填
  description: string;                  // 变量说明
  options: WorkflowVariableOption[];    // select 类型选项
  source_type?: "input" | "output";     // 变量来源
  source_node_id?: string;              // 输出变量来源节点 ID
  hidden?: boolean;                     // 是否隐藏（填参页面默认折叠）
}

/** 执行方案定义 */
export interface ExecutionScheme {
  id: string;
  name: string;
  selected_node_ids: string[];
  created_at: string;
  updated_at: string;
}

/** 工作流定义完整数据结构（API 返回） */
export interface WorkflowDefinition {
  workflow_id: string;
  name: string;
  version: number;
  created_at: string;
  updated_at: string;
  nodes: WorkflowNodeDef[];
  edges: WorkflowEdgeDef[];
  variables?: WorkflowVariable[];
  gateways?: WorkflowGatewayDef[];
  execution_schemes?: ExecutionScheme[];
  start_position?: { x: number; y: number };
  end_position?: { x: number; y: number };
}

/** 工作流列表摘要 */
export interface WorkflowSummary {
  workflow_id: string;
  name: string;
  node_count: number;
  version: number;
  created_at: string;
  updated_at: string;
  status: string;
  running_tasks?: number;
}

/** 单个工作流完整响应（定义 + 状态） */
export interface WorkflowDetailResponse {
  definition: WorkflowDefinition;
}

/** 工作流任务实例（编辑与运行分离架构） */
export interface WorkflowTask {
  task_id: string;
  workflow_id: string;
  name: string;
  status: "pending" | "pre_running" | "resume_pending" | "running" | "retry_waiting" | "completed" | "failed" | "stopped";
  current_node_id: string | null;
  run_id: string | null;
  main_session_id?: string | null;  // Task 所属的 Main session ID
  main_takeover?: boolean;  // 是否启用逐 Agent 节点的 Main 接管审批
  created_at: string;
  updated_at?: string;
  started_at: string | null;
  completed_at: string | null;
  node_states: Record<string, NodeExecutionInfo>;
  parameter_values?: Record<string, string>;
  snapshot_variables?: WorkflowVariable[];
  disabled_node_ids?: string[];  // 任务创建时被取消勾选的节点ID
  scheme_id?: string | null;  // 任务来源执行方案 ID
  workspace_override?: string | null;  // 用户指定的工作空间覆盖路径（空则使用默认路径）
  workspace_mode?: "task_isolated" | "named_shared" | "legacy_shared";
  workspace_ref?: string | null;
  progress?: { completed: number; total: number };
  workflow_name?: string;  // 仅全局任务历史接口附带
}

/** 任务列表分页响应 */
export interface TaskListResponse {
  tasks: WorkflowTask[];
  total: number;
  page: number;
  page_size: number;
}

/** 任务 + 定义完整响应 */
export interface TaskDetailResponse {
  task: WorkflowTask;
  definition: WorkflowDefinition;
}

/** 节点消息响应 */
export interface NodeMessageResponse {
  node_id: string;
  session_id: string;
  messages: Message[];
  message_count: number;
  node_status: string;
  summary?: string;
  error?: string;
  agent_type?: string;
}

/** Agent 类型选项（从 API 动态加载） */
export interface AgentTypeOption {
  agent_type: string;
  description: string;
  available_for_sub_session?: boolean;
}

/** Agent 类型分组 */
export interface AgentTypeGroup {
  id: string;
  name: string;
  types: AgentTypeOption[];
}

/** 节点类型选项（从 /api/workflows/node-types/list 动态加载） */
export interface NodeTypeOption {
  node_type: string;
  label: string;
  icon: string;
  params_schema: {
    key: string;
    label: string;
    type: string;
    required: boolean;
    default: string;
    description: string;
    placeholder?: string;
    options?: ParamSchemaOption[];
  }[];
}

/**
 * Agent 类型颜色映射（用于 ReactFlow 等可视化组件，值对齐 Tailwind 标准色板）
 * coder=green-500, reviewer=blue-500, researcher=amber-500, reader=violet-500, default=indigo-500
 */
export const AGENT_TYPE_COLORS: Record<string, string> = {
  coder: "#22C55E",
  reviewer: "#3B82F6",
  researcher: "#F59E0B",
  reader: "#8B5CF6",
  default: "#6366F1",
};

/**
 * 节点类型颜色映射（用于 ReactFlow 画布节点，值对齐 Tailwind 标准色板）
 * agent=indigo-500, approval=amber-500, script=cyan-500
 */
export const NODE_TYPE_COLORS: Record<string, string> = {
  agent: "#6366F1",
  approval: "#F59E0B",
  script: "#06B6D4",
  subprocess: "#10B981",
};

/**
 * 工作流节点状态颜色（用于 ReactFlow 画布节点状态指示，值对齐 Tailwind 标准色板）
 * pending=slate-400, running=blue-500, completed=green-500, failed=red-500
 */
export const NODE_STATUS_COLORS: Record<string, string> = {
  pending: "#94A3B8",
  running: "#3B82F6",
  completed: "#22C55E",
  failed: "#EF4444",
};

// ============================================================
// 工作流 WebSocket 实时事件类型（替代 HTTP 轮询）
// ============================================================

/** 工作流节点消息实时推送事件 */
export interface WfNodeMessageEvent {
  type: "wf_node_message";
  workflow_id: string;
  node_id: string;
  session_id: string;
  message: Message;
}

/** 工作流节点状态变更事件 */
export interface WfNodeStatusEvent {
  type: "wf_node_status";
  workflow_id: string;
  node_id: string;
  session_id: string;
  status: NodeExecutionStatus | "success" | "failure";
  summary: string;
  error: string;
  /** 子流程内部节点时，标记所属父节点 ID */
  parent_node_id?: string;
}

/** 工作流任务状态更新事件（含所有节点状态） */
export interface WfTaskUpdateEvent {
  type: "wf_task_update";
  workflow_id: string;
  task_id: string;
  status: WorkflowTask["status"];
  current_node_id: string | null;
  node_states: Record<string, NodeExecutionInfo>;
  started_at: string | null;
  completed_at: string | null;
  created_at?: string;
  updated_at?: string;
  name?: string;
  main_session_id?: string | null;
  main_takeover?: boolean;
  workspace_mode?: "task_isolated" | "named_shared" | "legacy_shared";
  workspace_ref?: string | null;
  progress?: { completed: number; total: number };
}

/** Chat Main 会话通道中的后台工作流任务更新。 */
export interface WorkflowTaskUpdateEvent extends Omit<WfTaskUpdateEvent, "type"> {
  type: "workflow_task_update";
  session_id: string;
}

/** 审批节点文件信息 */
export interface ApprovalFileInfo {
  path: string;
  content: string;
  exists: boolean;
}

/** 审批节点请求事件（WebSocket 推送） */
export interface WfApprovalRequiredEvent {
  type: "wf_approval_required";
  workflow_id: string;
  task_id: string;
  node_id: string;
  node_label: string;
  files: ApprovalFileInfo[];
  placeholder: string;
}

/** 所有 workflow 事件联合类型 */
export type WorkflowEvent =
  | WfNodeMessageEvent
  | WfNodeStatusEvent
  | WfTaskUpdateEvent
  | WfApprovalRequiredEvent;

// ============ 脚本库类型 ============

/** 脚本库分组 */
export interface ScriptLibraryGroup {
  name: string;
  script_count: number;
}

/** 脚本库脚本条目 */
export interface ScriptLibraryScript {
  group: string;
  name: string;
  script_type: "shell" | "python";
}

// ============ 流式聊天共享类型 ============

/** 工具调用状态（canonical conversation adapters 共用） */
export interface ToolCallState {
  id?: string | null;
  /** Wire tool_call_delta slot, retained in active stream snapshots for reconnect. */
  index?: number;
  name: string;
  args: string;
  run_id: string;
  result?: string;
  status: "building" | "running" | "completed" | "failed" | "cancelled";
}

/** 流式片段（canonical conversation adapters 共用） */
export type StreamingSegment =
  | { type: "text"; content: string }
  | { type: "reasoning"; content: string }
  | { type: "tool"; tool: ToolCallState };
