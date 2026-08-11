const AGENT_LABELS: Record<string, string> = {
  main: "主代理",
  subagent: "子代理",
  compressor: "上下文压缩代理",
  "bishu-novel-novel-action-writer": "笔枢·动作描写代理",
  "bishu-novel-novel-arbiter": "笔枢·剧情仲裁代理",
  "bishu-novel-novel-chapter-observer": "笔枢·章节观察代理",
  "bishu-novel-novel-character-belief": "笔枢·角色信念代理",
  "bishu-novel-novel-character-deep": "笔枢·角色深化代理",
  "bishu-novel-novel-character-maintainer": "笔枢·角色维护代理",
  "bishu-novel-novel-character-skeleton": "笔枢·角色骨架代理",
  "bishu-novel-novel-character-voice": "笔枢·角色声音代理",
  "bishu-novel-novel-description-writer": "笔枢·场景描写代理",
  "bishu-novel-novel-dialogue-writer": "笔枢·对话写作代理",
  "bishu-novel-novel-director": "笔枢·大纲导演代理",
  "bishu-novel-novel-intent-distributor": "笔枢·意图分配代理",
  "bishu-novel-novel-internal-writer": "笔枢·内在描写代理",
  "bishu-novel-novel-observer": "笔枢·世界观察代理",
  "bishu-novel-novel-outliner": "笔枢·大纲规划代理",
  "bishu-novel-novel-polisher": "笔枢·文本润色代理",
  "bishu-novel-novel-professional-polisher": "笔枢·专业润色代理",
  "bishu-novel-novel-self-critic": "笔枢·自我审校代理",
  "bishu-novel-novel-settler": "笔枢·意图导演代理",
  "bishu-novel-novel-single-writer": "笔枢·单章写作代理",
  "bishu-novel-novel-story-planner": "笔枢·故事规划代理",
  "bishu-novel-novel-storyboard-integrator": "笔枢·分镜整合代理",
  "bishu-novel-novel-style-profiler": "笔枢·风格分析代理",
  "bishu-novel-novel-transition-writer": "笔枢·过渡写作代理",
  "bishu-novel-novel-volume-outliner": "笔枢·卷纲规划代理",
  "bishu-novel-novel-world-context-trimmer": "笔枢·世界上下文精简代理",
  "bishu-novel-novel-worldbuilder-corelaws": "笔枢·世界核心法则代理",
  "bishu-novel-novel-worldbuilder-existence": "笔枢·世界存在规则代理",
  "bishu-novel-novel-worldbuilder-historyculture": "笔枢·世界历史文化代理",
  "bishu-novel-novel-worldbuilder-information": "笔枢·世界信息体系代理",
  "bishu-novel-novel-worldbuilder-society": "笔枢·世界社会体系代理",
  "bishu-novel-novel-worldbuilder-spacetime": "笔枢·世界时空体系代理",
  "bishu-novel-novel-writer": "笔枢·小说主写作代理",
};

// 插件名映射（资源前缀 → 中文名）。新插件在此扩展；未收录的插件按资源名回退可读格式。
const PLUGIN_LABELS: Record<string, string> = {
  "bishu-novel": "笔枢",
};

// Prompt Section 显示名映射（技术键 → 中文）。custom_* 为用户自建，不映射。
const SECTION_LABELS: Record<string, string> = {
  // 通用/共享
  file_structure: "文件结构",
  session_meta: "会话元信息",
  od_modes: "运行模式",
  no_em_dash: "禁用破折号",
  we_constraints: "观察约束",
  wf_report: "工作流报告",
  nw_importent: "写作要点",
  sw_importent: "写作要点",

  // novel-director 大纲导演
  nd_role: "导演角色",
  nd_memo_format: "备忘录格式",
  nd_input_guide: "输入指引",
  nd_output: "产出格式",
  nd_human_intent: "人类意图",
  nd_information_boundary: "信息边界",

  // novel-writer 小说作家
  nw_role: "写手角色",
  nw_core_rules: "核心规则",
  nw_anti_ai: "反AI味",
  nw_narrative: "叙事原则",
  nw_coherence: "连贯性",
  nw_discipline: "写作纪律",
  nw_outline_lock: "大纲锁定",
  nw_memory_engine: "记忆引擎",
  nw_output: "产出格式",
  nw_soul: "文风灵魂",
  nw_style: "风格规范",
  nw_basics: "写作基础",
  nw_slot_output: "槽位输出",
  nw_concrete: "具体化要求",
  nw_sentence: "句法要求",

  // novel-observer / novel-outliner（共享 no_* 前缀）
  no_role: "角色定义",
  no_output_format: "输出格式",
  no_input: "输入说明",
  no_output: "产出格式",
  no_constraints: "约束条件",

  // novel-settler 结算员
  ns_role: "结算员角色",
  ns_hook_ops: "伏笔操作",
  ns_output_format: "输出格式",
  ns_human_intent: "人类意图",

  // novel-character-maintainer 角色状态维护师
  ncm_role: "维护师角色",
  ncm_long_format: "长线状态格式",
  ncm_minor_format: "次要角色格式",
  ncm_input: "输入说明",
  ncm_output: "产出格式",

  // novel-storyboard-integrator 整合写手
  niw_role: "整合写手角色",
  niw_flow: "工作流程",
  niw_output: "产出格式",
  niw_constraints: "约束条件",
  niw_concrete: "具体化要求",
  niw_sentence: "句法要求",
  niw_syntax: "语法规范",
  niw_expand: "扩写规则",
  niw_polish: "润色规则",

  // novel-dialogue-writer 对话写手
  ndw_role: "对话写手角色",
  ndw_rules: "写作规则",
  ndw_output: "产出格式",
  ndw_concrete: "具体化要求",
  ndw_sentence: "句法要求",

  // novel-action-writer 动作写手
  naw_role: "动作写手角色",
  naw_rules: "写作规则",
  naw_output: "产出格式",
  naw_concrete: "具体化要求",
  naw_sentence: "句法要求",

  // novel-internal-writer 内心写手
  niw2_role: "内心写手角色",
  niw2_rules: "写作规则",
  niw2_output: "产出格式",
  niw2_concrete: "具体化要求",
  niw2_sentence: "句法要求",

  // novel-description-writer 描写写手
  ndw2_role: "描写写手角色",
  ndw2_rules: "写作规则",
  ndw2_output: "产出格式",
  ndw2_concrete: "具体化要求",
  ndw2_sentence: "句法要求",

  // novel-single-writer 单写手
  sw_role: "单写手角色",
  sw_core: "核心规则",
  sw_replace: "替换原则",
  sw_dialogue: "对话规则",
  sw_action: "动作规则",
  sw_internal: "内心规则",
  sw_description: "描写规则",
  sw_style: "风格规则",
  sw_concrete: "具体化要求",
  sw_sentence: "句法要求",
  sw_self_edit: "自检编辑",
  sw_output: "产出格式",

  // novel-worldbuilder-* 世界观各维度
  nwb1_role: "核心法则角色",
  nwb1_schema: "数据结构",
  nwb2_role: "时空地理角色",
  nwb2_schema: "数据结构",
  nwb3_role: "社会权力角色",
  nwb3_schema: "数据结构",
  nwb4_role: "历史文化角色",
  nwb4_schema: "数据结构",
  nwb5_role: "存在基础角色",
  nwb5_schema: "数据结构",
  nwb6_role: "信息传播角色",
  nwb6_schema: "数据结构",

  // novel-character-* 角色构建
  ncs_role: "阵容架构师角色",
  ncs_schema: "数据结构",
  ncb_role: "信念架构师角色",
  ncb_schema: "数据结构",
  ncd_role: "深层维度构建师角色",
  ncd_schema: "数据结构",
  cv_role: "角色声音角色",
  cv_output: "产出格式",
  sp_role: "风格分析角色",
  sp_output: "产出格式",
  nsp_role: "故事规划角色",
  nsp_schema: "数据结构",

  // novel-self-critic 自审
  nsc_role: "自审角色",
  nsc_dimensions: "审查维度",
  nsc_output: "产出格式",
  nsc_chinese_syntax_bans: "中文句法禁令",

  // novel-polisher 润色
  npl_role: "润色角色",
  npl_humanization: "人文化规则",
  npl_output: "产出格式",

  // novel-professional-polisher 专业润色
  npp_role: "专业润色角色",
  npp_dimensions: "审查维度",
  npp_rules: "润色规则",
  npp_output: "产出格式",

  // novel-intent-distributor 意图分发器
  nid_role: "意图分发角色",
  nid_rules: "分发规则",

  // novel-chapter-observer 章节观察员
  nco_role: "章节观察角色",
  nco_input: "输入说明",
  nco_discipline: "观察纪律",
  nco_outputs: "产出格式",

  // novel-arbiter 后验裁决器
  nar_role: "裁决器角色",
  nar_world: "世界裁决",
  nar_story: "故事裁决",
  nar_events: "事件裁决",
  nar_discipline: "裁决纪律",
  nar_output: "产出格式",

  // novel-transition-writer 过渡写手
  ntw_role: "过渡写手角色",
  ntw_rules: "写作规则",
  ntw_output: "产出格式",
  ntw_concrete: "具体化要求",
  ntw_sentence: "句法要求",

  // novel-world-context-trimmer 世界上下文裁剪器
  nwct_role: "裁剪器角色",
  nwct_principles: "裁剪原则",
  nwct_output: "产出格式",

  // novel-volume-outliner 卷纲规划者
  vo_role: "卷纲规划角色",
  vo_input: "输入说明",
  vo_output: "产出格式",
};

function readableResourceName(resource: string): string {
  return resource.replace(/-/g, " ").trim();
}

function matchPluginPrefix(agentType: string): { plugin: string; rest: string } | null {
  const keys = Object.keys(PLUGIN_LABELS).sort((a, b) => b.length - a.length);
  for (const key of keys) {
    if (agentType === key) return { plugin: key, rest: "" };
    if (agentType.startsWith(key + "-")) return { plugin: key, rest: agentType.slice(key.length + 1) };
  }
  return null;
}

export function getAgentDisplayName(agentType: string): string {
  if (AGENT_LABELS[agentType]) return AGENT_LABELS[agentType];
  if (agentType.startsWith("custom_")) return `自定义代理：${agentType.slice(7)}`;
  const match = matchPluginPrefix(agentType);
  if (match) {
    const pluginName = PLUGIN_LABELS[match.plugin];
    return match.rest ? `${pluginName}·${readableResourceName(match.rest)}` : pluginName;
  }
  return readableResourceName(agentType);
}

export function getSectionDisplayName(sectionName: string): string {
  if (sectionName.startsWith("custom_")) return sectionName;
  return SECTION_LABELS[sectionName] ?? sectionName;
}
