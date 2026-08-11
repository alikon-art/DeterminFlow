import { memo, useState, useRef, useEffect } from "react";
import { MessageSquare, FileText, FolderCode, GripVertical } from "lucide-react";
import WorkspaceExplorer from "./WorkspaceExplorer";
import SessionsPanel from "./SessionsPanel";
import PromptPanel from "./PromptPanel";
import { Session, SessionDetail } from "../types";
import { fetchSessionSystemPrompt } from "../lib/api";

interface ResizableSidePanelProps {
  sidePanel: "sessions" | "prompt" | "workspace";
  setSidePanel: (panel: "sessions" | "prompt" | "workspace") => void;
  sortedSessions: Session[];
  viewingSessionId: string | null;
  mainSessionId: string | null;
  onViewSession: (sessionId: string) => void;
  onDeleteSession: (sessionId: string, e: React.MouseEvent) => void;
  onKillSession: (sessionId: string, e: React.MouseEvent) => void;
  onCreateSession: (agentType?: string, projectName?: string) => void;
  onDeleteProject: (projectName: string) => void;
  llmContext: Awaited<ReturnType<typeof fetchSessionSystemPrompt>> | null;
  promptLoading: boolean;
  onRefreshPrompt: () => void;
  viewingSession: SessionDetail | null;
  sessions: Session[];
}

function ResizableSidePanel({
  sidePanel,
  setSidePanel,
  sortedSessions,
  viewingSessionId,
  mainSessionId,
  onViewSession,
  onDeleteSession,
  onKillSession,
  onCreateSession,
  onDeleteProject,
  llmContext,
  promptLoading,
  onRefreshPrompt,
  viewingSession,
  sessions,
}: ResizableSidePanelProps) {
  const [width, setWidth] = useState(320); // 默认 320px (w-80)
  const [isResizing, setIsResizing] = useState(false);
  const panelRef = useRef<HTMLDivElement>(null);

  const handleMouseDown = (e: React.MouseEvent) => {
    e.preventDefault();
    setIsResizing(true);
  };

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!isResizing) return;

      const newWidth = window.innerWidth - e.clientX;
      // 限制宽度在 280px 到 800px 之间
      const clampedWidth = Math.max(280, Math.min(800, newWidth));
      setWidth(clampedWidth);
    };

    const handleMouseUp = () => {
      setIsResizing(false);
    };

    if (isResizing) {
      document.addEventListener("mousemove", handleMouseMove);
      document.addEventListener("mouseup", handleMouseUp);
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
    }

    return () => {
      document.removeEventListener("mousemove", handleMouseMove);
      document.removeEventListener("mouseup", handleMouseUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
  }, [isResizing]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
      e.preventDefault();
      const delta = e.key === 'ArrowLeft' ? -10 : 10;
      const newWidth = Math.max(280, Math.min(800, width + delta));
      setWidth(newWidth);
    }
  };

  return (
    <div
      ref={panelRef}
      className="border-l border-border bg-slate-900 flex flex-col relative"
      style={{ width: `${width}px`, minWidth: "280px", maxWidth: "800px" }}
    >
      {/* Resize Handle */}
      <div
        onMouseDown={handleMouseDown}
        onKeyDown={handleKeyDown}
        role="separator"
        aria-orientation="vertical"
        aria-label="调整侧边面板宽度，使用左右箭头键调整"
        tabIndex={0}
        className={`absolute left-0 top-0 bottom-0 w-1 cursor-col-resize hover:bg-indigo-500/30 transition-colors z-10 group ${
          isResizing ? "bg-indigo-500/50" : ""
        }`}
      >
        <div className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 opacity-0 group-hover:opacity-100 transition-opacity">
          <GripVertical size={16} className="text-indigo-500" aria-hidden="true" />
        </div>
      </div>

      {/* Panel Tabs */}
      <div className="flex border-b border-border" role="tablist" aria-label="侧边面板导航">
        {[
          { key: "sessions" as const, icon: MessageSquare, label: "会话" },
          { key: "prompt" as const, icon: FileText, label: "提示词" },
          { key: "workspace" as const, icon: FolderCode, label: "工作空间" },
        ].map(({ key, icon: Icon, label }) => (
          <button
            key={key}
            onClick={() => setSidePanel(key)}
            role="tab"
            aria-selected={sidePanel === key}
            aria-controls={`panel-${key}`}
            className={`flex-1 flex items-center justify-center gap-1.5 py-2.5 text-xs font-medium transition-colors cursor-pointer min-h-[44px] ${
              sidePanel === key
                ? "text-indigo-500 border-b-2 border-indigo-500"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            <Icon size={14} aria-hidden="true" />
            {label}
          </button>
        ))}
      </div>

      {/* Panel Content */}
      <div className="flex-1 overflow-hidden">
        {sidePanel === "sessions" && (
          <div id="panel-sessions" className="h-full" role="tabpanel" aria-label="会话面板">
            <SessionsPanel
              sessions={sortedSessions}
              viewingSessionId={viewingSessionId}
              mainSessionId={mainSessionId}
              onViewSession={onViewSession}
              onDeleteSession={onDeleteSession}
              onKillSession={onKillSession}
              onCreateSession={onCreateSession}
              onDeleteProject={onDeleteProject}
            />
          </div>
        )}
        {sidePanel === "prompt" && (
          <div id="panel-prompt" className="h-full" role="tabpanel" aria-label="提示词面板">
            <PromptPanel
              llmContext={llmContext}
              loading={promptLoading}
              onRefresh={onRefreshPrompt}
              sessionId={viewingSessionId || mainSessionId || ""}
            />
          </div>
        )}
        {sidePanel === "workspace" && (
          <div id="panel-workspace" className="h-full" role="tabpanel" aria-label="工作空间面板">
            <WorkspaceExplorer
              sessionId={viewingSessionId || mainSessionId || null}
              workspacePath={
                viewingSession?.workspace_path ||
                sessions.find((s) => s.session_id === mainSessionId)?.workspace_path
              }
            />
          </div>
        )}
      </div>
    </div>
  );
}

export default memo(ResizableSidePanel);
