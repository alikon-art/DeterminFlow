import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, ChevronUp, Loader2, RefreshCw, X } from "lucide-react";
import ModelListEditor from "./ModelListEditor";
import { mergeUniqueModels } from "../lib/model-options";
import type { ProviderSchema } from "../types";

interface AddProviderInput {
  provider_id: string;
  name: string;
  base_url: string;
  api_key: string;
  models: string[];
}

interface Props {
  open: boolean;
  schemas: Record<string, ProviderSchema>;
  existingProviderIds: string[];
  onClose: () => void;
  onAdd: (input: AddProviderInput) => Promise<void>;
  onDiscoverModels: (input: {
    provider_id: string;
    base_url?: string;
    api_key?: string;
  }) => Promise<string[]>;
}

export default function AddModelProviderDialog({
  open,
  schemas,
  existingProviderIds,
  onClose,
  onAdd,
  onDiscoverModels,
}: Props) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const availableProviderIds = useMemo(
    () => Object.keys(schemas).filter((id) => !existingProviderIds.includes(id)),
    [schemas, existingProviderIds],
  );
  const availableProviderKey = availableProviderIds.join("|");
  const firstAvailableProviderId = availableProviderIds[0] || "";
  const [providerId, setProviderId] = useState("");
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const [apiExpanded, setApiExpanded] = useState(false);
  const [discovering, setDiscovering] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const selectProvider = useCallback((id: string) => {
    const schema = schemas[id];
    setProviderId(id);
    setName(schema?.display_name || "");
    setBaseUrl(schema?.default_base_url || "");
    setModels([]);
    setError(null);
  }, [schemas]);

  useEffect(() => {
    if (!open) return;
    selectProvider(firstAvailableProviderId);
    setApiKey("");
    setApiExpanded(false);
    const frame = window.requestAnimationFrame(() => {
      dialogRef.current?.querySelector<HTMLElement>("select, input")?.focus();
    });
    return () => window.cancelAnimationFrame(frame);
  }, [open, availableProviderKey, firstAvailableProviderId, selectProvider]);

  useEffect(() => {
    if (!open) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [open, onClose]);

  const discoverModels = async () => {
    if (!providerId || !baseUrl) return;
    setDiscovering(true);
    setError(null);
    try {
      const result = await onDiscoverModels({
        provider_id: providerId,
        base_url: baseUrl,
        api_key: apiKey,
      });
      if (result.length === 0) {
        setError("供应商未返回可选模型");
        return;
      }
      setModels((current) => mergeUniqueModels(current, result));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "拉取模型失败");
    } finally {
      setDiscovering(false);
    }
  };

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/70 px-4 backdrop-blur-sm"
      onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}
      role="presentation"
    >
      <section
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="add-provider-title"
        className="max-h-[88dvh] w-full max-w-xl overflow-y-auto rounded-2xl border border-slate-700 bg-slate-900 shadow-2xl shadow-slate-950/60"
      >
        <header className="sticky top-0 z-10 flex items-center justify-between border-b border-slate-700 bg-slate-900/95 px-5 py-4 backdrop-blur">
          <div>
            <h3 id="add-provider-title" className="text-lg font-semibold text-slate-100">添加模型供应商</h3>
            <p className="mt-0.5 text-xs text-slate-500">选择模板后填写凭据并拉取模型</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭"
            className="flex min-h-11 min-w-11 items-center justify-center rounded-lg text-slate-400 hover:bg-slate-800 hover:text-slate-200"
          >
            <X size={18} />
          </button>
        </header>

        <div className="space-y-4 p-5">
          {availableProviderIds.length === 0 ? (
            <div className="rounded-lg border border-slate-700 bg-slate-800/50 p-4 text-sm text-slate-400">
              所有支持的供应商都已添加。
            </div>
          ) : (
            <>
              <label className="block text-sm text-slate-300">
                供应商
                <select
                  value={providerId}
                  onChange={(event) => selectProvider(event.target.value)}
                  className="mt-1 min-h-11 w-full appearance-none rounded-lg border border-slate-600 bg-slate-800 px-3 text-sm text-slate-100 outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/20"
                >
                  {availableProviderIds.map((id) => <option key={id} value={id}>{schemas[id].display_name}</option>)}
                </select>
              </label>

              <label className="block text-sm text-slate-300">
                显示名称
                <input
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  className="mt-1 min-h-11 w-full rounded-lg border border-slate-600 bg-slate-800 px-3 text-sm text-slate-100 outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/20"
                />
              </label>

              <label className="block text-sm text-slate-300">
                API Key
                <input
                  type="password"
                  value={apiKey}
                  onChange={(event) => setApiKey(event.target.value)}
                  onBlur={() => { if (apiKey.trim() && models.length === 0) void discoverModels(); }}
                  placeholder="输入后自动拉取模型"
                  className="mt-1 min-h-11 w-full rounded-lg border border-slate-600 bg-slate-800 px-3 text-sm text-slate-100 outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/20"
                />
              </label>

              <div>
                <div className="mb-2 flex items-end justify-between gap-3">
                  <div>
                    <h4 className="text-sm font-medium text-slate-200">模型列表</h4>
                    <p className="mt-0.5 text-xs text-slate-500">
                      {existingProviderIds.length === 0
                        ? "第一个会成为 Main 的默认模型，可拖动排序"
                        : "第一个是该供应商默认模型；供应商置顶后供 Main 使用"}
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={discoverModels}
                    disabled={discovering}
                    className="flex min-h-10 items-center gap-1.5 rounded-lg px-3 text-xs text-indigo-300 hover:bg-indigo-500/10 disabled:opacity-50"
                  >
                    <RefreshCw size={14} className={discovering ? "animate-spin motion-reduce:animate-none" : ""} />
                    拉取模型
                  </button>
                </div>
                <ModelListEditor
                  models={models}
                  onChange={setModels}
                  inputLabel={`为 ${name || "供应商"} 输入模型`}
                />
              </div>

              <div className="rounded-lg border border-slate-700 bg-slate-800/30">
                <button
                  type="button"
                  onClick={() => setApiExpanded(!apiExpanded)}
                  aria-expanded={apiExpanded}
                  className="flex min-h-11 w-full items-center justify-between px-3 text-sm text-slate-300 hover:bg-slate-800/60"
                >
                  API 地址
                  {apiExpanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                </button>
                {apiExpanded && (
                  <div className="border-t border-slate-700 p-3">
                    <input
                      value={baseUrl}
                      onChange={(event) => setBaseUrl(event.target.value)}
                      aria-label="API 地址"
                      className="min-h-11 w-full rounded-lg border border-slate-600 bg-slate-800 px-3 font-mono text-sm text-slate-100 outline-none focus:border-indigo-500"
                    />
                    <p className="mt-2 text-xs text-slate-500">
                      完整 API Base URL，包含版本路径（v1，不带末尾斜杠/）
                    </p>
                  </div>
                )}
              </div>
            </>
          )}

          {error && <p className="text-sm text-amber-400" role="alert">{error}</p>}
        </div>

        <footer className="sticky bottom-0 flex justify-end gap-3 border-t border-slate-700 bg-slate-900/95 px-5 py-4 backdrop-blur">
          <button type="button" onClick={onClose} className="min-h-11 rounded-lg border border-slate-600 px-4 text-sm text-slate-300 hover:bg-slate-800">取消</button>
          <button
            type="button"
            disabled={!providerId || !name.trim() || !baseUrl.trim() || models.length === 0 || saving}
            onClick={async () => {
              setSaving(true);
              setError(null);
              try {
                await onAdd({
                  provider_id: providerId,
                  name: name.trim(),
                  base_url: baseUrl.trim(),
                  api_key: apiKey.trim(),
                  models,
                });
                onClose();
              } catch (reason) {
                setError(reason instanceof Error ? reason.message : "添加供应商失败");
              } finally {
                setSaving(false);
              }
            }}
            className="flex min-h-11 items-center gap-2 rounded-lg bg-indigo-600 px-4 text-sm font-medium text-white hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {saving && <Loader2 size={15} className="animate-spin motion-reduce:animate-none" />}
            添加供应商
          </button>
        </footer>
      </section>
    </div>
  );
}
