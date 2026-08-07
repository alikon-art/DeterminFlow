import { useEffect, useState } from "react";
import {
  ArrowUp,
  ChevronDown,
  ChevronUp,
  Eye,
  EyeOff,
  RefreshCw,
  Save,
  Trash2,
} from "lucide-react";
import ModelListEditor from "./ModelListEditor";
import { mergeUniqueModels } from "../lib/model-options";
import type { ModelProvider, ProviderSchema } from "../types";

export type { ModelProvider, ProviderSchema } from "../types";

interface Props {
  provider: ModelProvider;
  schema: ProviderSchema | null;
  isDefault: boolean;
  onUpdate: (providerId: string, updates: Partial<ModelProvider>) => Promise<void>;
  onDelete: (providerId: string) => Promise<void>;
  onPrioritize: (providerId: string) => Promise<void>;
  onDiscoverModels: (input: {
    provider_id: string;
    base_url?: string;
    api_key?: string;
  }) => Promise<string[]>;
}

export default function ModelProviderCard({
  provider,
  schema,
  isDefault,
  onUpdate,
  onDelete,
  onPrioritize,
  onDiscoverModels,
}: Props) {
  const [expanded, setExpanded] = useState(false);
  const [apiAddressExpanded, setApiAddressExpanded] = useState(false);
  const [showApiKey, setShowApiKey] = useState(false);
  const [localProvider, setLocalProvider] = useState(provider);
  const [edited, setEdited] = useState(false);
  const [saving, setSaving] = useState(false);
  const [discovering, setDiscovering] = useState(false);
  const [discoverError, setDiscoverError] = useState<string | null>(null);

  useEffect(() => {
    setLocalProvider(provider);
    setEdited(false);
  }, [provider]);

  const discoverModels = async () => {
    setDiscovering(true);
    setDiscoverError(null);
    try {
      const models = await onDiscoverModels({
        provider_id: provider.id,
        base_url: localProvider.base_url,
        api_key: localProvider.api_key,
      });
      if (models.length === 0) {
        setDiscoverError("供应商未返回可选模型");
        return;
      }
      setLocalProvider((current) => {
        const nextModels = mergeUniqueModels(current.models, models);
        if (nextModels.length !== current.models.length) setEdited(true);
        return { ...current, models: nextModels };
      });
    } catch (error) {
      setDiscoverError(error instanceof Error ? error.message : "拉取模型失败");
    } finally {
      setDiscovering(false);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await onUpdate(provider.id, {
        name: localProvider.name,
        base_url: localProvider.base_url,
        api_key: localProvider.api_key,
        models: localProvider.models,
        maxContextTokens: localProvider.maxContextTokens,
        models_config: localProvider.models_config,
        hyperparameter_values: localProvider.hyperparameter_values,
      });
      setEdited(false);
    } finally {
      setSaving(false);
    }
  };

  const updateHyperparam = (key: string, value: unknown) => {
    setLocalProvider({
      ...localProvider,
      hyperparameter_values: {
        ...localProvider.hyperparameter_values,
        [key]: value,
      },
    });
    setEdited(true);
  };

  return (
    <article className={`rounded-xl border bg-slate-900/50 p-4 ${isDefault ? "border-indigo-500/50" : "border-slate-700"}`}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <button
            type="button"
            onClick={() => setExpanded(!expanded)}
            aria-expanded={expanded}
            aria-label={expanded ? "折叠供应商配置" : "展开供应商配置"}
            className="flex min-h-11 min-w-11 items-center justify-center rounded-lg text-slate-400 hover:bg-slate-800 hover:text-slate-200"
          >
            {expanded ? <ChevronUp size={18} /> : <ChevronDown size={18} />}
          </button>
          <div className="min-w-0">
            <h4 className="truncate text-base font-semibold text-slate-100">{localProvider.name}</h4>
            <p className="mt-0.5 text-xs text-slate-500">{localProvider.models.length} 个模型</p>
          </div>
        </div>
        <div className="flex items-center gap-1">
          {!isDefault && (
            <button
              type="button"
              onClick={() => onPrioritize(provider.id)}
              className="flex min-h-11 items-center gap-1.5 rounded-lg px-3 text-xs text-slate-400 hover:bg-indigo-500/10 hover:text-indigo-300"
            >
              <ArrowUp size={14} aria-hidden="true" />
              设为首位
            </button>
          )}
          <button
            type="button"
            onClick={handleSave}
            disabled={!edited || saving}
            aria-label="保存供应商配置"
            className="flex min-h-11 min-w-11 items-center justify-center rounded-lg text-green-400 hover:bg-green-500/10 disabled:cursor-not-allowed disabled:text-slate-600"
          >
            {saving ? <RefreshCw size={16} className="animate-spin motion-reduce:animate-none" /> : <Save size={16} />}
          </button>
          <button
            type="button"
            onClick={() => onDelete(provider.id)}
            aria-label="删除供应商"
            className="flex min-h-11 min-w-11 items-center justify-center rounded-lg text-slate-400 hover:bg-red-500/10 hover:text-red-400"
          >
            <Trash2 size={16} />
          </button>
        </div>
      </div>

      {expanded && (
        <div className="mt-4 space-y-4 border-t border-slate-700/70 pt-4">
          <div>
            <label htmlFor={`provider-${provider.id}-api-key`} className="mb-1 block text-sm text-slate-300">API Key</label>
            <div className="relative">
              <input
                id={`provider-${provider.id}-api-key`}
                type={showApiKey ? "text" : "password"}
                value={localProvider.api_key}
                onChange={(event) => {
                  setLocalProvider({ ...localProvider, api_key: event.target.value });
                  setEdited(true);
                }}
                placeholder="输入 API Key"
                className="min-h-11 w-full rounded-lg border border-slate-600 bg-slate-800 px-3 pr-12 text-sm text-slate-200 outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/20"
              />
              <button
                type="button"
                onClick={() => setShowApiKey(!showApiKey)}
                aria-label={showApiKey ? "隐藏 API Key" : "显示 API Key"}
                className="absolute right-1 top-0 flex min-h-11 min-w-11 items-center justify-center text-slate-400 hover:text-slate-200"
              >
                {showApiKey ? <EyeOff size={16} /> : <Eye size={16} />}
              </button>
            </div>
          </div>

          <div>
            <div className="mb-2 flex items-end justify-between gap-4">
              <div>
                <h5 className="text-sm font-medium text-slate-200">模型列表</h5>
                <p className="mt-0.5 text-xs text-slate-500">
                  {isDefault
                    ? "第一个会成为 Main 的默认模型，可拖动排序"
                    : "第一个是该供应商默认模型；设为首位后供 Main 使用"}
                </p>
              </div>
              <button
                type="button"
                onClick={discoverModels}
                disabled={discovering}
                className="flex min-h-10 items-center gap-1.5 rounded-lg px-3 text-xs text-indigo-300 hover:bg-indigo-500/10 disabled:opacity-50"
              >
                <RefreshCw size={14} className={discovering ? "animate-spin motion-reduce:animate-none" : ""} />
                {discovering ? "拉取中" : "拉取模型"}
              </button>
            </div>

            <ModelListEditor
              models={localProvider.models}
              onChange={(models) => {
                setLocalProvider({ ...localProvider, models });
                setEdited(true);
              }}
              inputLabel={`为 ${localProvider.name} 输入模型`}
            />
            {discoverError && <p className="mt-1 text-xs text-amber-400" role="alert">{discoverError}</p>}
          </div>

          <div className="rounded-lg border border-slate-700/80 bg-slate-800/30">
            <button
              type="button"
              onClick={() => setApiAddressExpanded(!apiAddressExpanded)}
              aria-expanded={apiAddressExpanded}
              className="flex min-h-11 w-full items-center justify-between px-3 text-sm text-slate-300 hover:bg-slate-800/60"
            >
              API 地址
              {apiAddressExpanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
            </button>
            {apiAddressExpanded && (
              <div className="border-t border-slate-700/80 p-3">
                <input
                  value={localProvider.base_url}
                  onChange={(event) => {
                    setLocalProvider({ ...localProvider, base_url: event.target.value });
                    setEdited(true);
                  }}
                  aria-label="API 地址"
                  className="min-h-11 w-full rounded-lg border border-slate-600 bg-slate-800 px-3 font-mono text-sm text-slate-200 outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/20"
                />
                <p className="mt-2 text-xs text-slate-500">
                  完整 API Base URL，包含版本路径（v1，不带末尾斜杠/）
                </p>
              </div>
            )}
          </div>

          <div className="space-y-3 border-t border-slate-700/70 pt-4">
            <h5 className="text-sm font-medium text-slate-200">模型参数</h5>
            <label className="flex items-center justify-between gap-4 text-sm text-slate-300">
              最大上下文 Tokens
              <input
                type="number"
                min={1000}
                step={1000}
                value={localProvider.maxContextTokens ?? 128000}
                onChange={(event) => {
                  setLocalProvider({
                    ...localProvider,
                    maxContextTokens: Number(event.target.value) || 128000,
                  });
                  setEdited(true);
                }}
                className="min-h-10 w-36 rounded-lg border border-slate-600 bg-slate-800 px-3 text-right font-mono text-sm text-slate-200 outline-none focus:border-indigo-500"
              />
            </label>
            {schema && Object.entries(schema.hyperparams).map(([key, param]) => {
                const value = localProvider.hyperparameter_values[key] ?? param.default;
                return (
                  <label key={key} className="flex items-center justify-between gap-4 text-sm text-slate-300">
                    {param.label}
                    <input
                      type="number"
                      min={param.min}
                      max={param.max}
                      value={value == null ? "" : Number(value)}
                      onChange={(event) => updateHyperparam(key, event.target.value === "" ? null : Number(event.target.value))}
                      className="min-h-10 w-36 rounded-lg border border-slate-600 bg-slate-800 px-3 text-right font-mono text-sm text-slate-200 outline-none focus:border-indigo-500"
                    />
                  </label>
                );
              })}
          </div>
        </div>
      )}
    </article>
  );
}
