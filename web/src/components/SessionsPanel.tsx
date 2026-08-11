import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Badge } from "@/components/ui/badge";
import {
  ChevronDown, ChevronRight, Folder, MessageSquare, Plus, Trash2, X, Zap,
} from "lucide-react";
import { Session } from "../types";
import { getStatusConfig, formatRelativeTime, truncate } from "../lib/utils-helpers";
import { getAgentDisplayName } from "../lib/agent-labels";
import { useAgentTypes } from "../hooks/useAgentTypes";

interface SessionsPanelProps {
  sessions: Session[];
  viewingSessionId: string | null;
  mainSessionId: string | null;
  onViewSession: (sessionId: string) => void;
  onDeleteSession: (sessionId: string, e: React.MouseEvent) => void;
  onKillSession: (sessionId: string, e: React.MouseEvent) => void;
  onCreateSession: (agentType?: string, projectName?: string) => void;
  onDeleteProject: (projectName: string) => void;
}

const DEFAULT_PROJECT = "未分类项目";

function isWorkflowMain(session: Session): boolean {
  return session.type === "main" && (session.task || "").startsWith("Workflow:");
}

export function canDeleteMainSession(
  _session: Session,
  _activeMainSessionId: string | null,
): boolean {
  return true;
}

function SessionCard({
  session, isViewing, isSub, canDelete, canKill,
  onViewSession, onDeleteSession, onKillSession,
}: {
  session: Session; isViewing: boolean;
  isSub: boolean; canDelete: boolean; canKill: boolean;
  onViewSession: (id: string) => void;
  onDeleteSession: (id: string, e: React.MouseEvent) => void;
  onKillSession: (id: string, e: React.MouseEvent) => void;
}) {
  const cfg = getStatusConfig(session.status);
  const workflowMain = isWorkflowMain(session);
  const typeLabel = session.type === "main"
    ? (workflowMain ? "工作流" : "对话")
    : "子代理";
  const agentLabel = getAgentDisplayName(session.agent_type || (isSub ? "subagent" : "main"));
  const summary = session.task || session.last_message || (isSub ? "子代理对话" : "新对话");

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => onViewSession(session.session_id)}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onViewSession(session.session_id);
        }
      }}
      aria-label={`${typeLabel} ${agentLabel}：${summary}`}
      className={`border rounded-lg transition-colors cursor-pointer group relative ${
        isSub ? "px-2 py-1.5 ml-6 bg-slate-800/30" : "px-3 py-2.5 bg-slate-800/50"
      } ${
        isViewing
          ? "border-indigo-500/60 bg-indigo-500/10"
          : "border-slate-700/50 hover:border-indigo-500/30"
      }`}
    >
      {isViewing && <div className="absolute left-0 top-2 bottom-2 w-0.5 bg-indigo-500 rounded-r" />}
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5 min-w-0">
            <span className={`w-2 h-2 rounded-full shrink-0 ${cfg.dotColor}`} aria-hidden="true" />
            <span className="text-sm font-medium text-slate-200 truncate">{agentLabel}</span>
          </div>
          <p className="mt-1 text-xs text-muted-foreground truncate">
            {truncate(summary, isSub ? 34 : 52)}
          </p>
        </div>
        <Badge variant="outline" className={`shrink-0 text-[10px] ${workflowMain ? "text-purple-400" : cfg.color} border-current/30`}>
          {typeLabel}
        </Badge>
      </div>
      <div className="mt-1.5 flex items-center justify-between text-[11px] text-muted-foreground">
        <span>{session.message_count} 条消息 · {formatRelativeTime(session.updated_at)}</span>
        <div className="flex items-center">
          {canKill && (
            <button
              type="button"
              onClick={(event) => onKillSession(session.session_id, event)}
              aria-label={`终止对话 ${session.session_id}`}
              title="终止对话"
              className="p-1 rounded text-amber-400 hover:bg-amber-500/20 opacity-0 group-hover:opacity-100 focus:opacity-100"
            >
              <X size={13} />
            </button>
          )}
          {canDelete && (
            <button
              type="button"
              onClick={(event) => onDeleteSession(session.session_id, event)}
              aria-label={`删除对话 ${session.session_id}`}
              title="删除对话"
              className="p-1 rounded text-red-400 hover:bg-red-500/20 opacity-0 group-hover:opacity-100 focus:opacity-100"
            >
              <Trash2 size={13} />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

export default function SessionsPanel({
  sessions, viewingSessionId, mainSessionId,
  onViewSession, onDeleteSession, onKillSession, onCreateSession, onDeleteProject,
}: SessionsPanelProps) {
  const [collapsedProjects, setCollapsedProjects] = useState<Set<string>>(new Set());
  const [collapsedConversations, setCollapsedConversations] = useState<Set<string>>(new Set());
  const [agentMenuOpen, setAgentMenuOpen] = useState(false);
  const [createDialogOpen, setCreateDialogOpen] = useState(false);
  const [selectedAgentType, setSelectedAgentType] = useState("main");
  const [projectName, setProjectName] = useState("");
  const [targetProjectName, setTargetProjectName] = useState<string | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const projectInputRef = useRef<HTMLInputElement>(null);
  const { agentTypes } = useAgentTypes({ endpoint: "/api/agent-types", filterSubSessionOnly: true });

  const groups = useMemo(() => {
    const mainSessions = sessions.filter((session) => session.type === "main");
    const subSessions = sessions.filter((session) => session.type === "sub");
    const byProject = new Map<string, { main: Session; subs: Session[] }[]>();
    for (const main of mainSessions) {
      const name = main.project_name?.trim() || DEFAULT_PROJECT;
      const conversations = byProject.get(name) || [];
      conversations.push({
        main,
        subs: subSessions.filter((session) => session.parent_id === main.session_id),
      });
      byProject.set(name, conversations);
    }
    return Array.from(byProject, ([name, conversations]) => ({ name, conversations }));
  }, [sessions]);

  const projectNames = useMemo(() => groups.map((group) => group.name), [groups]);

  useEffect(() => {
    if (!agentMenuOpen) return;
    const close = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) setAgentMenuOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [agentMenuOpen]);

  useEffect(() => {
    if (!createDialogOpen) return;
    projectInputRef.current?.focus();
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape") setCreateDialogOpen(false);
    };
    document.addEventListener("keydown", close);
    return () => document.removeEventListener("keydown", close);
  }, [createDialogOpen]);

  const openCreateDialog = useCallback((agentType: string, existingProject?: string) => {
    setSelectedAgentType(agentType);
    setTargetProjectName(existingProject || null);
    setProjectName(existingProject || "");
    setAgentMenuOpen(false);
    setCreateDialogOpen(true);
  }, []);

  const createConversation = useCallback(() => {
    const name = projectName.trim();
    if (!name) return;
    onCreateSession(selectedAgentType, name);
    setCreateDialogOpen(false);
  }, [onCreateSession, projectName, selectedAgentType]);

  const toggleSetValue = (setter: React.Dispatch<React.SetStateAction<Set<string>>>, key: string) => {
    setter((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  return (
    <>
      <ScrollArea className="h-full">
        <div className="px-3 py-2 space-y-2">
          <div className="relative" ref={menuRef}>
            <div className="flex rounded-lg overflow-hidden">
              <button
                type="button"
                onClick={() => openCreateDialog("main")}
                className="flex-1 flex items-center justify-center gap-2 px-3 py-2 bg-indigo-500/15 text-indigo-400 hover:bg-indigo-500/25 text-xs font-medium"
              >
                <Plus size={14} />新建项目
              </button>
              <button
                type="button"
                onClick={() => setAgentMenuOpen((open) => !open)}
                aria-haspopup="menu"
                aria-expanded={agentMenuOpen}
                aria-label="选择代理类型"
                className="px-2 bg-indigo-500/15 text-indigo-400 hover:bg-indigo-500/25 border-l border-indigo-500/30"
              >
                <ChevronDown size={14} className={agentMenuOpen ? "rotate-180" : ""} />
              </button>
            </div>
            {agentMenuOpen && (
              <div className="absolute left-0 right-0 mt-1 z-50 bg-slate-800 border border-border/60 rounded-lg shadow-xl py-1 max-h-72 overflow-y-auto" role="menu">
                {agentTypes.map((type) => (
                  <button
                    key={type.agent_type}
                    type="button"
                    onClick={() => openCreateDialog(type.agent_type)}
                    role="menuitem"
                    className="w-full flex items-start gap-3 px-3 py-2 text-left hover:bg-indigo-500/10"
                  >
                    <Zap size={14} className="mt-0.5 text-indigo-400 shrink-0" />
                    <span className="min-w-0">
                      <span className="block text-sm font-medium text-slate-200 truncate">
                        {getAgentDisplayName(type.agent_type)}
                      </span>
                      <span className="block text-xs text-muted-foreground truncate">
                        {type.description || "创建此类型的对话"}
                      </span>
                    </span>
                  </button>
                ))}
                {agentTypes.length === 0 && <div className="px-3 py-3 text-xs text-center text-muted-foreground">暂无可用代理</div>}
              </div>
            )}
          </div>

          {groups.map((project) => {
            const projectCollapsed = collapsedProjects.has(project.name);
            return (
              <section key={project.name} className="border-b border-border/40 pb-2 last:border-b-0">
                <div className="flex items-center gap-1">
                  <button
                    type="button"
                    onClick={() => toggleSetValue(setCollapsedProjects, project.name)}
                    className="flex min-w-0 flex-1 items-center gap-2 px-1 py-2 text-left text-slate-200 hover:text-indigo-300"
                    aria-expanded={!projectCollapsed}
                  >
                    {projectCollapsed ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
                    <Folder size={15} className="text-indigo-400" />
                    <span className="flex-1 min-w-0 truncate text-sm font-medium">{project.name}</span>
                    <span className="text-[11px] text-muted-foreground">{project.conversations.length}</span>
                  </button>
                  <button
                    type="button"
                    onClick={() => openCreateDialog("main", project.name)}
                    aria-label={`在项目“${project.name}”中新增对话`}
                    title="新增对话"
                    className="flex h-7 w-7 shrink-0 items-center justify-center rounded text-muted-foreground hover:bg-indigo-500/10 hover:text-indigo-400"
                  >
                    <Plus size={14} />
                  </button>
                  <button
                    type="button"
                    onClick={() => onDeleteProject(project.name)}
                    aria-label={`删除项目“${project.name}”`}
                    title="删除项目"
                    className="flex h-7 w-7 shrink-0 items-center justify-center rounded text-red-400 hover:bg-red-500/10 hover:text-red-300"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>

                {!projectCollapsed && (
                  <div className="space-y-1.5">
                    {project.conversations.map(({ main, subs }) => {
                      const conversationCollapsed = collapsedConversations.has(main.session_id);
                      return (
                        <div key={main.session_id}>
                          <div className="flex items-start gap-1">
                            <button
                              type="button"
                              onClick={() => toggleSetValue(setCollapsedConversations, main.session_id)}
                              aria-label={conversationCollapsed ? "展开子代理记录" : "折叠子代理记录"}
                              aria-expanded={!conversationCollapsed}
                              className="mt-2 p-1 text-muted-foreground hover:text-foreground"
                            >
                              {subs.length > 0
                                ? (conversationCollapsed ? <ChevronRight size={13} /> : <ChevronDown size={13} />)
                                : <MessageSquare size={13} />}
                            </button>
                            <div className="flex-1 min-w-0">
                              <SessionCard
                                session={main}
                                isViewing={viewingSessionId === main.session_id}
                                isSub={false}
                                canDelete={canDeleteMainSession(main, mainSessionId)}
                                canKill={false}
                                onViewSession={onViewSession}
                                onDeleteSession={onDeleteSession}
                                onKillSession={onKillSession}
                              />
                            </div>
                          </div>
                          {!conversationCollapsed && subs.map((sub) => (
                            <div key={sub.session_id} className="mt-1">
                              <SessionCard
                                session={sub}
                                isViewing={viewingSessionId === sub.session_id}
                                isSub
                                canDelete={sub.status !== "running"}
                                canKill={["running", "waiting", "streaming"].includes(sub.status)}
                                onViewSession={onViewSession}
                                onDeleteSession={onDeleteSession}
                                onKillSession={onKillSession}
                              />
                            </div>
                          ))}
                        </div>
                      );
                    })}
                  </div>
                )}
              </section>
            );
          })}

          {sessions.length === 0 && (
            <div className="text-center text-muted-foreground text-sm py-8">暂无对话</div>
          )}
        </div>
      </ScrollArea>

      {createDialogOpen && (
        <div
          className="fixed inset-0 z-[100] flex items-center justify-center bg-black/55 px-4"
          onMouseDown={(event) => { if (event.target === event.currentTarget) setCreateDialogOpen(false); }}
          role="presentation"
        >
          <div className="w-full max-w-md rounded-lg border border-border/60 bg-slate-800 p-5 shadow-2xl" role="dialog" aria-modal="true" aria-labelledby="create-conversation-title">
            <div className="flex items-center justify-between">
              <h3 id="create-conversation-title" className="text-base font-medium text-slate-100">
                {targetProjectName ? "新增对话" : "新建项目"}
              </h3>
              <button type="button" onClick={() => setCreateDialogOpen(false)} aria-label="关闭" className="p-1 text-muted-foreground hover:text-foreground">
                <X size={17} />
              </button>
            </div>
            <p className="mt-1 text-xs text-muted-foreground">
              {targetProjectName
                ? `新对话将添加到项目“${targetProjectName}”。`
                : "输入项目名称，创建项目及其首个对话。"}
            </p>
            <label htmlFor="project-name" className="mt-4 block text-xs text-slate-300">项目名称</label>
            <input
              ref={projectInputRef}
              id="project-name"
              list="existing-projects"
              value={projectName}
              onChange={(event) => setProjectName(event.target.value)}
              onKeyDown={(event) => { if (event.key === "Enter") createConversation(); }}
              maxLength={80}
              readOnly={Boolean(targetProjectName)}
              placeholder="例如：长篇小说《归途》"
              className="mt-1.5 w-full rounded-md border border-border/60 bg-slate-700 px-3 py-2 text-sm text-foreground outline-none focus:border-indigo-500 read-only:cursor-default read-only:opacity-75"
            />
            <datalist id="existing-projects">
              {projectNames.map((name) => <option key={name} value={name} />)}
            </datalist>
            <div className="mt-3 rounded-md bg-slate-700/50 px-3 py-2 text-xs text-muted-foreground">
              使用代理：<span className="text-slate-200">{getAgentDisplayName(selectedAgentType)}</span>
            </div>
            <div className="mt-5 flex justify-end gap-2">
              <button type="button" onClick={() => setCreateDialogOpen(false)} className="rounded-md px-3 py-2 text-xs text-muted-foreground hover:bg-slate-700">取消</button>
              <button type="button" onClick={createConversation} disabled={!projectName.trim()} className="rounded-md bg-indigo-500 px-4 py-2 text-xs font-medium text-white hover:bg-indigo-400 disabled:cursor-not-allowed disabled:opacity-40">
                {targetProjectName ? "新增对话" : "创建项目"}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
