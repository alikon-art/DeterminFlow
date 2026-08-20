import { useState, useEffect, useRef } from "react";
import { Users, Play, Square, Plus, X, Zap, Brain, FileText, Pause, SkipForward, Send, UserPlus, CircleCheck, AlertTriangle, Search, Rocket, BarChart3, Loader2, RefreshCw, ArrowLeft } from "lucide-react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useRoundtable } from "../hooks/useRoundtable";
import TranscriptMessage from "../components/TranscriptMessage";
import { getSeatColor } from "../lib/seatColors";
import SeatCard from "../components/SeatCard";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  EmptyState,
  DeleteRoundtableDialog,
  InfoRow,
  SessionStatusBadge,
  StrategyBadge,
} from "../components/roundtable/RoundtablePageParts";
import {
  ROUNDTABLE_TEMPLATES,
  type RoundtableTemplate,
  type SeatFormItem,
} from "../components/roundtable/roundtableTemplates";
import type { RoundtableSummary } from "../types";

export default function RoundtablePage() {
  const {
    roundtables,
    activeSession,
    loadDetail,
    clearActive,
    detailLoading,
    detailError,
    retryDetail,
    connected,
    transcript,
    seats,
    streamingSeat,
    currentRound,
    isDiscussing,
    handleCreate,
    handleStart,
    handleStop,
    handleDelete,
    refreshList,
    moderatorDecision,
    thinkingSeatId,
    roundSummaries,
    conclusion,
    strategy,
    structuredConclusion,
    isPaused,
    handlePause,
    handleResume,
    handleInject,
    handleNominate,
    handleAddSeat,
    handleRemoveSeat,
    pendingAction,
  } = useRoundtable();
  const [showCreate, setShowCreate] = useState(false);
  const [topic, setTopic] = useState("");
  const [maxRounds, setMaxRounds] = useState(3);
  const [selectedStrategy, setSelectedStrategy] = useState("round_robin");
  const [compressorEnabled, setCompressorEnabled] = useState(false);
  const [compressorWindow, setCompressorWindow] = useState(20);
  const [compressorInterval, setCompressorInterval] = useState(3);
  const [seatForms, setSeatForms] = useState<SeatFormItem[]>([
    { role_name: "", system_prompt: "", temperature: 0.7, is_moderator: false },
    { role_name: "", system_prompt: "", temperature: 0.7, is_moderator: false },
  ]);
  const [injectContent, setInjectContent] = useState("");
  const [showAddSeat, setShowAddSeat] = useState(false);
  const [newSeatName, setNewSeatName] = useState("");
  const [newSeatPrompt, setNewSeatPrompt] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<RoundtableSummary | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const creating = pendingAction === "create";
  const actionPending = pendingAction !== null;
  const interventionPending = pendingAction === "inject" || pendingAction === "nominate";

  useEffect(() => {
    if (scrollRef.current) {
      const prefersReduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      scrollRef.current.scrollIntoView({ behavior: prefersReduced ? "auto" : "smooth" });
    }
  }, [transcript, streamingSeat]);

  // Escape 关闭创建表单
  useEffect(() => {
    if (!showCreate) return;
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === "Escape") setShowCreate(false);
    };
    document.addEventListener("keydown", handleEscape);
    return () => document.removeEventListener("keydown", handleEscape);
  }, [showCreate]);

  // 构建 seatId → index 映射
  const seatIndexMap: Record<string, number> = {};
  seats.forEach((s, i) => {
    seatIndexMap[s.seat_id] = i;
  });

  // 应用模板
  const applyTemplate = (tpl: RoundtableTemplate) => {
    setTopic(tpl.topic);
    setSelectedStrategy(tpl.strategy);
    setSeatForms(
      tpl.seats.map((s) => ({
        role_name: s.role_name,
        system_prompt: s.system_prompt,
        temperature: s.temperature,
        is_moderator: s.is_moderator || false,
      }))
    );
  };

  // 添加席位
  const addSeat = () => {
    if (seatForms.length >= 6) return;
    setSeatForms((prev) => [
      ...prev,
      { role_name: "", system_prompt: "", temperature: 0.7, is_moderator: false },
    ]);
  };

  // 删除席位
  const removeSeat = (index: number) => {
    if (seatForms.length <= 2) return;
    setSeatForms((prev) => prev.filter((_, i) => i !== index));
  };

  // 更新席位
  const updateSeat = (index: number, field: keyof SeatFormItem, value: string | number | boolean) => {
    setSeatForms((prev) =>
      prev.map((s, i) => (i === index ? { ...s, [field]: value } : s))
    );
  };

  // 提交创建
  const handleSubmitCreate = async () => {
    if (!topic.trim()) return;
    if (seatForms.some((s) => !s.role_name.trim())) return;

    const result = await handleCreate({
      topic: topic.trim(),
      seats: seatForms.map((s) => ({
        role_name: s.role_name,
        system_prompt: s.system_prompt || `你是${s.role_name}。`,
        temperature: s.temperature,
        is_moderator: s.is_moderator,
      })),
      max_rounds: maxRounds,
      strategy: selectedStrategy,
      compressor: compressorEnabled ? {
        enabled: true,
        window_size: compressorWindow,
        summary_interval: compressorInterval,
      } : null,
    });

    if (result?.success) {
      setShowCreate(false);
      setTopic("");
      setSeatForms([
        { role_name: "", system_prompt: "", temperature: 0.7, is_moderator: false },
        { role_name: "", system_prompt: "", temperature: 0.7, is_moderator: false },
      ]);
      setSelectedStrategy("round_robin");
      setCompressorEnabled(false);
    }
  };

  const submitIntervention = async () => {
    const content = injectContent.trim();
    if (!activeSession || !content || interventionPending) return;

    const atMatch = content.match(/^@(\S+)\s*(.*)/);
    const result = atMatch
      ? await handleNominate(activeSession.session_id, undefined, atMatch[1], atMatch[2])
      : await handleInject(activeSession.session_id, content);
    if (result?.success) setInjectContent("");
  };

  const confirmDelete = async () => {
    if (!deleteTarget || pendingAction === "delete") return;
    const result = await handleDelete(deleteTarget.session_id);
    if (result?.success) setDeleteTarget(null);
  };

  // 判断 transcript 中轮次是否变化
  const isNewRound = (index: number) => {
    if (index === 0) return true;
    if (index < 0 || index >= transcript.length) return false;
    return transcript[index].round_number !== transcript[index - 1].round_number;
  };

  // 判断流式发言是否属于新轮次（与 transcript 最后一条记录比较）
  const isStreamingNewRound = () => {
    if (!streamingSeat) return false;
    if (transcript.length === 0) return true;
    return streamingSeat.round !== transcript[transcript.length - 1].round_number;
  };

  // 获取思考中的席位名称
  const thinkingSeatName = thinkingSeatId
    ? seats.find((s) => s.seat_id === thinkingSeatId)?.role_name || "主持人"
    : null;

  return (
    <div className="h-[calc(100dvh-3.5rem)] flex">
      {/* ========== 左侧主区域 ========== */}
      <div className="flex-1 flex flex-col min-w-0" role="main" aria-label="圆桌会议主区域">
        {/* 顶部状态栏 */}
        {activeSession && (
          <div className="px-6 py-3 border-b border-slate-700/50 bg-slate-800/80 flex items-center gap-4" role="banner" aria-label="会议状态栏">
            <button
              type="button"
              onClick={clearActive}
              aria-label="返回历史列表"
              title="返回历史列表"
              className="min-h-[44px] min-w-[44px] -ml-2 flex items-center justify-center rounded-lg text-slate-400 hover:text-slate-200 hover:bg-slate-700/50 transition-colors cursor-pointer focus-visible:ring-2 focus-visible:ring-indigo-500/30"
            >
              <ArrowLeft size={18} aria-hidden="true" />
            </button>
            <Users size={18} className="text-cyan-500" aria-hidden="true" />
            <span className="text-sm font-medium text-slate-200 truncate flex-1">
              {activeSession.topic}
            </span>
            <StrategyBadge strategy={strategy} />
            <SessionStatusBadge status={activeSession.status} />
            <span className="text-xs text-slate-400">
              轮次 {currentRound}/{activeSession.max_rounds}
            </span>
          </div>
        )}

        {/* Moderator 决策横幅 */}
        {moderatorDecision && isDiscussing && (
          <div className="px-6 py-2 bg-amber-500/10 border-b border-amber-500/20 flex items-center gap-3" role="status" aria-live="polite" aria-label="主持人决策">
            <Brain size={16} className="text-amber-400" aria-hidden="true" />
            <span className="text-xs text-amber-300">
              {moderatorDecision.action === "select_speaker" && `主持人选择了下一位发言者`}
              {moderatorDecision.action === "new_round" && "主持人决定开始新一轮讨论"}
              {moderatorDecision.action === "summarize" && "主持人正在生成阶段摘要..."}
              {moderatorDecision.action === "conclude" && "主持人决定结束讨论"}
            </span>
            {moderatorDecision.reason && (
              <span className="text-xs text-amber-400/60 truncate flex-1">
                ({moderatorDecision.reason})
              </span>
            )}
          </div>
        )}

        {/* 思考中状态 */}
        {thinkingSeatId && isDiscussing && (
          <div className="px-6 py-2 bg-amber-500/5 border-b border-amber-500/10 flex items-center gap-3" role="status" aria-live="polite" aria-label={`${thinkingSeatName} 正在思考决策`}>
            <div className="flex items-center gap-1.5" aria-hidden="true">
              <span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse motion-reduce:animate-none" />
              <span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse motion-reduce:animate-none" style={{ animationDelay: "0.3s" }} />
              <span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse motion-reduce:animate-none" style={{ animationDelay: "0.6s" }} />
            </div>
            <span className="text-xs text-amber-300">
              {thinkingSeatName} 正在思考决策...
            </span>
          </div>
        )}

        {/* 讨论区 */}
        <ScrollArea className="flex-1 px-6 py-4">
          {detailLoading && !activeSession && !showCreate && (
            <div className="flex min-h-64 flex-col items-center justify-center gap-3 text-slate-500" role="status" aria-live="polite">
              <Loader2 size={24} className="animate-spin text-indigo-400 motion-reduce:animate-none" aria-hidden="true" />
              <p className="text-sm">正在加载圆桌详情</p>
            </div>
          )}

          {detailError && !activeSession && !showCreate && (
            <div className="flex min-h-64 flex-col items-center justify-center gap-3 text-center" role="alert">
              <AlertTriangle size={24} className="text-red-400" aria-hidden="true" />
              <p className="text-sm text-red-300">{detailError}</p>
              <button
                type="button"
                onClick={retryDetail}
                className="inline-flex min-h-10 items-center gap-2 rounded-lg border border-red-500/25 bg-red-500/10 px-3 text-sm text-red-300 transition-colors hover:bg-red-500/15 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500/50"
              >
                <RefreshCw size={14} aria-hidden="true" />
                重试
              </button>
            </div>
          )}

          {/* 无活跃会话 - 空状态或创建表单 */}
          {!activeSession && !showCreate && !detailLoading && !detailError && (
            <EmptyState
              onShowCreate={() => setShowCreate(true)}
              roundtables={roundtables}
              onSelect={(id) => loadDetail(id)}
              onDelete={setDeleteTarget}
            />
          )}

          {/* 创建表单 */}
          {showCreate && (
            <CreateForm
              topic={topic}
              setTopic={setTopic}
              maxRounds={maxRounds}
              setMaxRounds={setMaxRounds}
              selectedStrategy={selectedStrategy}
              setSelectedStrategy={setSelectedStrategy}
              compressorEnabled={compressorEnabled}
              setCompressorEnabled={setCompressorEnabled}
              compressorWindow={compressorWindow}
              setCompressorWindow={setCompressorWindow}
              compressorInterval={compressorInterval}
              setCompressorInterval={setCompressorInterval}
              seatForms={seatForms}
              updateSeat={updateSeat}
              addSeat={addSeat}
              removeSeat={removeSeat}
              applyTemplate={applyTemplate}
              onSubmit={handleSubmitCreate}
              onCancel={() => setShowCreate(false)}
              creating={creating}
            />
          )}

          {/* 讨论记录 */}
          {activeSession && (
            <div>
              {detailLoading && (
                <div className="mb-4 flex items-center gap-2 rounded-lg border border-indigo-500/15 bg-indigo-500/5 px-3 py-2 text-xs text-indigo-300" role="status" aria-live="polite">
                  <Loader2 size={14} className="animate-spin motion-reduce:animate-none" aria-hidden="true" />
                  正在同步最新圆桌记录
                </div>
              )}
              {detailError && (
                <div className="mb-4 flex items-center gap-2 rounded-lg border border-red-500/20 bg-red-500/5 px-3 py-2 text-xs text-red-300" role="alert">
                  <AlertTriangle size={14} aria-hidden="true" />
                  <span className="flex-1">{detailError}</span>
                  <button
                    type="button"
                    onClick={retryDetail}
                    className="inline-flex min-h-8 items-center gap-1 rounded px-2 transition-colors hover:bg-red-500/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500/50"
                  >
                    <RefreshCw size={12} aria-hidden="true" />
                    重试
                  </button>
                </div>
              )}
              {!connected && (
                <div className="mb-4 flex items-center gap-2 rounded-lg border border-amber-500/20 bg-amber-500/5 px-3 py-2 text-xs text-amber-300" role="status" aria-live="polite">
                  <AlertTriangle size={14} aria-hidden="true" />
                  实时连接已断开，正在重连并恢复最新记录
                </div>
              )}
              {transcript.length === 0 && !isDiscussing && activeSession.status === "waiting" && (
                <div className="text-center text-slate-500 py-16" role="status">
                  <Users size={48} className="mx-auto mb-4 opacity-30" aria-hidden="true" />
                  <p>会议已创建，点击"开始讨论"启动圆桌会议</p>
                </div>
              )}

              {transcript.map((entry, i) => (
                <TranscriptMessage
                  key={`${entry.speaker_seat_id}-${entry.round_number}-${i}`}
                  entry={entry}
                  seatIndex={seatIndexMap[entry.speaker_seat_id] ?? 0}
                  showRoundHeader={isNewRound(i)}
                />
              ))}

              {/* 流式发言 */}
              {streamingSeat && connected && (
                <div className="mb-4" role="log" aria-live="polite" aria-label={`${streamingSeat.speakerName} 正在发言`}>
                  {isStreamingNewRound() && transcript.length > 0 && (
                    <div className="flex items-center gap-3 my-4" aria-hidden="true">
                      <div className="flex-1 h-px bg-slate-600" />
                      <span className="text-xs text-slate-500 font-medium px-2">
                        第 {streamingSeat.round} 轮
                      </span>
                      <div className="flex-1 h-px bg-slate-600" />
                    </div>
                  )}
                  <div className={`bg-slate-800/80 border border-slate-700 rounded-lg p-4 ${getSeatColor(seatIndexMap[streamingSeat.seatId] ?? 0).bg} ${getSeatColor(seatIndexMap[streamingSeat.seatId] ?? 0).border}`} role="article" aria-label={`${streamingSeat.speakerName} 第${streamingSeat.round}轮发言`}>
                    <div className="flex items-center gap-2 mb-2">
                      <span className={`w-2 h-2 rounded-full ${getSeatColor(seatIndexMap[streamingSeat.seatId] ?? 0).dot}`} aria-hidden="true" />
                      <span className={`text-sm font-semibold ${getSeatColor(seatIndexMap[streamingSeat.seatId] ?? 0).text}`}>
                        {streamingSeat.speakerName}
                      </span>
                      <span className="text-xs text-slate-500">
                        R{streamingSeat.round}
                      </span>
                      <span className="sr-only">发言中</span>
                    </div>
                    <div className="prose prose-invert prose-sm max-w-none text-slate-300">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>
                        {streamingSeat.content}
                      </ReactMarkdown>
                      <span className="inline-block w-2 h-4 bg-purple-500 animate-pulse motion-reduce:animate-none ml-1" />
                    </div>
                  </div>
                </div>
              )}

              <div ref={scrollRef} />
            </div>
          )}
        </ScrollArea>

        {/* 底部控制栏 */}
        {activeSession && (
          <div className="border-t border-slate-700/50 bg-slate-800/80">
            {/* Phase 3: 讨论中 - 插话输入框 */}
            {(isDiscussing || isPaused) && (
              <div className="px-6 py-2 flex items-center gap-2 border-b border-slate-700/30">
                <label htmlFor="inject-input" className="sr-only">插话输入</label>
                <input
                  id="inject-input"
                  type="text"
                  value={injectContent}
                  onChange={(e) => setInjectContent(e.target.value)}
                  disabled={interventionPending}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && injectContent.trim() && !interventionPending) {
                      e.preventDefault();
                      void submitIntervention();
                    }
                  }}
                  placeholder="插话... (输入 @角色名 可点名发言)"
                  aria-label="插话输入，输入 @角色名 可点名发言"
                  className="flex-1 px-3 py-1.5 rounded-lg bg-slate-800 border border-slate-700 text-sm text-slate-200 placeholder-slate-600 focus:outline-none focus:border-indigo-500/50 min-h-[44px] disabled:cursor-not-allowed disabled:opacity-60"
                />
                  <button
                    type="button"
                    onClick={() => void submitIntervention()}
                    disabled={!injectContent.trim() || interventionPending}
                    aria-label="发送插话"
                  className="min-h-[44px] min-w-[44px] flex items-center justify-center rounded-lg bg-indigo-500/20 text-indigo-500 hover:bg-indigo-500/30 transition-colors duration-200 focus-visible:ring-2 focus-visible:ring-indigo-500/30 disabled:opacity-30 disabled:cursor-not-allowed cursor-pointer"
                  >
                    {interventionPending ? (
                      <Loader2 size={14} className="animate-spin motion-reduce:animate-none" aria-hidden="true" />
                    ) : (
                      <Send size={14} aria-hidden="true" />
                    )}
                  </button>
              </div>
            )}

            {/* 控制按钮行 */}
            <div className="px-6 py-3 flex items-center gap-3" role="toolbar" aria-label="会议控制">
              {activeSession.status === "waiting" && (
                <button
                  type="button"
                  onClick={() => handleStart(activeSession.session_id)}
                  disabled={actionPending}
                  aria-label="开始讨论"
                  className="flex items-center gap-2 px-4 py-2 rounded-lg bg-emerald-500/20 text-emerald-500 hover:bg-emerald-500/30 transition-colors duration-200 focus-visible:ring-2 focus-visible:ring-emerald-500/30 cursor-pointer min-h-[44px] disabled:cursor-not-allowed disabled:opacity-40"
                >
                  {pendingAction === "start" ? (
                    <Loader2 size={16} className="animate-spin motion-reduce:animate-none" aria-hidden="true" />
                  ) : (
                    <Play size={16} aria-hidden="true" />
                  )}
                  {pendingAction === "start" ? "正在开始" : "开始讨论"}
                </button>
              )}

              {isDiscussing && (
                <>
                  <div className="flex items-center gap-2 text-sm text-emerald-500" role="status" aria-label="讨论进行中">
                    <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse motion-reduce:animate-none" aria-hidden="true" />
                    讨论进行中
                  </div>
                  <button
                    type="button"
                    onClick={() => handlePause(activeSession.session_id)}
                    disabled={actionPending}
                    className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-amber-500/20 text-amber-400 hover:bg-amber-500/30 transition-colors duration-200 focus-visible:ring-2 focus-visible:ring-amber-500/30 cursor-pointer min-h-[44px] disabled:cursor-not-allowed disabled:opacity-40"
                    aria-label="暂停讨论"
                  >
                    {pendingAction === "pause" ? (
                      <Loader2 size={14} className="animate-spin motion-reduce:animate-none" aria-hidden="true" />
                    ) : (
                      <Pause size={14} aria-hidden="true" />
                    )}
                    {pendingAction === "pause" ? "正在暂停" : "暂停"}
                  </button>
                </>
              )}

              {isPaused && (
                <>
                  <div className="flex items-center gap-2 text-sm text-amber-400" role="status" aria-label="讨论已暂停">
                    <Pause size={14} aria-hidden="true" />
                    已暂停
                  </div>
                  <button
                    type="button"
                    onClick={() => handleResume(activeSession.session_id)}
                    disabled={actionPending}
                    className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-emerald-500/20 text-emerald-500 hover:bg-emerald-500/30 transition-colors duration-200 focus-visible:ring-2 focus-visible:ring-emerald-500/30 cursor-pointer min-h-[44px] disabled:cursor-not-allowed disabled:opacity-40"
                    aria-label="恢复讨论"
                  >
                    {pendingAction === "resume" ? (
                      <Loader2 size={14} className="animate-spin motion-reduce:animate-none" aria-hidden="true" />
                    ) : (
                      <SkipForward size={14} aria-hidden="true" />
                    )}
                    {pendingAction === "resume" ? "正在继续" : "继续"}
                  </button>
                </>
              )}

              {(isDiscussing || isPaused) && (
                <>
                  {/* 添加席位 */}
                  {!showAddSeat ? (
                    <button
                      type="button"
                      onClick={() => setShowAddSeat(true)}
                      disabled={actionPending}
                      className="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-slate-700/50 text-slate-400 hover:text-slate-200 transition-colors duration-200 focus-visible:ring-2 focus-visible:ring-indigo-500/30 cursor-pointer min-h-[44px] disabled:cursor-not-allowed disabled:opacity-40"
                      aria-label="添加席位"
                    >
                      <UserPlus size={14} aria-hidden="true" />
                    </button>
                  ) : (
                    <div className="flex items-center gap-2" role="group" aria-label="添加新席位">
                      <label htmlFor="new-seat-name" className="sr-only">角色名</label>
                      <input
                        id="new-seat-name"
                        type="text"
                        value={newSeatName}
                        onChange={(e) => setNewSeatName(e.target.value)}
                        disabled={pendingAction === "addSeat"}
                        placeholder="角色名"
                        aria-label="新席位角色名"
                        className="w-20 px-2 py-1 rounded bg-slate-800 border border-slate-700 text-xs text-slate-200 placeholder-slate-600 focus:outline-none focus:border-indigo-500/50 min-h-[44px]"
                      />
                      <label htmlFor="new-seat-prompt" className="sr-only">角色 Prompt</label>
                      <input
                        id="new-seat-prompt"
                        type="text"
                        value={newSeatPrompt}
                        onChange={(e) => setNewSeatPrompt(e.target.value)}
                        disabled={pendingAction === "addSeat"}
                        placeholder="角色 Prompt"
                        aria-label="新席位系统提示词"
                        className="w-32 px-2 py-1 rounded bg-slate-800 border border-slate-700 text-xs text-slate-200 placeholder-slate-600 focus:outline-none focus:border-indigo-500/50 min-h-[44px]"
                      />
                      <button
                        type="button"
                        onClick={async () => {
                          if (newSeatName.trim()) {
                            const result = await handleAddSeat(activeSession.session_id, {
                              role_name: newSeatName.trim(),
                              system_prompt: newSeatPrompt.trim() || `你是${newSeatName.trim()}。`,
                            });
                            if (result?.success) {
                              setNewSeatName("");
                              setNewSeatPrompt("");
                              setShowAddSeat(false);
                            }
                          }
                        }}
                        disabled={!newSeatName.trim() || actionPending}
                        aria-label="确认添加席位"
                        className="min-h-[44px] min-w-[44px] flex items-center justify-center rounded bg-emerald-500/20 text-emerald-500 hover:bg-emerald-500/30 transition-colors duration-200 focus-visible:ring-2 focus-visible:ring-emerald-500/30 disabled:opacity-30 disabled:cursor-not-allowed cursor-pointer"
                      >
                        {pendingAction === "addSeat" ? (
                          <Loader2 size={12} className="animate-spin motion-reduce:animate-none" aria-hidden="true" />
                        ) : (
                          <Plus size={12} aria-hidden="true" />
                        )}
                      </button>
                      <button
                        type="button"
                        onClick={() => { setShowAddSeat(false); setNewSeatName(""); setNewSeatPrompt(""); }}
                        disabled={actionPending}
                        aria-label="取消添加席位"
                        className="min-h-[44px] min-w-[44px] flex items-center justify-center rounded text-slate-500 hover:text-slate-300 transition-colors duration-200 focus-visible:ring-2 focus-visible:ring-indigo-500/30 cursor-pointer disabled:cursor-not-allowed disabled:opacity-40"
                      >
                        <X size={12} aria-hidden="true" />
                      </button>
                    </div>
                  )}

                  {/* 终止按钮 */}
                  <button
                    type="button"
                    onClick={() => handleStop(activeSession.session_id)}
                    disabled={actionPending}
                    aria-label="终止讨论"
                    className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-red-500/20 text-red-400 hover:bg-red-500/30 transition-colors duration-200 focus-visible:ring-2 focus-visible:ring-red-500/30 ml-auto cursor-pointer min-h-[44px] disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    {pendingAction === "stop" ? (
                      <Loader2 size={14} className="animate-spin motion-reduce:animate-none" aria-hidden="true" />
                    ) : (
                      <Square size={14} aria-hidden="true" />
                    )}
                    {pendingAction === "stop" ? "正在终止" : "终止"}
                  </button>
                </>
              )}

              {activeSession.status === "ended" && (
                <>
                  <span className="text-sm text-slate-400" role="status">会议已结束</span>

                  <button
                    type="button"
                    onClick={() => {
                      clearActive();
                      setShowCreate(true);
                      refreshList();
                    }}
                    aria-label="新建圆桌会议"
                    className="flex items-center gap-2 px-4 py-2 rounded-lg bg-indigo-500/20 text-indigo-500 hover:bg-indigo-500/30 transition-colors duration-200 focus-visible:ring-2 focus-visible:ring-indigo-500/30 ml-auto cursor-pointer min-h-[44px]"
                  >
                    <Plus size={16} aria-hidden="true" />
                    新建圆桌
                  </button>
                </>
              )}
            </div>
          </div>
        )}
      </div>

      {/* ========== 右侧面板 ========== */}
      <div className="w-72 border-l border-slate-700/50 bg-slate-800/80 flex flex-col" role="complementary" aria-label="席位信息面板">
        <div className="px-4 py-3 border-b border-slate-700/50">
          <h3 className="text-sm font-semibold text-slate-300">席位状态</h3>
        </div>
        <ScrollArea className="flex-1 px-4 py-3">
          {seats.length > 0 ? (
            <div className="space-y-2">
              {seats.map((seat, i) => (
                <SeatCard
                  key={seat.seat_id}
                  seat={seat}
                  seatIndex={i}
                  isDiscussing={isDiscussing || isPaused}
                  onRemove={
                    (isDiscussing || isPaused || activeSession?.status === "waiting")
                      ? (seatId) => activeSession && handleRemoveSeat(activeSession.session_id, seatId)
                      : undefined
                  }
                  actionsDisabled={actionPending}
                  onNominate={
                    (isDiscussing || isPaused)
                      ? (seatId) => activeSession && handleNominate(activeSession.session_id, seatId)
                      : undefined
                  }
                />
              ))}
            </div>
          ) : (
            <p className="text-xs text-slate-500 text-center py-8">
              暂无席位信息
            </p>
          )}

          {/* 会议信息摘要 */}
          {activeSession && (
            <div className="mt-6 pt-4 border-t border-slate-700/50 space-y-2">
              <InfoRow label="会话 ID" value={activeSession.session_id} />
              <InfoRow label="状态" value={activeSession.status} />
              <InfoRow label="策略" value={strategy === "moderator_decides" ? "智能主持" : "轮询"} />
              <InfoRow label="席位数" value={String(seats.length)} />
              <InfoRow label="轮次" value={`${currentRound} / ${activeSession.max_rounds}`} />
              <InfoRow label="发言数" value={String(transcript.length)} />
            </div>
          )}

          {/* Phase 2: 阶段摘要 */}
          {roundSummaries.length > 0 && (
            <div className="mt-4 pt-4 border-t border-slate-700/50">
              <div className="flex items-center gap-2 mb-2">
                <FileText size={14} className="text-amber-400" aria-hidden="true" />
                <h4 className="text-xs font-semibold text-amber-400">阶段摘要</h4>
              </div>
              <div className="space-y-2">
                {roundSummaries.map((s, i) => (
                  <div key={i} className="text-xs text-slate-400 bg-slate-700/50 rounded p-2">
                    <span className="text-amber-400 font-medium">R{s.round}</span>
                    <span className="text-slate-500 ml-1">({s.source})</span>
                    <p className="mt-1 line-clamp-3">{s.content}</p>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Phase 2: 会议结论 */}
          {conclusion && (
            <div className="mt-4 pt-4 border-t border-slate-700/50">
              <div className="flex items-center gap-2 mb-2">
                <CircleCheck size={14} className="text-emerald-500" aria-hidden="true" />
                <h4 className="text-xs font-semibold text-emerald-500">会议结论</h4>
              </div>
              <div className="text-xs text-slate-300 bg-emerald-500/5 border border-emerald-500/20 rounded p-2">
                <p className="line-clamp-6">{conclusion.content}</p>
                <span className="text-slate-500 text-xs mt-1 block">({conclusion.source})</span>
              </div>
            </div>
          )}

          {/* Phase 3: 结构化结论详情 */}
          {structuredConclusion && (
            <div className="mt-4 pt-4 border-t border-slate-700/50 space-y-3">
              <h4 className="text-xs font-semibold text-slate-300 flex items-center gap-1.5">
                <BarChart3 size={14} className="text-slate-400" aria-hidden="true" />
                结构化结论
              </h4>
              {structuredConclusion.consensus?.length > 0 && (
                <div>
                  <span className="text-xs text-emerald-500 font-semibold block mb-1 flex items-center gap-1">
                    <CircleCheck size={12} aria-hidden="true" /> 共识
                  </span>
                  {structuredConclusion.consensus.map((c, i) => (
                    <p key={i} className="text-xs text-slate-400 pl-2 bg-emerald-500/5 rounded mb-1 py-0.5">{c}</p>
                  ))}
                </div>
              )}
              {structuredConclusion.disagreements?.length > 0 && (
                <div>
                  <span className="text-xs text-amber-400 font-semibold block mb-1 flex items-center gap-1">
                    <AlertTriangle size={12} aria-hidden="true" /> 分歧
                  </span>
                  {structuredConclusion.disagreements.map((d, i) => (
                    <p key={i} className="text-xs text-slate-400 pl-2 bg-amber-400/5 rounded mb-1 py-0.5">{d}</p>
                  ))}
                </div>
              )}
              {structuredConclusion.pending_verification?.length > 0 && (
                <div>
                  <span className="text-xs text-cyan-500 font-semibold block mb-1 flex items-center gap-1">
                    <Search size={12} aria-hidden="true" /> 待验证
                  </span>
                  {structuredConclusion.pending_verification.map((v, i) => (
                    <p key={i} className="text-xs text-slate-400 pl-2 bg-cyan-500/5 rounded mb-1 py-0.5">{v}</p>
                  ))}
                </div>
              )}
              {structuredConclusion.action_items?.length > 0 && (
                <div>
                  <span className="text-xs text-purple-500 font-semibold block mb-1 flex items-center gap-1">
                    <Rocket size={12} aria-hidden="true" /> 行动项
                  </span>
                  {structuredConclusion.action_items.map((a, i) => (
                    <p key={i} className="text-xs text-slate-400 pl-2 bg-purple-500/5 rounded mb-1 py-0.5">{a}</p>
                  ))}
                </div>
              )}
            </div>
          )}
        </ScrollArea>
      </div>
      <DeleteRoundtableDialog
        roundtable={deleteTarget}
        deleting={pendingAction === "delete"}
        onCancel={() => setDeleteTarget(null)}
        onConfirm={() => void confirmDelete()}
      />
    </div>
  );
}

function CreateForm({
  topic,
  setTopic,
  maxRounds,
  setMaxRounds,
  selectedStrategy,
  setSelectedStrategy,
  compressorEnabled,
  setCompressorEnabled,
  compressorWindow,
  setCompressorWindow,
  compressorInterval,
  setCompressorInterval,
  seatForms,
  updateSeat,
  addSeat,
  removeSeat,
  applyTemplate,
  onSubmit,
  onCancel,
  creating,
}: {
  topic: string;
  setTopic: (v: string) => void;
  maxRounds: number;
  setMaxRounds: (v: number) => void;
  selectedStrategy: string;
  setSelectedStrategy: (v: string) => void;
  compressorEnabled: boolean;
  setCompressorEnabled: (v: boolean) => void;
  compressorWindow: number;
  setCompressorWindow: (v: number) => void;
  compressorInterval: number;
  setCompressorInterval: (v: number) => void;
  seatForms: SeatFormItem[];
  updateSeat: (i: number, field: keyof SeatFormItem, value: string | number | boolean) => void;
  addSeat: () => void;
  removeSeat: (i: number) => void;
  applyTemplate: (tpl: RoundtableTemplate) => void;
  onSubmit: () => void;
  onCancel: () => void;
  creating: boolean;
}) {
  return (
    <section className="max-w-2xl mx-auto py-4" role="dialog" aria-modal="false" aria-label="创建圆桌会议">
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-lg font-semibold text-slate-200">创建圆桌会议</h2>
        <button type="button" onClick={onCancel} className="text-slate-500 hover:text-slate-300 cursor-pointer" aria-label="关闭创建表单">
          <X size={20} aria-hidden="true" />
        </button>
      </div>

      {/* 快速模板 */}
      <fieldset className="mb-6">
        <legend className="text-xs text-slate-400 mb-2">快速模板</legend>
        <div className="flex gap-2 flex-wrap">
          {ROUNDTABLE_TEMPLATES.map((tpl) => (
            <button
              type="button"
              key={tpl.name}
              onClick={() => applyTemplate(tpl)}
              aria-label={`应用 ${tpl.name} 模板`}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-slate-700 border border-slate-700 text-xs text-slate-300 hover:border-indigo-500/50 transition-colors cursor-pointer focus-visible:outline-2 focus-visible:outline-indigo-500 focus-visible:outline-offset-2"
            >
              {tpl.strategy === "moderator_decides" ? <Brain size={12} className="text-amber-400" aria-hidden="true" /> : <Zap size={12} className="text-cyan-500" aria-hidden="true" />}
              {tpl.name}
            </button>
          ))}
        </div>
      </fieldset>

      {/* 主题 */}
      <div className="mb-4">
        <label htmlFor="rt-topic" className="text-xs text-slate-400 mb-1.5 block">讨论主题 *</label>
        <input
          id="rt-topic"
          type="text"
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
          placeholder="例如：如何优化系统架构"
          aria-required="true"
          className="w-full px-3 py-2 rounded-lg bg-slate-800 border border-slate-700 text-sm text-slate-200 placeholder-slate-600 focus:outline-none focus:border-indigo-500/50 min-h-[44px]"
        />
      </div>

      {/* 调度策略 + 轮次 */}
      <div className="mb-6 flex gap-4">
        <div className="flex-1">
          <span className="text-xs text-slate-400 mb-1.5 block" id="strategy-label">调度策略</span>
          <div className="flex gap-2" role="radiogroup" aria-labelledby="strategy-label">
            <button
              type="button"
              onClick={() => setSelectedStrategy("round_robin")}
              role="radio"
              aria-checked={selectedStrategy === "round_robin"}
              className={`flex-1 px-3 py-2 rounded-lg text-xs border transition-colors cursor-pointer focus-visible:ring-2 focus-visible:ring-indigo-500/30 min-h-[44px] ${
                selectedStrategy === "round_robin"
                  ? "border-indigo-500/50 bg-indigo-500/10 text-indigo-500"
                  : "border-slate-700 bg-slate-800 text-slate-400 hover:border-slate-600"
              }`}
            >
              <Zap size={12} className="inline mr-1 text-cyan-500" aria-hidden="true" />
              固定轮询
            </button>
            <button
              type="button"
              onClick={() => setSelectedStrategy("moderator_decides")}
              role="radio"
              aria-checked={selectedStrategy === "moderator_decides"}
              className={`flex-1 px-3 py-2 rounded-lg text-xs border transition-colors cursor-pointer focus-visible:ring-2 focus-visible:ring-amber-500/30 min-h-[44px] ${
                selectedStrategy === "moderator_decides"
                  ? "border-amber-400/50 bg-amber-400/10 text-amber-400"
                  : "border-slate-700 bg-slate-800 text-slate-400 hover:border-slate-600"
              }`}
            >
              <Brain size={12} className="inline mr-1 text-amber-400" aria-hidden="true" />
              智能主持
            </button>
          </div>
          {selectedStrategy === "moderator_decides" && (
            <p className="text-xs text-amber-400/60 mt-1">
              Moderator 将由 AI 动态决定发言顺序和讨论终止
            </p>
          )}
        </div>
        <div>
          <label htmlFor="rt-max-rounds" className="text-xs text-slate-400 mb-1.5 block">讨论轮次</label>
          <input
            id="rt-max-rounds"
            type="number"
            value={maxRounds}
            onChange={(e) => setMaxRounds(Math.max(1, Math.min(20, Number(e.target.value))))}
            min={1}
            max={20}
            className="w-20 px-3 py-2 rounded-lg bg-slate-800 border border-slate-700 text-sm text-slate-200 focus:outline-none focus:border-indigo-500/50 min-h-[44px]"
          />
        </div>
      </div>

      {/* 上下文压缩配置 */}
      <fieldset className="mb-6">
        <legend className="text-xs text-slate-400 mb-2">上下文压缩</legend>
        <label className="flex items-center gap-2 text-xs text-slate-400 mb-2 cursor-pointer min-h-[44px]">
          <input
            type="checkbox"
            role="switch"
            aria-checked={compressorEnabled}
            checked={compressorEnabled}
            onChange={(e) => setCompressorEnabled(e.target.checked)}
            className="w-4 h-4 rounded accent-indigo-500 cursor-pointer"
          />
          启用上下文压缩（长讨论推荐）
        </label>
        {compressorEnabled && (
          <div className="flex gap-4 mt-2 pl-5">
            <div>
              <label htmlFor="rt-compressor-window" className="text-xs text-slate-500 block mb-1">窗口大小</label>
              <input
                id="rt-compressor-window"
                type="number"
                value={compressorWindow}
                onChange={(e) => setCompressorWindow(Math.max(5, Math.min(100, Number(e.target.value))))}
                min={5}
                max={100}
                className="w-16 px-2 py-1 rounded bg-slate-800 border border-slate-700 text-xs text-slate-400 focus:outline-none focus:border-indigo-500/50 min-h-[44px]"
              />
            </div>
            <div>
              <label htmlFor="rt-compressor-interval" className="text-xs text-slate-500 block mb-1">摘要间隔轮次</label>
              <input
                id="rt-compressor-interval"
                type="number"
                value={compressorInterval}
                onChange={(e) => setCompressorInterval(Math.max(0, Math.min(10, Number(e.target.value))))}
                min={0}
                max={10}
                className="w-16 px-2 py-1 rounded bg-slate-800 border border-slate-700 text-xs text-slate-400 focus:outline-none focus:border-indigo-500/50 min-h-[44px]"
              />
            </div>
          </div>
        )}
      </fieldset>

      {/* 席位列表 */}
      <fieldset className="mb-6">
        <legend className="text-xs text-slate-400 mb-3">席位配置（{seatForms.length}/6）</legend>
        <div className="flex items-center justify-end mb-3">
          {seatForms.length < 6 && (
            <button
              type="button"
              onClick={addSeat}
              className="text-xs text-cyan-500 hover:text-cyan-500/80 flex items-center gap-1 cursor-pointer focus-visible:ring-2 focus-visible:ring-cyan-500/30 min-h-[44px]"
              aria-label="添加新席位"
            >
              <Plus size={12} aria-hidden="true" /> 添加席位
            </button>
          )}
        </div>
        <div className="space-y-3">
          {seatForms.map((seat, i) => (
            <div key={i} className="bg-slate-800/80 border border-slate-700 rounded-lg p-3 space-y-2" role="group" aria-label={`席位 ${i + 1}: ${seat.role_name || "未命名"}`}>
              <div className="flex items-center gap-2">
                <span className={`w-2 h-2 rounded-full ${getSeatColor(i).dot}`} aria-hidden="true" />
                <label htmlFor={`seat-name-${i}`} className="sr-only">角色名称 {i + 1}</label>
                <input
                  id={`seat-name-${i}`}
                  type="text"
                  value={seat.role_name}
                  onChange={(e) => updateSeat(i, "role_name", e.target.value)}
                  placeholder={`角色名称 ${i + 1}`}
                  aria-required="true"
                  className="flex-1 px-2 py-1 rounded bg-slate-800 border border-slate-700 text-sm text-slate-200 placeholder-slate-600 focus:outline-none focus:border-indigo-500/50 min-h-[44px]"
                />
                <label className="flex items-center gap-1 text-xs text-slate-500 cursor-pointer min-h-[44px]">
                  <input
                    type="checkbox"
                    role="switch"
                    aria-checked={seat.is_moderator}
                    checked={seat.is_moderator}
                    onChange={(e) => updateSeat(i, "is_moderator", e.target.checked)}
                    className="w-4 h-4 rounded accent-indigo-500 cursor-pointer"
                  />
                  主持
                </label>
                <label htmlFor={`seat-temp-${i}`} className="sr-only">Temperature</label>
                <input
                  id={`seat-temp-${i}`}
                  type="number"
                  value={seat.temperature}
                  onChange={(e) => updateSeat(i, "temperature", Number(e.target.value))}
                  step={0.1}
                  min={0}
                  max={2}
                  className="w-16 px-2 py-1 rounded bg-slate-800 border border-slate-700 text-xs text-slate-400 focus:outline-none focus:border-indigo-500/50 min-h-[44px]"
                  title="Temperature"
                />
                {seatForms.length > 2 && (
                  <button
                    type="button"
                    onClick={() => removeSeat(i)}
                    aria-label={`删除席位: ${seat.role_name || `角色 ${i + 1}`}`}
                    className="text-slate-600 hover:text-red-400 cursor-pointer focus-visible:ring-2 focus-visible:ring-red-500/30 min-h-[44px] min-w-[44px] flex items-center justify-center"
                  >
                    <X size={14} aria-hidden="true" />
                  </button>
                )}
              </div>
              <label htmlFor={`seat-prompt-${i}`} className="sr-only">角色 Prompt</label>
              <textarea
                id={`seat-prompt-${i}`}
                value={seat.system_prompt}
                onChange={(e) => updateSeat(i, "system_prompt", e.target.value)}
                placeholder="角色的 System Prompt（留空将自动生成）"
                rows={2}
                className="w-full px-2 py-1 rounded bg-slate-800 border border-slate-700 text-xs text-slate-300 placeholder-slate-600 focus:outline-none focus:border-indigo-500/50 resize-none min-h-[44px]"
              />
            </div>
          ))}
        </div>
      </fieldset>

      {/* 提交按钮 */}
      <div className="flex gap-3">
        <button
          type="button"
          onClick={onSubmit}
          disabled={creating || !topic.trim() || seatForms.some((s) => !s.role_name.trim())}
          aria-label={creating ? "正在创建圆桌会议" : "创建圆桌会议"}
          className="flex-1 py-2.5 rounded-lg bg-indigo-600 text-white font-medium hover:bg-indigo-500 transition-colors focus-visible:ring-2 focus-visible:ring-indigo-500/30 disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer min-h-[44px]"
        >
          {creating ? "创建中..." : "创建圆桌会议"}
        </button>
        <button
          type="button"
          onClick={onCancel}
          aria-label="取消创建"
          className="px-6 py-2.5 rounded-lg bg-slate-700 border border-slate-700 text-slate-400 hover:text-slate-200 transition-colors cursor-pointer min-h-[44px] focus-visible:ring-2 focus-visible:ring-indigo-500/30"
        >
          取消
        </button>
      </div>
    </section>
  );
}
