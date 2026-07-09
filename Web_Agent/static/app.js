const state = {
  fileId: null,
  fileName: null,
  history: []
};

const els = {
  connectionStatus: document.getElementById("connectionStatus"),
  fileInput: document.getElementById("fileInput"),
  fileMeta: document.getElementById("fileMeta"),
  analysisBox: document.getElementById("analysisBox"),
  activeFileLabel: document.getElementById("activeFileLabel"),
  messages: document.getElementById("messages"),
  messageInput: document.getElementById("messageInput"),
  chatForm: document.getElementById("chatForm"),
  sendBtn: document.getElementById("sendBtn"),
  clearFileBtn: document.getElementById("clearFileBtn"),
  refreshGeneratedBtn: document.getElementById("refreshGeneratedBtn"),
  generatedList: document.getElementById("generatedList"),
  messageTemplate: document.getElementById("messageTemplate")
};

function setStatus(text) {
  els.connectionStatus.textContent = text;
}

function setBusy(isBusy) {
  document.body.classList.toggle("busy", isBusy);
  els.sendBtn.disabled = isBusy;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function addMessage(role, text, toolResults = []) {
  const node = els.messageTemplate.content.firstElementChild.cloneNode(true);
  node.classList.add(role);
  const bubble = node.querySelector(".bubble");
  bubble.innerHTML = escapeHtml(text || "");
  if (toolResults.length) {
    const details = document.createElement("details");
    details.className = "tool-result";
    const summary = document.createElement("summary");
    summary.textContent = `${toolResults.length} tool result${toolResults.length > 1 ? "s" : ""}`;
    const pre = document.createElement("pre");
    pre.textContent = JSON.stringify(toolResults, null, 2);
    details.append(summary, pre);
    bubble.appendChild(details);
  }
  els.messages.appendChild(node);
  els.messages.scrollTop = els.messages.scrollHeight;

  if (role === "user" || role === "assistant") {
    state.history.push({ role, content: text || "" });
    state.history = state.history.slice(-12);
  }
}

function renderAnalysis(analysis) {
  if (!analysis) {
    els.analysisBox.innerHTML = "";
    return;
  }
  const methods = (analysis.suggested_test_targets || [])
    .slice(0, 8)
    .map((method) => `<span class="method-pill">${escapeHtml(method.name || "method")}</span>`)
    .join("");
  els.analysisBox.innerHTML = `
    <div><strong>${escapeHtml(analysis.class_name || "Unknown")}</strong></div>
    <div>${escapeHtml(String(analysis.method_count || 0))} methods · ${escapeHtml(String(analysis.line_count || 0))} lines</div>
    <div>${methods || "<span class=\"method-pill\">No methods found</span>"}</div>
  `;
}

function setActiveFile(meta) {
  state.fileId = meta?.file_id || null;
  state.fileName = meta?.file_name || null;
  els.fileMeta.textContent = state.fileName ? `${state.fileName} · ${state.fileId}` : "No file selected";
  els.activeFileLabel.textContent = state.fileName ? `Active file: ${state.fileName}` : "Workspace mode";
  renderAnalysis(meta?.analysis || null);
}

async function uploadFile(file) {
  const formData = new FormData();
  formData.append("file", file);
  setBusy(true);
  setStatus("Uploading");
  try {
    const response = await fetch("/api/upload", { method: "POST", body: formData });
    const payload = await response.json();
    if (!payload.ok) throw new Error(payload.error || "Upload failed");
    setActiveFile(payload);
    addMessage("system", `Uploaded ${payload.file_name}.`);
    setStatus("Ready");
  } catch (error) {
    addMessage("system", `Upload failed: ${error.message}`);
    setStatus("Error");
  } finally {
    setBusy(false);
  }
}

async function sendMessage(text) {
  const message = text.trim();
  if (!message) return;
  addMessage("user", message);
  els.messageInput.value = "";
  setBusy(true);
  setStatus("Thinking");
  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message,
        file_id: state.fileId,
        history: state.history.filter((entry) => entry.role === "user" || entry.role === "assistant")
      })
    });
    const payload = await response.json();
    if (!payload.ok) throw new Error(payload.error || "Chat failed");
    addMessage("assistant", payload.reply || "", payload.tool_results || []);
    await refreshGenerated();
    setStatus(payload.planner === "llm" ? "LLM" : "Local");
  } catch (error) {
    addMessage("system", `Request failed: ${error.message}`);
    setStatus("Error");
  } finally {
    setBusy(false);
  }
}

async function callTool(tool, args = {}) {
  const response = await fetch("/api/tool", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tool, args })
  });
  const payload = await response.json();
  if (!payload.ok) throw new Error(payload.error || `${tool} failed`);
  return payload;
}

async function refreshGenerated() {
  try {
    const payload = await callTool("list_generated_tests");
    const files = payload.files || [];
    if (!files.length) {
      els.generatedList.innerHTML = "<div>No generated files</div>";
      return;
    }
    els.generatedList.innerHTML = files
      .slice()
      .reverse()
      .map((file) => {
        const href = `/generated/${encodeURIComponent(file.file_id)}/${encodeURIComponent(file.name)}`;
        return `
          <div class="generated-item">
            <a href="${href}" target="_blank" rel="noreferrer">${escapeHtml(file.name)}</a>
            <span>${escapeHtml(file.modified)} · ${escapeHtml(file.file_id)}</span>
          </div>
        `;
      })
      .join("");
  } catch (error) {
    els.generatedList.innerHTML = `<div>${escapeHtml(error.message)}</div>`;
  }
}

els.fileInput.addEventListener("change", (event) => {
  const file = event.target.files?.[0];
  if (file) uploadFile(file);
});

els.chatForm.addEventListener("submit", (event) => {
  event.preventDefault();
  sendMessage(els.messageInput.value);
});

els.messageInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
    event.preventDefault();
    sendMessage(els.messageInput.value);
  }
});

document.querySelectorAll("[data-prompt]").forEach((button) => {
  button.addEventListener("click", () => {
    sendMessage(button.getAttribute("data-prompt") || "");
  });
});

els.clearFileBtn.addEventListener("click", () => {
  els.fileInput.value = "";
  setActiveFile(null);
});

els.refreshGeneratedBtn.addEventListener("click", refreshGenerated);

addMessage(
  "assistant",
  "可以上传 Java 文件让我分析或生成 JUnit 4 测试，也可以直接问当前 A3 workspace 的覆盖率、失败分类和下一步策略。"
);
refreshGenerated();
