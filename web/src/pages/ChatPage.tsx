import { useState, useRef, useEffect, useCallback, useMemo } from "react";
import {
  Trash2, X,
  Edit3, Minimize2,
} from "lucide-react";

// Dialog focus trap helper
function useDialogFocus(open: boolean, containerRef: React.RefObject<HTMLDivElement | null>) {
  useEffect(() => {
    if (!open || !containerRef.current) return;
    const el = containerRef.current;
    // Auto-focus first input/textarea
    const firstInput = el.querySelector<HTMLInputElement | HTMLTextAreaElement>("input, textarea");
    firstInput?.focus();
    // Focus trap
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key !== "Tab") return;
      const focusable = el.querySelectorAll<HTMLElement>(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    el.addEventListener("keydown", handleKeyDown);
    return () => el.removeEventListener("keydown", handleKeyDown);
  }, [open, containerRef]);
}
import { BRAND_MARK_DARK, PRODUCT_NAME } from "@/brand";

import { useSessions } from "../hooks/useSessions";
import { useApprovals } from "../hooks/useApprovals";
import { useUrlParam } from "../hooks/useUrlParam";
import { useConversation } from "../features/conversation/useConversation";
import { ConversationComposer, ConversationTimeline } from "../components/conversation";
import ApprovalPanel from "../components/ApprovalPanel";
import ResizableSidePanel from "../components/ResizableSidePanel";
import MonitoringCard from "../components/MonitoringCard";
import ModelSwitcher from "../components/ModelSwitcher";
import { shouldShowModelSwitcher } from "../lib/model-options";
import ChatWorkflowTasks, { upsertWorkflowTask } from "../components/workflow/ChatWorkflowTasks";

import {
  fetchSessionDetail, fetchSessionSystemPrompt, deleteSession, deleteProject, killSession,
  abortSession, compressSession, createNewMainSession,
  fetchPresetPhrases, createPresetPhrase, updatePresetPhrase, deletePresetPhrase,
  listAllTasks,
} from "../lib/api";
import { patchSearchParams } from "../hooks/useUrlParam";
import type {
  Message, NotificationData, SessionDetail, PresetPhrase,
  WorkflowTask, WorkflowTaskUpdateEvent,
} from "../types";

export default function ChatPage() {
  const { sessions, mainSessionId, loadSessions } = useSessions();
  const [viewingSessionId, setViewingSessionId] = useUrlParam("session_id");
  const targetSessionId = viewingSessionId || mainSessionId;
  const workflowTaskSessionId = sessions.find(
    (session) => session.session_id === targetSessionId && session.type === "main",
  )?.session_id || null;
  const [workflowTasks, setWorkflowTasks] = useState<WorkflowTask[]>([]);
  const [workflowTasksLoading, setWorkflowTasksLoading] = useState(false);
  const [notificationMessages, setNotificationMessages] = useState<Message[]>([]);
  const handleExtraConversationEvent = useCallback((rawEvent: unknown) => {
    if (!rawEvent || typeof rawEvent !== "object") return;
    const event = rawEvent as {
      type?: string;
      data?: NotificationData;
      session_id?: string;
    };
    if (
      event.type === "workflow_task_update" &&
      event.session_id === workflowTaskSessionId
    ) {
      setWorkflowTasks((current) => upsertWorkflowTask(
        current,
        event as WorkflowTaskUpdateEvent,
      ));
      return;
    }
    if (targetSessionId !== mainSessionId) return;
    if (event.type !== "notification" || !event.data) return;
    const notification = event.data;
    setNotificationMessages((current) => [
      ...current,
      {
        id: `notification:${notification.from}:${Date.now()}`,
        type: "assistant",
        content:
          `**[子会话通知]** 来自 \`${notification.from}\`` +
          `${notification.task ? ` (${notification.task})` : ""}` +
          `${notification.status ? ` — 状态: ${notification.status}` : ""}` +
          `\n\n${notification.content}`,
      },
    ]);
  }, [mainSessionId, targetSessionId, workflowTaskSessionId]);
  const {
    messages,
    streamingSegments,
    phase,
    isStreaming: isStreamingForCurrentView,
    connected,
    tokenUsage,
    error: conversationError,
    sendMessage,
    sendCommand,
    editMessageAndResend,
    replaceMessages,
    resync,
  } = useConversation({
    sessionId: targetSessionId,
    onExtraEvent: handleExtraConversationEvent,
  });
  const {
    pendingApprovals, resolvedApprovals,
    approve: handleApprove, reject: handleReject, clearResolved,
  } = useApprovals();
  const [sidePanel, setSidePanel] = useState<"sessions" | "prompt" | "workspace">("sessions");

  // 预设短语状态
  const [presetPhrases, setPresetPhrases] = useState<PresetPhrase[]>([]);
  const [editDialogOpen, setEditDialogOpen] = useState(false);
  const dialogRef = useRef<HTMLDivElement>(null);
  useDialogFocus(editDialogOpen, dialogRef);

  const [editLabel, setEditLabel] = useState("");
  const [editContent, setEditContent] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [compressing, setCompressing] = useState(false);

  // 自定义确认对话框状态
  const [confirmDialog, setConfirmDialog] = useState<{
    open: boolean;
    title: string;
    message: string;
    onConfirm: () => void;
  }>({ open: false, title: "", message: "", onConfirm: () => {} });
  const confirmBtnRef = useRef<HTMLButtonElement>(null);

  // 确认对话框焦点管理
  useEffect(() => {
    if (confirmDialog.open && confirmBtnRef.current) {
      setTimeout(() => confirmBtnRef.current?.focus(), 50);
    }
  }, [confirmDialog.open]);

  const [actionError, setActionError] = useState<string | null>(null);

  // 监控卡片折叠状态（默认折叠）
  const [monitoringCollapsed, setMonitoringCollapsed] = useState(true);

  // 实时 LLM 上下文状态
  const [llmContext, setLlmContext] = useState<Awaited<ReturnType<typeof fetchSessionSystemPrompt>> | null>(null);
  const [promptLoading, setPromptLoading] = useState(false);

  // 会话详情仅用于判断交互能力；消息历史由 canonical WS snapshot 管理。
  const [viewingSession, setViewingSession] = useState<SessionDetail | null>(null);
  const [loadingSession, setLoadingSession] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [historyReloadToken, setHistoryReloadToken] = useState(0);
  const detailRequestRef = useRef(0);

  useEffect(() => {
    setNotificationMessages([]);
  }, [targetSessionId]);

  useEffect(() => {
    let cancelled = false;
    setWorkflowTasks([]);
    if (!workflowTaskSessionId) {
      setWorkflowTasksLoading(false);
      return;
    }
    setWorkflowTasksLoading(true);
    listAllTasks({
      main_session_id: workflowTaskSessionId,
      page_size: 20,
      sort_by: "updated_at",
      sort_order: "desc",
    })
      .then((result) => {
        if (!cancelled) {
          setWorkflowTasks((current) => result.tasks.reduce(
            (tasks, task) => upsertWorkflowTask(tasks, task),
            current,
          ));
        }
      })
      .catch(() => {
        if (!cancelled) setWorkflowTasks([]);
      })
      .finally(() => {
        if (!cancelled) setWorkflowTasksLoading(false);
      });
    return () => { cancelled = true; };
  }, [connected, workflowTaskSessionId]);

  const handleOpenWorkflowTask = useCallback((task: WorkflowTask) => {
    const search = patchSearchParams(window.location.search, {
      tab: "workflow",
      workflow_id: task.workflow_id,
      task_id: task.task_id,
      node_id: null,
    });
    window.history.pushState(
      window.history.state,
      "",
      `${window.location.pathname}${search}${window.location.hash}`,
    );
    window.dispatchEvent(new PopStateEvent("popstate"));
  }, []);

  // REST 只作为握手前的历史兜底；replaceMessages 会拒绝覆盖已到达的权威 snapshot。
  useEffect(() => {
    const requestId = ++detailRequestRef.current;
    setViewingSession(null);
    setHistoryError(null);
    if (!targetSessionId) {
      setLoadingSession(false);
      return;
    }

    setLoadingSession(true);
    fetchSessionDetail(targetSessionId)
      .then((detail) => {
        if (requestId !== detailRequestRef.current) return;
        setViewingSession(detail);
        replaceMessages(detail.messages || [], {
          status: detail.status,
          error: detail.last_error?.message,
        });
      })
      .catch((error: unknown) => {
        if (requestId !== detailRequestRef.current) return;
        setHistoryError(error instanceof Error ? error.message : "加载会话详情失败");
      })
      .finally(() => {
        if (requestId === detailRequestRef.current) setLoadingSession(false);
      });

    return () => {
      if (requestId === detailRequestRef.current) detailRequestRef.current += 1;
    };
  }, [historyReloadToken, replaceMessages, targetSessionId, viewingSessionId]);

  const displayMessages = useMemo(
    () => [...messages, ...notificationMessages],
    [messages, notificationMessages],
  );

  // 实时获取当前查看会话的完整 LLM 上下文
  const loadSystemPrompt = useCallback(async () => {
    const targetId = viewingSessionId || mainSessionId;
    if (!targetId) return;
    setPromptLoading(true);
    setActionError(null);
    try {
      const data = await fetchSessionSystemPrompt(targetId);
      setLlmContext(data);
    } catch {
      setActionError("获取 LLM 上下文失败，请重试");
    } finally {
      setPromptLoading(false);
    }
  }, [viewingSessionId, mainSessionId]);

  // 切换到提示词面板时自动加载，会话切换时也自动刷新
  useEffect(() => {
    if (sidePanel === "prompt") {
      loadSystemPrompt();
    }
  }, [sidePanel, viewingSessionId, mainSessionId, loadSystemPrompt]);

  // 每次 LLM 对话结束后，如果正在看提示词面板，自动刷新
  useEffect(() => {
    if (sidePanel === "prompt" && !isStreamingForCurrentView) {
      loadSystemPrompt();
    }
  }, [displayMessages.length, isStreamingForCurrentView, sidePanel, loadSystemPrompt]);

  // 判断会话是否可交互（后端有已编译 graph 且状态非 error/idle）
  const isSessionInteractive = useCallback((session: SessionDetail | null): boolean => {
    if (!session) return false;
    if (session.has_graph === false) return false;
    return session.status !== "error" && session.status !== "idle";
  }, []);

  const isViewingOther = viewingSessionId !== null;
  const isReadOnly = isViewingOther && !isSessionInteractive(viewingSession);
  const showModelSwitcher = shouldShowModelSwitcher(viewingSession);
  const hasConfiguredModel = Boolean(viewingSession?.model_id);
  const canSend = Boolean(
    targetSessionId &&
    connected &&
    phase === "ready" &&
    !isReadOnly &&
    hasConfiguredModel
  );
  const timelineError = conversationError ||
    ((!connected || phase === "loading") ? historyError : null);
  const handleRetryHistory = useCallback(() => {
    setHistoryReloadToken((value) => value + 1);
    resync();
  }, [resync]);

  // 切换到查看某个会话
  const handleViewSession = useCallback((sessionId: string) => {
    setViewingSessionId(viewingSessionId === sessionId ? null : sessionId);
  }, [setViewingSessionId, viewingSessionId]);

  // 删除会话
  const handleDeleteSession = useCallback(async (sessionId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    const session = sessions.find((item) => item.session_id === sessionId);
    const isMainSession = session?.type === "main";
    setConfirmDialog({
      open: true,
      title: isMainSession ? "删除对话" : "删除会话",
      message: isMainSession
        ? "确定删除此对话吗？只会删除当前对话及其子代理记录，不会删除同一项目中的其他对话。"
        : `确定要删除会话 ${sessionId} 吗？此操作不可恢复。`,
      onConfirm: async () => {
        try {
          const result = await deleteSession(sessionId);
          if (result.deleted_session_ids?.includes(targetSessionId || "")) {
            setViewingSessionId(null);
            setViewingSession(null);
          }
          loadSessions();
        } catch {
          setActionError("删除会话失败，请重试");
        }
      },
    });
  }, [sessions, targetSessionId, setViewingSessionId, loadSessions]);

  const handleDeleteProject = useCallback((projectName: string) => {
    setConfirmDialog({
      open: true,
      title: "删除项目",
      message: `确定删除项目“${projectName}”吗？项目内所有对话、子代理记录和关联工作流任务都会被永久删除。`,
      onConfirm: async () => {
        try {
          const result = await deleteProject(projectName);
          if (result.deleted_session_ids?.includes(targetSessionId || "")) {
            setViewingSessionId(null);
            setViewingSession(null);
          }
          await loadSessions();
        } catch {
          setActionError("删除项目失败，请重试");
        }
      },
    });
  }, [targetSessionId, setViewingSessionId, loadSessions]);

  // 终止会话
  const handleKillSession = useCallback(async (sessionId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setConfirmDialog({
      open: true,
      title: "终止会话",
      message: `确定要终止会话 ${sessionId} 吗？`,
      onConfirm: async () => {
        try {
          await killSession(sessionId);
          loadSessions();
        } catch {
          setActionError("终止会话失败，请重试");
        }
      },
    });
  }, [loadSessions]);

  // 加载预设短语
  useEffect(() => {
    fetchPresetPhrases().then(setPresetPhrases).catch(() => {});
  }, []);

  // 中止当前查看会话的流式输出
  const handleStop = useCallback(async () => {
    if (!targetSessionId) return;
    try {
      await abortSession(targetSessionId);
    } finally {
      resync();
    }
  }, [resync, targetSessionId]);

  // 手动触发上下文压缩
  const handleCompress = useCallback(async () => {
    const targetId = viewingSessionId || mainSessionId;
    if (!targetId || compressing) return;
    setCompressing(true);
    setActionError(null);
    try {
      await compressSession(targetId);
    } catch {
      setActionError("压缩失败，请重试");
    } finally {
      setCompressing(false);
    }
  }, [viewingSessionId, mainSessionId, compressing]);

  // 点击预设短语
  const handlePresetSend = useCallback((content: string) => {
    if (!canSend) return;
    sendMessage(content);
  }, [canSend, sendMessage]);

  // 编辑对话框 - 打开新增
  const openAddDialog = useCallback(() => {
    setEditingId(null);
    setEditLabel("");
    setEditContent("");
    setEditDialogOpen(true);
  }, []);

  // 编辑对话框 - 打开编辑
  const openEditDialog = useCallback((phrase: PresetPhrase) => {
    setEditingId(phrase.id);
    setEditLabel(phrase.label);
    setEditContent(phrase.content);
    setEditDialogOpen(true);
  }, []);

  // 保存预设短语
  const handleSavePresetPhrase = useCallback(async () => {
    if (!editLabel.trim() || !editContent.trim()) return;
    setActionError(null);
    try {
      if (editingId) {
        const updated = await updatePresetPhrase(editingId, { label: editLabel.trim(), content: editContent.trim() });
        setPresetPhrases((prev) => prev.map((p) => (p.id === editingId ? updated : p)));
      } else {
        const created = await createPresetPhrase({ label: editLabel.trim(), content: editContent.trim() });
        setPresetPhrases((prev) => [...prev, created]);
      }
      setEditDialogOpen(false);
    } catch {
      setActionError("保存预设短语失败，请重试");
    }
  }, [editLabel, editContent, editingId]);

  // 新建主会话（支持指定 agent_type）
  const handleCreateSession = useCallback(async (agentType?: string, projectName?: string) => {
    setActionError(null);
    try {
      const result = await createNewMainSession(agentType, projectName);
      await loadSessions();
      // 自动选中新建的会话
      if (result.session_id) {
        setViewingSessionId(result.session_id);
      }
    } catch (e) {
      setActionError("新建会话失败：" + (e as Error).message);
    }
  }, [loadSessions, setViewingSessionId]);

  // 删除预设短语
  const handleDeletePresetPhrase = useCallback(async (phraseId: string) => {
    setActionError(null);
    try {
      await deletePresetPhrase(phraseId);
      setPresetPhrases((prev) => prev.filter((p) => p.id !== phraseId));
    } catch {
      setActionError("删除预设短语失败，请重试");
    }
  }, []);

  const handleEditDisplayedMessage = useCallback((msgId: string, newContent: string) => {
    editMessageAndResend(msgId, newContent);
  }, [editMessageAndResend]);

  // 计算可编辑消息范围：最后一条 compression_divider 之后的 user 消息可编辑
  const editableMap = useMemo(() => {
    const map = new Set<string>();
    // 找到最后一条 compression_divider 的索引
    let lastDividerIdx = -1;
    for (let i = displayMessages.length - 1; i >= 0; i--) {
      if (displayMessages[i].type === "compression_divider") {
        lastDividerIdx = i;
        break;
      }
    }
    // 标记 divider 之后的 user 消息为可编辑
    for (let i = lastDividerIdx + 1; i < displayMessages.length; i++) {
      const msg = displayMessages[i];
      if (msg.type === "user" && msg.id) {
        map.add(msg.id);
      }
    }
    return map;
  }, [displayMessages]);

  // 会话列表按 updated_at 降序排序（最近活跃的在前）
  const sortedSessions = useMemo(
    () => [...sessions].sort((a, b) => b.updated_at.localeCompare(a.updated_at)),
    [sessions],
  );
  const handleMonitoringToggle = useCallback(() => {
    setMonitoringCollapsed((value) => !value);
  }, []);

  return (
    <div className="h-[calc(100dvh-3.5rem)] flex">
      {/* Token 监控竖向边栏 - 左侧，可折叠 */}
      <div
        className={`shrink-0 flex flex-col transition-all duration-300 ${monitoringCollapsed ? "w-7" : "w-64"}`}
        role="complementary"
        aria-label="Token 监控面板"
      >
        <div className={`h-full ${monitoringCollapsed ? "px-1" : "px-2"} py-3 overflow-y-auto`}>
          <MonitoringCard
            tokenUsage={tokenUsage}
            collapsed={monitoringCollapsed}
            onToggle={handleMonitoringToggle}
          />
        </div>
      </div>

      {/* Main Chat Area */}
      <div className="flex-1 flex flex-col min-w-0 min-h-0" role="main" aria-label="聊天区域">
        {/* 审批通知面板 */}
        <ApprovalPanel
          pendingApprovals={pendingApprovals}
          resolvedApprovals={resolvedApprovals}
          onApprove={handleApprove}
          onReject={handleReject}
          onClearResolved={clearResolved}
        />

        <ChatWorkflowTasks
          tasks={workflowTasks}
          loading={workflowTasksLoading}
          onOpenTask={handleOpenWorkflowTask}
        />

        <ConversationTimeline
          messages={displayMessages}
          streamingSegments={streamingSegments}
          isStreaming={isStreamingForCurrentView}
          loading={phase === "loading" || (loadingSession && displayMessages.length === 0)}
          error={timelineError}
          onRetry={handleRetryHistory}
          conversationId={targetSessionId}
          readonly={isReadOnly}
          onEditMessage={handleEditDisplayedMessage}
          onCommand={isReadOnly ? undefined : sendCommand}
          isMessageEditable={(message) =>
            message.type === "user" && !!message.id && editableMap.has(message.id)
          }
          ariaLabel="聊天消息"
          contentClassName="w-full max-w-4xl mx-auto px-6 py-4"
          emptyState={(
            <div className="flex flex-col items-center justify-center h-64 text-center" role="status" aria-label="暂无消息">
              <div className="mb-4 h-16 w-16 animate-float motion-reduce:animate-none">
                <img src={BRAND_MARK_DARK} alt="" className="h-full w-full" aria-hidden="true" />
              </div>
              <h2 className="text-xl font-semibold text-slate-200 mb-2">
                {isViewingOther ? "此会话暂无消息" : PRODUCT_NAME}
              </h2>
              <p className="text-muted-foreground text-sm">
                {isViewingOther
                  ? "可以在下方输入框向此会话发送消息"
                  : "输入消息开始对话，或切换到 Workflow 构建可恢复的 AI 流程"}
              </p>
            </div>
          )}
        />

        {/* Input Area */}
        <div className="px-6 pb-4">
          <div className="w-full max-w-4xl mx-auto space-y-2">
            {/* 预设短语栏 */}
            <div className="group flex items-center gap-1.5 flex-wrap min-h-[28px]" role="toolbar" aria-label="预设短语">
              <div className="flex-1 flex items-center gap-1.5 flex-wrap">
                {presetPhrases.length === 0 ? (
                  <span className="text-xs text-muted-foreground/40 italic">暂无预设短语，点击右侧编辑按钮添加</span>
                ) : (
                  presetPhrases.map((phrase) => (
                    <button
                      type="button"
                      key={phrase.id}
                      onClick={() => handlePresetSend(phrase.content)}
                      disabled={!canSend}
                      aria-label={`发送预设短语: ${phrase.label}`}
                      className="px-2.5 py-1 text-xs rounded-full bg-slate-700/60 text-slate-300 hover:bg-indigo-500/20 hover:text-indigo-400 border border-border/40 transition-colors duration-200 disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer whitespace-nowrap"
                    >
                      {phrase.label}
                    </button>
                  ))
                )}
              </div>
              {/* 编辑按钮 - hover 时显示 */}
              <button
                type="button"
                onClick={openAddDialog}
                title="编辑预设短语"
                aria-label="编辑预设短语"
                className="p-1.5 rounded-md text-muted-foreground hover:text-indigo-400 hover:bg-indigo-500/10 opacity-0 group-hover:opacity-100 transition-all cursor-pointer flex-shrink-0 min-h-[44px] min-w-[44px] flex items-center justify-center"
              >
                <Edit3 size={14} aria-hidden="true" />
              </button>
            </div>

            {/* 快捷按钮栏 */}
            <div className="flex items-center gap-2" role="toolbar" aria-label="快捷操作">
              <button
                type="button"
                onClick={handleCompress}
                disabled={isReadOnly || compressing}
                title="手动触发上下文压缩"
                aria-label="手动触发上下文压缩"
                className={`flex items-center gap-1 px-2.5 py-1 text-xs rounded-md transition-colors duration-200 cursor-pointer ${
                  compressing
                    ? "bg-slate-700 text-muted-foreground cursor-not-allowed"
                    : "bg-slate-700/60 text-slate-400 hover:bg-purple-500/20 hover:text-purple-400 border border-border/40"
                }`}
              >
                <Minimize2 size={12} aria-hidden="true" />
                {compressing ? "压缩中..." : "压缩上下文"}
              </button>
            </div>

            <ConversationComposer
              sessionId={targetSessionId}
              onSendMessage={sendMessage}
              onAbort={handleStop}
              isStreaming={isStreamingForCurrentView}
              editable={canSend || isStreamingForCurrentView}
              sendEnabled={canSend}
              placeholder={
                isReadOnly
                  ? "该会话已结束，无法发送消息"
                  : !hasConfiguredModel
                    ? "请先在模型设置中添加供应商和模型"
                  : isViewingOther
                    ? `向会话 ${viewingSessionId} 发消息... (Shift+Enter 换行)`
                    : "输入消息... (Shift+Enter 换行)"
              }
              trailingControls={showModelSwitcher ? (
                  <ModelSwitcher
                    sessionId={targetSessionId}
                    session={viewingSession}
                    disabled={isReadOnly || isStreamingForCurrentView}
                    onUpdated={(modelId, modelParams) => {
                      setViewingSession((current) => current ? {
                        ...current,
                        model_id: modelId,
                        model_params: modelParams,
                      } : current);
                    }}
                    onOpenSettings={() => {
                      const search = patchSearchParams(window.location.search, {
                        tab: "settings",
                        session_id: null,
                      });
                      window.history.pushState(
                        window.history.state,
                        "",
                        `${window.location.pathname}${search}${window.location.hash}`,
                      );
                      window.dispatchEvent(new PopStateEvent("popstate"));
                    }}
                  />
              ) : null}
            />
            {!connected && targetSessionId && phase !== "loading" && (
              <div className="text-center text-red-400 text-xs mt-2" role="alert" aria-live="polite">WebSocket 未连接，请检查后端服务</div>
            )}
          </div>
        </div>

        {/* 预设短语编辑弹窗 */}
        {editDialogOpen && (
          <div
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
            onClick={() => setEditDialogOpen(false)}
            onKeyDown={(e) => { if (e.key === "Escape") setEditDialogOpen(false); }}
            role="presentation"
          >
            <div
              ref={dialogRef}
              className="bg-slate-800 border border-border/60 rounded-xl p-4 sm:p-5 w-[460px] max-w-[calc(100vw-2rem)] max-h-[80vh] overflow-y-auto shadow-2xl"
              onClick={(e) => e.stopPropagation()}
              role="dialog"
              aria-modal="true"
              aria-label={editingId ? "编辑预设短语" : "新增预设短语"}
            >
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-sm font-medium text-slate-200">
                  {editingId ? "编辑预设短语" : "新增预设短语"}
                </h3>
                <button
                  type="button"
                  onClick={() => setEditDialogOpen(false)}
                  aria-label="关闭对话框"
                  className="p-1 rounded-md text-muted-foreground hover:text-foreground hover:bg-slate-700 cursor-pointer"
                >
                  <X size={16} aria-hidden="true" />
                </button>
              </div>

              {/* 已有预设短语列表 */}
              {presetPhrases.length > 0 && (
                <div className="space-y-1.5 mb-4 max-h-48 overflow-y-auto">
                  {presetPhrases.map((phrase) => (
                    <div
                      key={phrase.id}
                      className={`flex items-center justify-between px-3 py-2 rounded-lg text-xs ${
                        editingId === phrase.id
                      ? "bg-indigo-500/15 border border-indigo-500/30"
                      : "bg-slate-700/50 hover:bg-slate-700 border border-transparent"
                      }`}
                    >
                      <div className="flex-1 min-w-0 mr-2">
                        <div className="text-slate-200 font-medium truncate">{phrase.label}</div>
                        <div className="text-muted-foreground truncate">{phrase.content}</div>
                      </div>
                      <div className="flex items-center gap-1 flex-shrink-0">
                        <button
                          type="button"
                          onClick={() => openEditDialog(phrase)}
                          className="p-1 rounded text-muted-foreground hover:text-cyan-400 hover:bg-slate-600 cursor-pointer"
                          title="编辑"
                          aria-label={`编辑短语: ${phrase.label}`}
                        >
                          <Edit3 size={12} aria-hidden="true" />
                        </button>
                        <button
                          type="button"
                          onClick={() => handleDeletePresetPhrase(phrase.id)}
                          className="p-1 rounded text-muted-foreground hover:text-red-400 hover:bg-slate-600 cursor-pointer"
                          title="删除"
                          aria-label={`删除短语: ${phrase.label}`}
                        >
                          <Trash2 size={12} aria-hidden="true" />
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {/* 新增/编辑表单 */}
              <div className="space-y-3">
                <div>
                  <label htmlFor="preset-label" className="text-xs text-muted-foreground block mb-1">显示名</label>
                  <input
                    id="preset-label"
                    value={editLabel}
                    onChange={(e) => setEditLabel(e.target.value)}
                    placeholder="例如：自我介绍"
                    className="w-full px-3 py-2 text-sm bg-slate-700 border border-border/60 rounded-lg text-foreground placeholder:text-muted-foreground outline-none focus:border-indigo-500/60 transition-colors"
                  />
                </div>
                <div>
                  <label htmlFor="preset-content" className="text-xs text-muted-foreground block mb-1">实际输入内容</label>
                  <textarea
                    id="preset-content"
                    value={editContent}
                    onChange={(e) => setEditContent(e.target.value)}
                    placeholder="输入发送给 LLM 的实际文本..."
                    rows={3}
                    className="w-full px-3 py-2 text-sm bg-slate-700 border border-border/60 rounded-lg text-foreground placeholder:text-muted-foreground outline-none focus:border-indigo-500/60 transition-colors resize-none"
                  />
                </div>
                <div className="flex items-center justify-end gap-2 pt-1">
                  {editingId && (
                    <button
                      type="button"
                      onClick={() => {
                        setEditingId(null);
                        setEditLabel("");
                        setEditContent("");
                      }}
                      className="px-3 py-1.5 text-xs rounded-lg bg-slate-700 text-muted-foreground hover:text-foreground hover:bg-slate-600 transition-colors cursor-pointer min-h-[44px]"
                    >
                      取消编辑
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => setEditDialogOpen(false)}
                    className="px-3 py-1.5 text-xs rounded-lg bg-slate-700 text-muted-foreground hover:text-foreground hover:bg-slate-600 transition-colors cursor-pointer min-h-[44px]"
                  >
                    取消
                  </button>
                  <button
                    type="button"
                    onClick={handleSavePresetPhrase}
                    disabled={!editLabel.trim() || !editContent.trim()}
                    className="px-4 py-1.5 text-xs rounded-lg bg-indigo-500 text-white hover:bg-indigo-400 transition-colors disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer min-h-[44px]"
                  >
                    {editingId ? "保存" : "添加"}
                  </button>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Right Side Panel - Resizable */}
      <ResizableSidePanel
        sidePanel={sidePanel}
        setSidePanel={setSidePanel}
        sortedSessions={sortedSessions}
        viewingSessionId={viewingSessionId}
        mainSessionId={mainSessionId}
        onViewSession={handleViewSession}
        onDeleteSession={handleDeleteSession}
        onKillSession={handleKillSession}
        onCreateSession={handleCreateSession}
        onDeleteProject={handleDeleteProject}
        llmContext={llmContext}
        promptLoading={promptLoading}
        onRefreshPrompt={loadSystemPrompt}
        viewingSession={viewingSession}
        sessions={sessions}
      />

      {/* 自定义确认对话框 */}
      {confirmDialog.open && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
          onClick={() => setConfirmDialog((prev) => ({ ...prev, open: false }))}
          onKeyDown={(e) => { if (e.key === "Escape") setConfirmDialog((prev) => ({ ...prev, open: false })); }}
          role="presentation"
        >
          <div
            className="bg-slate-800 border border-border/60 rounded-xl p-5 w-[400px] max-w-[calc(100vw-2rem)] shadow-2xl"
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-modal="true"
            aria-label={confirmDialog.title}
          >
            <h3 className="text-sm font-medium text-slate-200 mb-2">{confirmDialog.title}</h3>
            <p className="text-xs text-muted-foreground mb-5">{confirmDialog.message}</p>
            <div className="flex items-center justify-end gap-2">
              <button
                type="button"
                onClick={() => setConfirmDialog((prev) => ({ ...prev, open: false }))}
                className="px-3 py-1.5 text-xs rounded-lg bg-slate-700 text-muted-foreground hover:text-foreground hover:bg-slate-600 transition-colors duration-200 cursor-pointer min-h-[44px]"
              >
                取消
              </button>
              <button
                ref={confirmBtnRef}
                type="button"
                onClick={() => {
                  confirmDialog.onConfirm();
                  setConfirmDialog((prev) => ({ ...prev, open: false }));
                }}
                className="px-4 py-1.5 text-xs rounded-lg bg-red-500 text-white hover:bg-red-400 transition-colors duration-200 cursor-pointer min-h-[44px]"
              >
                确认
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 操作错误提示 */}
      {actionError && (
        <div
          className="fixed bottom-4 right-4 z-50 max-w-sm bg-red-500/10 border border-red-500/30 rounded-lg px-4 py-3 flex items-center gap-3 shadow-lg"
          role="alert"
          aria-live="polite"
        >
          <span className="text-xs text-red-300 flex-1">{actionError}</span>
          <button
            type="button"
            onClick={() => setActionError(null)}
            className="text-red-400 hover:text-red-300 cursor-pointer min-h-[44px] min-w-[44px] flex items-center justify-center"
            aria-label="关闭错误提示"
          >
            <X size={14} aria-hidden="true" />
          </button>
        </div>
      )}
    </div>
  );
}
