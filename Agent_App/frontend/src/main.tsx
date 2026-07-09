import React, { DragEvent, FormEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  BarChart3,
  Bot,
  Download,
  Eye,
  FileArchive,
  FileCode2,
  History,
  LogOut,
  PackageOpen,
  Plus,
  RefreshCw,
  Send,
  ThumbsDown,
  ThumbsUp,
  Trash2,
  Upload,
  UserRound
} from "lucide-react";
import {
  Artifact,
  BatchGenerateResult,
  Conversation,
  Message,
  UploadedFile,
  createConversation,
  deleteConversation,
  deleteUploadedFile,
  deleteUploadedFiles,
  downloadArtifact,
  downloadArtifactsZip,
  generateTestsBatch,
  getArtifacts,
  getConversations,
  getFiles,
  getMessages,
  inspectWorkspace,
  login,
  logout,
  rateMessage,
  readArtifact,
  register,
  streamChat,
  uploadJavaBatch
} from "./api";
import "./styles.css";

const text = {
  signIn: "\u767b\u5f55\u540e\u7ee7\u7eed",
  displayName: "\u6635\u79f0",
  email: "\u90ae\u7bb1",
  password: "\u5bc6\u7801",
  login: "\u767b\u5f55",
  createAccount: "\u521b\u5efa\u8d26\u53f7",
  useExisting: "\u4f7f\u7528\u5df2\u6709\u8d26\u53f7",
  newChat: "\u65b0\u5bf9\u8bdd",
  history: "\u5386\u53f2\u5bf9\u8bdd",
  title: "Java \u6d4b\u8bd5\u751f\u6210 Agent",
  noFile: "\u4e0a\u4f20 Java \u6587\u4ef6\uff0c\u6216\u68c0\u67e5 A3 \u5de5\u4f5c\u533a",
  activeFile: "\u5f53\u524d\u6587\u4ef6\uff1a",
  inspect: "\u68c0\u67e5",
  generate: "\u751f\u6210\u6d4b\u8bd5",
  coverage: "\u8986\u76d6\u7387",
  toolResults: "\u4e2a\u5de5\u5177\u7ed3\u679c",
  helpful: "\u6709\u5e2e\u52a9",
  notHelpful: "\u6ca1\u6709\u5e2e\u52a9",
  placeholder: "\u8be2\u95ee\u6d4b\u8bd5\u751f\u6210\u3001\u4fee\u590d\u3001\u7f16\u8bd1\u5931\u8d25\u6216\u8986\u76d6\u7387...",
  tabHint: "\u6309 Tab \u63d2\u5165\u5efa\u8bae\u95ee\u9898",
  dropTitle: "\u62d6\u62fd Java \u6587\u4ef6\u6216 zip \u5305",
  dropHint: "支持单个 .java、多个 .java，或完整项目 .zip。项目请先压缩成 zip 再上传。",
  files: "\u6587\u4ef6",
  javaFiles: "Java \u6587\u4ef6",
  deleteFile: "\u5220\u5f53\u524d",
  deleteSelectedFiles: "\u6279\u91cf\u5220",
  selectAll: "\u5168\u9009",
  batchGenerateMissing: "\u751f\u6210\u672a\u6d4b",
  deleteConversation: "\u5220\u9664\u5bf9\u8bdd",
  methods: "\u4e2a\u65b9\u6cd5",
  artifacts: "\u751f\u6210\u4ea7\u7269",
  zipCurrent: "\u6253\u5305\u5f53\u524d",
  zipAll: "\u6253\u5305\u5168\u90e8",
  preview: "\u6d4b\u8bd5\u9884\u89c8",
  download: "\u4e0b\u8f7d",
  close: "\u5173\u95ed",
  loading: "\u52a0\u8f7d\u4e2d...",
  noArtifact: "\u5c1a\u672a\u9009\u62e9\u4ea7\u7269",
  snapshot: "\u5de5\u4f5c\u533a\u5feb\u7167",
  notLoaded: "\u5c1a\u672a\u52a0\u8f7d"
};

const suggestions = [
  "\u4e3a\u5f53\u524d Java \u6587\u4ef6\u751f\u6210 JUnit 4 \u6d4b\u8bd5",
  "\u770b\u770b\u4e4b\u524d\u751f\u6210\u7684\u6d4b\u8bd5\u4e3a\u4ec0\u4e48\u7f16\u8bd1\u4e0d\u8fc7",
  "\u4fee\u590d\u5f53\u524d\u6587\u4ef6\u6700\u65b0\u751f\u6210\u7684\u6d4b\u8bd5",
  "\u8fd0\u884c\u5f53\u524d\u6587\u4ef6\u6700\u65b0\u6d4b\u8bd5\u7684 JaCoCo \u8986\u76d6\u7387",
  "\u8bb0\u4f4f\uff1a\u8fd9\u4e2a\u9879\u76ee\u4f18\u5148\u4f7f\u7528 JUnit 4\uff0c\u4e0d\u4f7f\u7528 Mockito"
];

type TaskModalState = {
  open: boolean;
  running: boolean;
  title: string;
  detail: string;
  progress: number;
  files: UploadedFile[];
  rejected: Array<{ name: string; reason: string }>;
  result?: BatchGenerateResult;
};

type FileGroup = {
  id: string;
  name: string;
  buildTool?: string;
  files: UploadedFile[];
};

const emptyTask: TaskModalState = {
  open: false,
  running: false,
  title: "",
  detail: "",
  progress: 0,
  files: [],
  rejected: []
};

function AuthGate({ onAuthed }: { onAuthed: () => void }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    try {
      if (mode === "register") await register(email, password, displayName);
      await login(email, password);
      onAuthed();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Authentication failed");
    }
  }

  return (
    <main className="auth-screen">
      <form className="auth-card" onSubmit={submit}>
        <div className="auth-brand">
          <div className="logo-box">A3</div>
          <div>
            <h1>Agent Workspace</h1>
            <p>{text.signIn}</p>
          </div>
        </div>
        {mode === "register" && (
          <label>
            {text.displayName}
            <input value={displayName} onChange={(event) => setDisplayName(event.target.value)} />
          </label>
        )}
        <label>
          {text.email}
          <input value={email} onChange={(event) => setEmail(event.target.value)} type="email" required />
        </label>
        <label>
          {text.password}
          <input value={password} onChange={(event) => setPassword(event.target.value)} type="password" required />
        </label>
        {error && <div className="error-line">{error}</div>}
        <button className="primary-btn" type="submit">
          <UserRound size={17} />
          {mode === "login" ? text.login : text.createAccount}
        </button>
        <button className="text-btn" type="button" onClick={() => setMode(mode === "login" ? "register" : "login")}>
          {mode === "login" ? text.createAccount : text.useExisting}
        </button>
      </form>
    </main>
  );
}

function App() {
  const [authed, setAuthed] = useState(Boolean(localStorage.getItem("a3_agent_token")));
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [conversationId, setConversationId] = useState<string | undefined>();
  const [messages, setMessages] = useState<Message[]>([]);
  const [files, setFiles] = useState<UploadedFile[]>([]);
  const [activeFileId, setActiveFileId] = useState<string | undefined>();
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [selectedArtifact, setSelectedArtifact] = useState<Artifact | undefined>();
  const [artifactCode, setArtifactCode] = useState("");
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [dropActive, setDropActive] = useState(false);
  const [status, setStatus] = useState("");
  const [workspaceSummary, setWorkspaceSummary] = useState<Record<string, unknown> | null>(null);
  const [suggestionIndex, setSuggestionIndex] = useState(0);
  const [typedSuggestion, setTypedSuggestion] = useState("");
  const [selectedFileIds, setSelectedFileIds] = useState<string[]>([]);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [taskModal, setTaskModal] = useState<TaskModalState>(emptyTask);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);

  const activeFile = useMemo(() => files.find((file) => file.id === activeFileId), [files, activeFileId]);
  const activeProjectId = activeFile?.analysis._project_id;
  const activeProjectFiles = useMemo(
    () => (activeProjectId ? files.filter((file) => file.analysis._project_id === activeProjectId) : []),
    [files, activeProjectId]
  );
  const fileGroups = useMemo<FileGroup[]>(() => {
    const groups = new Map<string, FileGroup>();
    for (const file of files) {
      const projectId = file.analysis._project_id || "loose";
      const group = groups.get(projectId) || {
        id: projectId,
        name: file.analysis._project_name || (projectId === "loose" ? "散文件" : "项目文件"),
        buildTool: file.analysis._project_build_tool,
        files: []
      };
      group.files.push(file);
      groups.set(projectId, group);
    }
    return Array.from(groups.values());
  }, [files]);
  const artifactName = (artifact: Artifact) => artifact.storage_path.split(/[\\/]/).pop() || "GeneratedTest.java";
  const mergeFilesById = (incoming: UploadedFile[], current: UploadedFile[]) => [
    ...incoming,
    ...current.filter((file) => !incoming.some((next) => next.id === file.id))
  ];

  async function refresh() {
    const [conversationRows, fileRows] = await Promise.all([getConversations(), getFiles()]);
    setConversations(conversationRows);
    setFiles(fileRows);
    if (!conversationId && conversationRows[0]) setConversationId(conversationRows[0].id);
    if (!activeFileId && fileRows[0]) setActiveFileId(fileRows[0].id);
  }

  useEffect(() => {
    if (authed) refresh().catch(console.error);
  }, [authed]);

  useEffect(() => {
    if (!conversationId) {
      setMessages([]);
      return;
    }
    if (busy) return;
    getMessages(conversationId).then(setMessages).catch(console.error);
  }, [conversationId, busy]);

  useEffect(() => {
    if (!activeFileId) {
      setArtifacts([]);
      setSelectedArtifact(undefined);
      setArtifactCode("");
      setPreviewOpen(false);
      return;
    }
    getArtifacts(activeFileId)
      .then((rows) => {
        setArtifacts(rows);
        setSelectedArtifact(rows[0]);
      })
      .catch(console.error);
  }, [activeFileId]);

  useEffect(() => {
    if (!selectedArtifact) {
      setArtifactCode("");
      return;
    }
    readArtifact(selectedArtifact).then((payload) => setArtifactCode(payload.code)).catch(console.error);
  }, [selectedArtifact?.id]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, status]);

  useEffect(() => {
    if (!taskModal.open || !taskModal.running) return;
    const timer = window.setInterval(() => {
      setTaskModal((current) =>
        current.open && current.running
          ? { ...current, progress: Math.min(92, current.progress + (current.progress < 45 ? 8 : 3)) }
          : current
      );
    }, 650);
    return () => window.clearInterval(timer);
  }, [taskModal.open, taskModal.running]);

  useEffect(() => {
    setTypedSuggestion("");
    let index = 0;
    const full = suggestions[suggestionIndex];
    const timer = window.setInterval(() => {
      index += 1;
      setTypedSuggestion(full.slice(0, index));
      if (index >= full.length) {
        window.clearInterval(timer);
        window.setTimeout(() => setSuggestionIndex((current) => (current + 1) % suggestions.length), 1800);
      }
    }, 34);
    return () => window.clearInterval(timer);
  }, [suggestionIndex]);

  async function startNewConversation() {
    const conversation = await createConversation(text.newChat);
    setConversationId(conversation.id);
    setMessages([]);
    await refresh();
  }

  async function removeConversation(id: string) {
    if (busy) return;
    setBusy(true);
    try {
      await deleteConversation(id);
      const nextConversations = await getConversations();
      setConversations(nextConversations);
      if (conversationId === id) {
        setConversationId(nextConversations[0]?.id);
        if (!nextConversations[0]) setMessages([]);
      }
    } finally {
      setBusy(false);
    }
  }

  function toggleFileSelection(fileId: string) {
    setSelectedFileIds((current) =>
      current.includes(fileId) ? current.filter((id) => id !== fileId) : [...current, fileId]
    );
  }

  function toggleAllFiles() {
    setSelectedFileIds((current) => (current.length === files.length ? [] : files.map((file) => file.id)));
  }

  async function deleteActiveFile() {
    if (!activeFileId || busy) return;
    setBusy(true);
    try {
      await deleteUploadedFile(activeFileId);
      const nextFiles = await getFiles();
      setFiles(nextFiles);
      setActiveFileId(nextFiles[0]?.id);
      setSelectedFileIds((current) => current.filter((id) => id !== activeFileId));
      setArtifacts([]);
      setSelectedArtifact(undefined);
      setArtifactCode("");
    } finally {
      setBusy(false);
    }
  }

  async function deleteSelectedFiles() {
    if (!selectedFileIds.length || busy) return;
    setBusy(true);
    try {
      await deleteUploadedFiles(selectedFileIds);
      const nextFiles = await getFiles();
      setFiles(nextFiles);
      setSelectedFileIds([]);
      if (activeFileId && selectedFileIds.includes(activeFileId)) {
        setActiveFileId(nextFiles[0]?.id);
        setArtifacts([]);
        setSelectedArtifact(undefined);
        setArtifactCode("");
      }
    } finally {
      setBusy(false);
    }
  }

  async function generateForFiles(fileIds: string[], title: string, onlyMissing = true) {
    if (busy || !fileIds.length) return;
    setBusy(true);
    setStatus(title);
    setTaskModal({
      open: true,
      running: true,
      title,
      detail: `准备处理 ${fileIds.length} 个 Java 文件`,
      progress: 8,
      files: files.filter((file) => fileIds.includes(file.id)),
      rejected: []
    });
    try {
      const result = await generateTestsBatch(fileIds, onlyMissing);
      const generated = Number(result.generated_count || 0);
      const skipped = Number(result.skipped_count || 0);
      const failed = Number(result.failed_count || 0);
      setTaskModal((current) => ({
        ...current,
        running: false,
        progress: 100,
        detail: `完成：生成 ${generated} 个，跳过 ${skipped} 个，失败 ${failed} 个`,
        result
      }));
      setMessages((current) => [
        ...current,
        {
          id: `local-batch-${Date.now()}`,
          role: "assistant",
          content: `已完成批量生成：生成 ${generated} 个，跳过 ${skipped} 个，失败 ${failed} 个。`,
          tool_results: { items: [result] },
          created_at: new Date().toISOString()
        }
      ]);
      await Promise.all([refresh(), activeFileId ? getArtifacts(activeFileId).then(setArtifacts) : Promise.resolve()]);
    } catch (err) {
      setTaskModal((current) => ({
        ...current,
        running: false,
        progress: 100,
        detail: err instanceof Error ? err.message : "批量生成失败"
      }));
    } finally {
      setBusy(false);
      setStatus("");
    }
  }

  async function batchGenerateMissingTests() {
    const targetIds = selectedFileIds.length ? selectedFileIds : files.map((file) => file.id);
    const label = selectedFileIds.length ? "为已选 Java 生成未测测试" : "为全部 Java 生成未测测试";
    await generateForFiles(targetIds, label, true);
  }

  async function submitChat(event?: FormEvent, preset?: string) {
    event?.preventDefault();
    const message = (preset ?? input).trim();
    if (!message || busy) return;
    setInput("");
    setBusy(true);
    setStatus("Thinking");
    const userMessage: Message = {
      id: `local-user-${Date.now()}`,
      role: "user",
      content: message,
      created_at: new Date().toISOString()
    };
    const assistantId = `local-assistant-${Date.now()}`;
    const assistantMessage: Message = {
      id: assistantId,
      role: "assistant",
      content: "",
      tool_results: { items: [] },
      created_at: new Date().toISOString()
    };
    setMessages((current) => [...current, userMessage, assistantMessage]);
    try {
      await streamChat(message, conversationId, activeFileId, {
        onMeta: (payload) => {
          if (typeof payload.conversation_id === "string") setConversationId(payload.conversation_id);
        },
        onStatus: (payload) => setStatus(String(payload.message || "")),
        onTool: (payload) => {
          setMessages((current) =>
            current.map((item) =>
              item.id === assistantId
                ? { ...item, tool_results: { items: [...(item.tool_results?.items || []), payload] } }
                : item
            )
          );
        },
        onDelta: (chunk) => {
          setMessages((current) =>
            current.map((item) => (item.id === assistantId ? { ...item, content: item.content + chunk } : item))
          );
        },
        onDone: async (payload) => {
          if (typeof payload.assistant_message_id === "string") {
            setMessages((current) =>
              current.map((item) => (item.id === assistantId ? { ...item, id: payload.assistant_message_id as string } : item))
            );
          }
          const id = String(payload.conversation_id || conversationId || "");
          if (id) await getMessages(id).then(setMessages);
          await Promise.all([refresh(), activeFileId ? getArtifacts(activeFileId).then(setArtifacts) : Promise.resolve()]);
        },
        onError: (payload) => {
          setMessages((current) =>
            current.map((item) =>
              item.id === assistantId ? { ...item, content: String(payload.detail || "Stream error") } : item
            )
          );
        }
      });
    } finally {
      setBusy(false);
      setStatus("");
    }
  }

  async function handleFiles(nextFiles: File[]) {
    const accepted = nextFiles.filter((file) => file.name.endsWith(".java") || file.name.endsWith(".zip"));
    if (!accepted.length) return;
    setBusy(true);
    const hasZip = accepted.some((file) => file.name.endsWith(".zip"));
    setTaskModal({
      open: true,
      running: true,
      title: hasZip ? "上传并解析项目 zip" : "上传 Java 文件",
      detail: `正在上传 ${accepted.length} 个文件`,
      progress: 8,
      files: [],
      rejected: []
    });
    try {
      const payload = await uploadJavaBatch(accepted);
      setFiles((current) => mergeFilesById(payload.files, current));
      if (payload.files[0]) setActiveFileId(payload.files[0].id);
      setSelectedFileIds(payload.files.map((file) => file.id));
      setArtifacts([]);
      setTaskModal((current) => ({
        ...current,
        running: false,
        progress: 100,
        detail: `解析完成：发现 ${payload.files.length} 个 Java 文件，拒绝 ${payload.rejected.length} 项`,
        files: payload.files,
        rejected: payload.rejected
      }));
    } catch (err) {
      setTaskModal((current) => ({
        ...current,
        running: false,
        progress: 100,
        detail: err instanceof Error ? err.message : "上传失败"
      }));
    } finally {
      setBusy(false);
      setDropActive(false);
    }
  }

  function drop(event: DragEvent<HTMLElement>) {
    event.preventDefault();
    setDropActive(false);
    handleFiles(Array.from(event.dataTransfer.files)).catch(console.error);
  }

  function keyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Tab" && typedSuggestion) {
      event.preventDefault();
      setInput(typedSuggestion);
    }
  }

  async function loadWorkspace() {
    setWorkspaceSummary(await inspectWorkspace());
  }

  function openPreview(artifact: Artifact) {
    setSelectedArtifact(artifact);
    setPreviewOpen(true);
  }

  if (!authed) return <AuthGate onAuthed={() => setAuthed(true)} />;

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="topbar">
          <div className="brand-line">
            <div className="logo-box">A3</div>
            <strong>Agent</strong>
          </div>
          <button
            className="icon-btn"
            title="Logout"
            onClick={() => {
              logout();
              setAuthed(false);
            }}
          >
            <LogOut size={18} />
          </button>
        </div>

        <button className="new-chat-btn" type="button" onClick={startNewConversation}>
          <Plus size={17} />
          {text.newChat}
        </button>

        <section className="nav-section">
          <div className="section-title">
            <History size={16} />
            {text.history}
          </div>
          <div className="scroll-list">
            {conversations.map((conversation) => (
              <div className="conversation-row" key={conversation.id}>
                <button
                  className={`list-item ${conversation.id === conversationId ? "selected" : ""}`}
                  onClick={() => setConversationId(conversation.id)}
                >
                  <strong>{conversation.title}</strong>
                  <span>{new Date(conversation.updated_at).toLocaleString()}</span>
                </button>
                <button
                  className="mini-icon-btn"
                  type="button"
                  title={text.deleteConversation}
                  onClick={() => removeConversation(conversation.id).catch(console.error)}
                >
                  <Trash2 size={15} />
                </button>
              </div>
            ))}
          </div>
        </section>
      </aside>

      <section className="chat-workspace">
        <header className="workspace-header">
          <div>
            <h1>{text.title}</h1>
            <p>{activeFile ? `${text.activeFile}${activeFile.original_name}` : text.noFile}</p>
          </div>
          <div className="header-actions">
            <button type="button" onClick={loadWorkspace}>
              <RefreshCw size={17} />
              {text.inspect}
            </button>
            <button type="button" onClick={() => submitChat(undefined, suggestions[0])} disabled={!activeFileId || busy}>
              <Bot size={17} />
              {text.generate}
            </button>
            <button type="button" onClick={() => submitChat(undefined, suggestions[3])} disabled={!activeFileId || busy}>
              <BarChart3 size={17} />
              {text.coverage}
            </button>
          </div>
        </header>

        <div className="main-grid">
          <section className="conversation-surface">
            <div className="messages">
              {messages.map((message) => (
                <article className={`message ${message.role}`} key={message.id}>
                  <div className="avatar">{message.role === "user" ? "U" : "A"}</div>
                  <div className="message-body">
                    <pre>{message.content || (message.role === "assistant" && busy ? status || "Thinking" : "")}</pre>
                    {message.tool_results?.items?.length ? (
                      <details>
                        <summary>{message.tool_results.items.length} {text.toolResults}</summary>
                        <code>{JSON.stringify(message.tool_results.items, null, 2)}</code>
                      </details>
                    ) : null}
                    {message.role === "assistant" && !message.id.startsWith("local-") && (
                      <div className="feedback-row">
                        <button type="button" title={text.helpful} onClick={() => rateMessage(message.id, "up").catch(console.error)}>
                          <ThumbsUp size={15} />
                        </button>
                        <button type="button" title={text.notHelpful} onClick={() => rateMessage(message.id, "down").catch(console.error)}>
                          <ThumbsDown size={15} />
                        </button>
                      </div>
                    )}
                  </div>
                </article>
              ))}
              <div ref={messagesEndRef} />
            </div>

            <form className="composer" onSubmit={submitChat}>
              <textarea
                value={input}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={keyDown}
                placeholder={typedSuggestion || text.placeholder}
              />
              <div className="composer-footer">
                <span>{text.tabHint}</span>
                <button className="send-btn" disabled={busy} type="submit">
                  <Send size={18} />
                </button>
              </div>
            </form>
          </section>

          <aside className="right-rail">
            <section
              className={`drop-zone ${dropActive ? "active" : ""}`}
              onDragOver={(event) => {
                event.preventDefault();
                setDropActive(true);
              }}
              onDragLeave={() => setDropActive(false)}
              onDrop={drop}
            >
              <Upload size={20} />
              <strong>{text.dropTitle}</strong>
              <span>{text.dropHint}</span>
              <div className="upload-actions">
                <label>
                  <FileCode2 size={15} />
                  {text.files}
                  <input type="file" accept=".java,.zip" multiple onChange={(event) => handleFiles(Array.from(event.target.files || [])).catch(console.error)} />
                </label>
              </div>
            </section>

            <section className="side-section">
              <div className="section-title">
                <FileCode2 size={16} />
                {text.javaFiles}
              </div>
              <div className="artifact-actions compact-actions">
                <button type="button" title={text.selectAll} onClick={toggleAllFiles} disabled={!files.length || busy}>
                  {text.selectAll}
                </button>
                <button type="button" title={text.deleteSelectedFiles} onClick={deleteSelectedFiles} disabled={!selectedFileIds.length || busy}>
                  <Trash2 size={15} />
                  {text.deleteSelectedFiles}
                </button>
                <button type="button" title={text.deleteFile} onClick={deleteActiveFile} disabled={!activeFileId || busy}>
                  <Trash2 size={15} />
                  {text.deleteFile}
                </button>
                {activeProjectFiles.length > 1 && (
                  <button
                    type="button"
                    title="为当前项目中尚未生成测试的 Java 文件生成测试"
                    onClick={() => generateForFiles(activeProjectFiles.map((file) => file.id), "为当前项目生成未测测试", true)}
                    disabled={busy}
                  >
                    <PackageOpen size={15} />
                    当前项目
                  </button>
                )}
                <button type="button" title="为已选或全部未生成测试的 Java 文件生成测试" onClick={batchGenerateMissingTests} disabled={!files.length || busy}>
                  <Bot size={15} />
                  {text.batchGenerateMissing}
                </button>
              </div>
              <div className="scroll-list files-list">
                {fileGroups.map((group) => (
                  <div className="project-group" key={group.id}>
                    <div className="project-header">
                      <div>
                        <strong>{group.name}</strong>
                        <span>{group.files.length} 个 Java{group.buildTool ? ` · ${group.buildTool}` : ""}</span>
                      </div>
                      {group.id !== "loose" && (
                        <button
                          className="mini-text-btn"
                          type="button"
                          onClick={() => generateForFiles(group.files.map((file) => file.id), `为项目 ${group.name} 生成未测测试`, true)}
                          disabled={busy}
                        >
                          生成项目未测
                        </button>
                      )}
                    </div>
                    {group.files.map((file) => (
                      <div className={`file-row ${file.id === activeFileId ? "selected" : ""}`} key={file.id}>
                        <input
                          aria-label={file.original_name}
                          checked={selectedFileIds.includes(file.id)}
                          onChange={() => toggleFileSelection(file.id)}
                          type="checkbox"
                        />
                        <button className="list-item" onClick={() => setActiveFileId(file.id)}>
                          <strong>{file.analysis._project_relative_path || file.original_name}</strong>
                          <span>{file.analysis.class_name || "Unknown"} - {file.analysis.method_count || 0} {text.methods}</span>
                        </button>
                        <button
                          className="mini-icon-btn"
                          type="button"
                          title="为这个 Java 文件生成测试"
                          onClick={() => generateForFiles([file.id], `为 ${file.original_name} 生成测试`, false)}
                          disabled={busy}
                        >
                          <Bot size={15} />
                        </button>
                      </div>
                    ))}
                  </div>
                ))}
              </div>
            </section>

            <section className="side-section">
              <div className="section-title">
                <PackageOpen size={16} />
                {text.artifacts}
              </div>
              <div className="artifact-actions compact-actions">
                <button type="button" onClick={() => activeFileId && downloadArtifactsZip(activeFileId)} disabled={!activeFileId}>
                  <FileArchive size={15} />
                  {text.zipCurrent}
                </button>
                <button type="button" onClick={() => downloadArtifactsZip()}>
                  <FileArchive size={15} />
                  {text.zipAll}
                </button>
              </div>
              <div className="scroll-list artifact-list">
                {artifacts.map((artifact) => (
                  <div className={`artifact-row ${artifact.id === selectedArtifact?.id ? "selected" : ""}`} key={artifact.id}>
                    <button className="list-item artifact-main" type="button" onClick={() => setSelectedArtifact(artifact)}>
                      <strong>{artifact.kind}</strong>
                      <span>{artifactName(artifact)}</span>
                    </button>
                    <button className="mini-icon-btn" type="button" title={text.preview} onClick={() => openPreview(artifact)}>
                      <Eye size={15} />
                    </button>
                    <button className="mini-icon-btn" type="button" title={text.download} onClick={() => downloadArtifact(artifact).catch(console.error)}>
                      <Download size={15} />
                    </button>
                  </div>
                ))}
              </div>
            </section>

            <section className="side-section">
              <div className="section-title">{text.snapshot}</div>
              <pre className="json-box">{workspaceSummary ? JSON.stringify(workspaceSummary, null, 2) : text.notLoaded}</pre>
            </section>
          </aside>
        </div>
      </section>

      {taskModal.open && (
        <div
          className="modal-backdrop"
          role="presentation"
          onMouseDown={() => {
            if (!taskModal.running) setTaskModal(emptyTask);
          }}
        >
          <section className="task-modal" role="dialog" aria-modal="true" aria-label={taskModal.title} onMouseDown={(event) => event.stopPropagation()}>
            <header className="modal-header">
              <div>
                <div className="section-title">
                  <PackageOpen size={16} />
                  {taskModal.title}
                </div>
                <p className="muted">{taskModal.detail}</p>
              </div>
              <div className="modal-actions">
                {!!taskModal.files.length && !taskModal.running && !taskModal.result && (
                  <button
                    type="button"
                    onClick={() => generateForFiles(taskModal.files.map((file) => file.id), "为上传项目生成未测测试", true)}
                  >
                    <Bot size={15} />
                    生成这些 Java
                  </button>
                )}
                <button type="button" onClick={() => setTaskModal(emptyTask)} disabled={taskModal.running}>
                  {text.close}
                </button>
              </div>
            </header>

            <div className="task-body">
              <div className="progress-track" aria-label="任务进度">
                <div className="progress-fill" style={{ width: `${taskModal.progress}%` }} />
              </div>
              <div className="task-stats">
                <span>{Math.round(taskModal.progress)}%</span>
                <span>{taskModal.running ? "处理中" : "完成"}</span>
              </div>

              {!!taskModal.files.length && (
                <section className="task-panel">
                  <div className="section-title">Java 文件</div>
                  <div className="task-file-list">
                    {taskModal.files.map((file) => (
                      <button
                        className="task-file-row"
                        key={file.id}
                        type="button"
                        onClick={() => {
                          setActiveFileId(file.id);
                          if (!taskModal.running) setTaskModal(emptyTask);
                        }}
                      >
                        <strong>{file.analysis._project_relative_path || file.original_name}</strong>
                        <span>{file.analysis.class_name || "Unknown"} · {file.analysis.method_count || 0} {text.methods}</span>
                      </button>
                    ))}
                  </div>
                </section>
              )}

              {!!taskModal.rejected.length && (
                <section className="task-panel warning">
                  <div className="section-title">未接收项目</div>
                  <div className="task-file-list">
                    {taskModal.rejected.map((item) => (
                      <div className="task-file-row static" key={`${item.name}-${item.reason}`}>
                        <strong>{item.name}</strong>
                        <span>{item.reason}</span>
                      </div>
                    ))}
                  </div>
                </section>
              )}

              {taskModal.result && (
                <section className="task-panel">
                  <div className="section-title">生成结果</div>
                  <div className="result-grid">
                    <div><strong>{taskModal.result.generated_count || 0}</strong><span>已生成</span></div>
                    <div><strong>{taskModal.result.skipped_count || 0}</strong><span>已跳过</span></div>
                    <div><strong>{taskModal.result.failed_count || 0}</strong><span>失败</span></div>
                  </div>
                  {!!taskModal.result.failed?.length && (
                    <div className="task-file-list">
                      {taskModal.result.failed.map((item) => (
                        <div className="task-file-row static" key={`${item.file_id}-${item.error}`}>
                          <strong>{item.file_name || item.file_id}</strong>
                          <span>{item.error}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </section>
              )}
            </div>
          </section>
        </div>
      )}

      {previewOpen && selectedArtifact && (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => setPreviewOpen(false)}>
          <section className="preview-modal" role="dialog" aria-modal="true" aria-label={text.preview} onMouseDown={(event) => event.stopPropagation()}>
            <header className="modal-header">
              <div>
                <div className="section-title">
                  <Eye size={16} />
                  {text.preview}
                </div>
                <p className="muted">{artifactName(selectedArtifact)}</p>
              </div>
              <div className="modal-actions">
                <button type="button" onClick={() => downloadArtifact(selectedArtifact).catch(console.error)}>
                  <Download size={15} />
                  {text.download}
                </button>
                <button type="button" onClick={() => setPreviewOpen(false)}>
                  {text.close}
                </button>
              </div>
            </header>
            <pre className="code-preview modal-code">{artifactCode || text.loading}</pre>
          </section>
        </div>
      )}
    </main>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
