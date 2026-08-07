(() => {
  "use strict";

  if (!/^https?:\/\/(127\.0\.0\.1|localhost)(?::\d+)?$/.test(window.location.origin)) {
    return;
  }

  const handledErrors = new Set();
  let dialogRoot = null;
  let dialogClose = null;
  let dialogTitle = null;
  let dialogMessage = null;

  function ensureDialog() {
    if (dialogRoot) return;

    const style = document.createElement("style");
    style.textContent = `
      #determinflow-desktop-dialog {
        position: fixed;
        inset: 0;
        z-index: 2147483647;
        display: grid;
        place-items: center;
        padding: 1rem;
        background: rgba(0, 0, 0, 0.64);
      }
      #determinflow-desktop-dialog[hidden] { display: none; }
      #determinflow-desktop-dialog .df-dialog-card {
        width: min(32rem, calc(100vw - 2rem));
        max-height: min(80vh, 32rem);
        overflow: auto;
        border: 1px solid rgba(255, 255, 255, 0.16);
        border-radius: 0.6rem;
        padding: 1.25rem;
        color: #f3f4f6;
        background: #171a21;
        box-shadow: 0 1.5rem 4rem rgba(0, 0, 0, 0.36);
        font: 14px/1.5 Inter, "Segoe UI", "Microsoft YaHei", sans-serif;
      }
      #determinflow-desktop-dialog .df-dialog-heading {
        display: flex;
        align-items: center;
        gap: 0.7rem;
        margin: 0;
        font-size: 1rem;
        font-weight: 600;
      }
      #determinflow-desktop-dialog .df-dialog-icon {
        display: grid;
        width: 2rem;
        height: 2rem;
        flex: 0 0 auto;
        place-items: center;
        border-radius: 0.4rem;
        color: #fecaca;
        background: rgba(239, 68, 68, 0.2);
        font-size: 1.1rem;
      }
      #determinflow-desktop-dialog .df-dialog-body {
        margin: 0.85rem 0 1.1rem 2.7rem;
        color: #c2c7d0;
        white-space: pre-wrap;
        overflow-wrap: anywhere;
      }
      #determinflow-desktop-dialog .df-dialog-actions {
        display: flex;
        justify-content: flex-end;
      }
      #determinflow-desktop-dialog button {
        min-width: 4.5rem;
        border: 1px solid rgba(255, 255, 255, 0.18);
        border-radius: 0.4rem;
        padding: 0.45rem 0.8rem;
        color: #f3f4f6;
        background: rgba(255, 255, 255, 0.08);
        cursor: pointer;
      }
      #determinflow-desktop-dialog button:hover { background: rgba(255, 255, 255, 0.14); }
      #determinflow-desktop-dialog button:focus-visible {
        outline: 2px solid #93c5fd;
        outline-offset: 2px;
      }
    `;
    document.head.appendChild(style);

    dialogRoot = document.createElement("div");
    dialogRoot.id = "determinflow-desktop-dialog";
    dialogRoot.hidden = true;
    dialogRoot.setAttribute("role", "presentation");
    dialogRoot.innerHTML = `
      <section class="df-dialog-card" role="alertdialog" aria-modal="true" aria-labelledby="df-dialog-title" aria-describedby="df-dialog-message">
        <h2 class="df-dialog-heading">
          <span class="df-dialog-icon" aria-hidden="true">!</span>
          <span id="df-dialog-title"></span>
        </h2>
        <div class="df-dialog-body" id="df-dialog-message"></div>
        <div class="df-dialog-actions"><button type="button">关闭</button></div>
      </section>
    `;
    document.body.appendChild(dialogRoot);
    dialogTitle = dialogRoot.querySelector("#df-dialog-title");
    dialogMessage = dialogRoot.querySelector("#df-dialog-message");
    dialogClose = dialogRoot.querySelector("button");
    dialogClose.addEventListener("click", () => { dialogRoot.hidden = true; });
    dialogRoot.addEventListener("click", (event) => {
      if (event.target === dialogRoot) dialogRoot.hidden = true;
    });
  }

  function showDialog({ title, message }) {
    ensureDialog();
    dialogTitle.textContent = title || "DeterminFlow";
    dialogMessage.textContent = message || "发生未知错误";
    dialogRoot.hidden = false;
    dialogClose.focus();
  }

  window.DeterminFlowDesktopDialog = Object.freeze({
    show: showDialog,
    close: () => { if (dialogRoot) dialogRoot.hidden = true; },
  });

  function displayRuntimeError(payload) {
    const message = typeof payload.message === "string" ? payload.message : payload.error;
    if (typeof message !== "string" || !message.trim()) return;
    if (payload.terminal === false) return;
    const sessionId = typeof payload.session_id === "string" ? payload.session_id : "main";
    const key = `${sessionId}:${message}`;
    if (handledErrors.has(key)) return;
    handledErrors.add(key);
    const viewingSessionId = new URLSearchParams(window.location.search).get("session_id");
    showDialog({
      title: viewingSessionId ? "会话运行失败" : "Main 会话运行失败",
      message: message.trim(),
    });
  }

  const NativeWebSocket = window.WebSocket;
  if (typeof NativeWebSocket === "function") {
    class DesktopWebSocket extends NativeWebSocket {
      constructor(url, protocols) {
        if (protocols === undefined) super(url);
        else super(url, protocols);
        this.addEventListener("message", (event) => {
          if (typeof event.data !== "string") return;
          try {
            const payload = JSON.parse(event.data);
            if (payload && typeof payload === "object") {
              if (payload.type === "stream_start" && typeof payload.session_id === "string") {
                for (const key of handledErrors) {
                  if (key.startsWith(`${payload.session_id}:`)) handledErrors.delete(key);
                }
              }
              if (payload.type === "error" || (payload.type === "snapshot" && payload.status === "error")) {
                displayRuntimeError(payload);
              }
            }
          } catch {
            // Ignore non-JSON WebSocket messages from unrelated channels.
          }
        });
      }
    }
    for (const name of ["CONNECTING", "OPEN", "CLOSING", "CLOSED"]) {
      Object.defineProperty(DesktopWebSocket, name, { value: NativeWebSocket[name] });
    }
    window.WebSocket = DesktopWebSocket;
  }

  const nativeFetch = window.fetch.bind(window);

  async function responseError(response) {
    const fallback = `HTTP ${response.status}`;
    try {
      const payload = await response.clone().json();
      if (typeof payload.detail === "string") return payload.detail;
      if (typeof payload.message === "string") return payload.message;
    } catch {
      // Fall back to the response status for non-JSON errors.
    }
    return fallback;
  }

  async function probeProvider(providerId, baseUrl, apiKey) {
    const response = await nativeFetch("/api/model-providers/models/discover", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider_id: providerId, base_url: baseUrl, api_key: apiKey }),
    });
    if (!response.ok) throw new Error(await responseError(response));
  }

  async function currentProvider(providerId) {
    const response = await nativeFetch("/api/model-providers");
    if (!response.ok) throw new Error(await responseError(response));
    const payload = await response.json();
    const provider = payload.providers && payload.providers[providerId];
    if (!provider || typeof provider !== "object") throw new Error("无法读取当前供应商配置");
    return provider;
  }

  function providerSaveRequest(input, init) {
    const requestUrl = typeof input === "string" ? input : input && input.url;
    if (typeof requestUrl !== "string") return null;
    const url = new URL(requestUrl, window.location.href);
    if (url.pathname === "/api/model-providers" && (init?.method || "GET").toUpperCase() === "POST") {
      return { providerId: null, body: init?.body };
    }
    const match = url.pathname.match(/^\/api\/model-providers\/([^/]+)$/);
    if (!match || (init?.method || "GET").toUpperCase() !== "PUT") return null;
    return { providerId: decodeURIComponent(match[1]), body: init?.body };
  }

  window.fetch = async (input, init = {}) => {
    const request = providerSaveRequest(input, init);
    if (request && typeof request.body === "string") {
      let payload;
      try { payload = JSON.parse(request.body); } catch { payload = null; }
      const apiKey = typeof payload?.api_key === "string" ? payload.api_key.trim() : "";
      if (apiKey && apiKey !== "***") {
        try {
          const provider = request.providerId && !(typeof payload.base_url === "string" && payload.base_url.trim())
            ? await currentProvider(request.providerId)
            : null;
          const providerId = request.providerId || payload.provider_id;
          const baseUrl = typeof payload.base_url === "string" && payload.base_url.trim()
            ? payload.base_url.trim()
            : provider?.base_url;
          if (!providerId || !baseUrl) throw new Error("缺少模型供应商 API 地址");
          await probeProvider(providerId, baseUrl, apiKey);
        } catch (error) {
          const message = error instanceof Error ? error.message : "无法连接模型供应商";
          showDialog({ title: "连接检测失败", message });
          throw error instanceof Error ? error : new Error(message);
        }
      }
    }
    return nativeFetch(input, init);
  };
})();
