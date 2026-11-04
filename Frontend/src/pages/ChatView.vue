<script setup>
import { ref, reactive, computed, nextTick, onMounted, onUnmounted, watch } from 'vue'
import DOMPurify from 'dompurify'
import { marked } from 'marked'

const API_BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000'
const STORAGE_KEY = 'rag-chat-conversation'
let abortController = null

const messages = ref([])
const inputText = ref('')
const sessionId = ref(null)
const isLoading = ref(false)
const apiOnline = ref(null)
const docCount = ref(0)
const documents = ref([])
const chatModel = ref('')
const chatContainer = ref(null)
const inputRef = ref(null)
const sidebarOpen = ref(false)
const copiedMessageId = ref(null)

let statusTimer = null
let persistenceTimer = null
let copiedTimer = null

const suggestions = [
  'How does Tesla make money?',
  "What was NVIDIA's first graphics accelerator called?",
  'How much did Microsoft pay to acquire GitHub?',
  "What was the name of SpaceX's first drone ship to land at sea?",
  'What is the Attention mechanism in transformers?',
]

const canSend = computed(() =>
  inputText.value.trim().length > 0 && !isLoading.value && apiOnline.value !== false,
)
const isEmpty = computed(() => messages.value.length === 0)

async function checkStatus() {
  try {
    const res = await fetch(`${API_BASE}/status`)
    if (!res.ok) throw new Error(`Status check failed (${res.status})`)
    const data = await res.json()
    apiOnline.value = data.status === 'ok'
    docCount.value = data.document_count
    documents.value = Array.isArray(data.documents) ? data.documents : []
    chatModel.value = typeof data.model === 'string' ? data.model : ''
  } catch {
    apiOnline.value = false
  }
}

// ─── Streaming chat ──────────────────────────────────────────────────
function handleEvent(ev, botMsg) {
  switch (ev.type) {
    case 'metadata':
      sessionId.value    = ev.session_id
      botMsg.sources     = [...new Set(ev.sources)]
      botMsg.searchQuery = ev.search_query
      break
    case 'token': {
      const stick = isNearBottom()          // check BEFORE the DOM grows
      botMsg.text += ev.text
      if (stick) scrollBottom()
      break
    }
    case 'error':
      throw new Error(ev.detail || 'Generation failed')
  }
}

async function runStream(text, botMsg) {
  abortController = new AbortController()
  try {
    const res = await fetch(`${API_BASE}/chat/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId.value, message: text }),
      signal: abortController.signal,
    })

    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.detail || `Server error (${res.status})`)
    }

    const reader  = res.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      const lines = buffer.split('\n')
      buffer = lines.pop()                  // keep any partial line for next chunk
      for (const line of lines) {
        if (line.trim()) handleEvent(JSON.parse(line), botMsg)
      }
    }
    if (buffer.trim()) handleEvent(JSON.parse(buffer), botMsg)
  } catch (err) {
    if (err.name === 'AbortError') {
      if (!botMsg.text) botMsg.text = '(stopped)'
    } else {
      botMsg.text = botMsg.text
        ? `${botMsg.text}\n\n⚠️ ${err.message}`
        : `⚠️ Error: ${err.message}`
      botMsg.isError = true
    }
  } finally {
    botMsg.loading = false
    isLoading.value = false
    abortController = null
    inputRef.value?.focus()
  }
}

async function sendMessage(textOverride) {
  const text = (typeof textOverride === 'string' ? textOverride : inputText.value).trim()
  if (!text || isLoading.value) return

  if (typeof textOverride !== 'string') {
    inputText.value = ''
  }
  isLoading.value = true
  messages.value.push(reactive({ id: crypto.randomUUID(), role: 'user', text }))

  // reactive() is what makes later mutations (botMsg.text += ...) trigger updates
  const botMsg = reactive({
    id: crypto.randomUUID(),
    role: 'bot',
    text: '',
    sources: [],
    searchQuery: '',
    loading: true,
    isError: false,
  })
  messages.value.push(botMsg)
  await nextTick()
  autoGrow()
  scrollBottom()

  await runStream(text, botMsg)
}

function stopGenerating() {
  abortController?.abort()
}

async function retryMessage(botMsg) {
  const index = messages.value.indexOf(botMsg)
  const previousMessage = messages.value[index - 1]
  if (!previousMessage || previousMessage.role !== 'user' || isLoading.value) return

  botMsg.text = ''
  botMsg.sources = []
  botMsg.searchQuery = ''
  botMsg.isError = false
  botMsg.loading = true
  isLoading.value = true

  await runStream(previousMessage.text, botMsg)
}

// ─── Helpers ─────────────────────────────────────────────────────────
function isNearBottom() {
  const el = chatContainer.value
  return !el || el.scrollHeight - el.scrollTop - el.clientHeight < 120
}

async function scrollBottom() {
  await nextTick()
  const el = chatContainer.value
  if (el) el.scrollTop = el.scrollHeight
}

function autoGrow() {
  const el = inputRef.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 160) + 'px'
}

function handleKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {   // isComposing = IME safe
    e.preventDefault()
    sendMessage()
  }
}

async function clearChat() {
  abortController?.abort()
  const id = sessionId.value
  messages.value = []
  sessionId.value = null
  localStorage.removeItem(STORAGE_KEY)
  sidebarOpen.value = false
  if (!id) return
  try {
    await fetch(`${API_BASE}/sessions/${id}`, { method: 'DELETE' })
  } catch (err) {
    console.warn('Failed to delete session on server:', err)
  }
}

function renderMarkdown(text) {
  return DOMPurify.sanitize(marked.parse(text, { breaks: true }))
}

async function copyMessage(message) {
  try {
    await navigator.clipboard.writeText(message.text)
    copiedMessageId.value = message.id
    clearTimeout(copiedTimer)
    copiedTimer = setTimeout(() => {
      copiedMessageId.value = null
    }, 1500)
  } catch (error) {
    console.error('Unable to copy the response:', error)
  }
}

function useSuggestion(suggestion) {
  sendMessage(suggestion)
}

function persistConversation() {
  try {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        sessionId: sessionId.value,
        messages: messages.value.map(
          ({ id, role, text, sources, searchQuery, isError, loading }) => ({
            id,
            role,
            text,
            sources,
            searchQuery,
            isError,
            loading,
          }),
        ),
      }),
    )
  } catch (error) {
    console.error('Unable to save the conversation locally:', error)
  }
}

function schedulePersistence() {
  clearTimeout(persistenceTimer)
  persistenceTimer = setTimeout(persistConversation, 250)
}

function restoreConversation() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null')
    if (!saved || !Array.isArray(saved.messages)) return

    sessionId.value = typeof saved.sessionId === 'string' ? saved.sessionId : null
    messages.value = saved.messages
      .filter(
        message =>
          message && ['user', 'bot'].includes(message.role) && typeof message.text === 'string',
      )
      .map(message =>
        reactive({
          id: message.id || crypto.randomUUID(),
          role: message.role,
          text: message.text,
          sources: Array.isArray(message.sources) ? message.sources : [],
          searchQuery: typeof message.searchQuery === 'string' ? message.searchQuery : '',
          isError: Boolean(message.isError),
          loading: false,
        }),
      )
  } catch (error) {
    console.error('Unable to restore the saved conversation:', error)
    localStorage.removeItem(STORAGE_KEY)
  }
}

watch([messages, sessionId], schedulePersistence, { deep: true, flush: 'post' })

function handleWindowFocus() {
  checkStatus()
}

onMounted(() => {
  restoreConversation()
  checkStatus()
  statusTimer = setInterval(checkStatus, 30000)
  window.addEventListener('focus', handleWindowFocus)
  inputRef.value?.focus()
})

onUnmounted(() => {
  abortController?.abort()
  clearInterval(statusTimer)
  clearTimeout(persistenceTimer)
  clearTimeout(copiedTimer)
  window.removeEventListener('focus', handleWindowFocus)
})
</script>

<template>
  <div class="app-shell">

    <!-- ══════════ SIDEBAR ══════════ -->
    <aside class="sidebar" :class="{ open: sidebarOpen }">
      <div class="sidebar-header">
        <div class="logo">
          <span class="logo-icon">⬡</span>
          <span class="logo-text">RAG<span class="logo-accent">Chat</span></span>
        </div>
        <button class="sidebar-close" aria-label="Close sidebar" @click="sidebarOpen = false">✕</button>
      </div>

      <!-- Status -->
      <div class="sidebar-section">
        <h3 class="sidebar-label">System Status</h3>
        <div class="status-card" :class="{ online: apiOnline === true, offline: apiOnline === false }">
          <span class="status-dot"></span>
          <span v-if="apiOnline === null">Checking…</span>
          <span v-else-if="apiOnline">
            API Online · <strong>{{ docCount.toLocaleString() }}</strong> chunks
          </span>
          <span v-else>API Offline</span>
        </div>
        <button class="btn-ghost btn-sm" @click="checkStatus">↺ Refresh</button>
      </div>

      <!-- Session -->
      <div class="sidebar-section" v-if="sessionId">
        <h3 class="sidebar-label">Session</h3>
        <div class="session-id">
          <code>{{ sessionId.slice(0, 18) }}…</code>
        </div>
        <button class="btn-danger btn-sm" @click="clearChat">🗑 Clear Chat</button>
      </div>

      <!-- Sources legend -->
      <div class="sidebar-section">
        <h3 class="sidebar-label">Knowledge Base</h3>
        <ul class="kb-list">
          <li v-for="doc in documents" :key="doc">
            <span class="kb-dot"></span>{{ doc }}
          </li>
        </ul>
      </div>
    </aside>
    <button
      v-if="sidebarOpen"
      class="mobile-backdrop"
      aria-label="Close sidebar"
      @click="sidebarOpen = false"
    ></button>

    <!-- ══════════ MAIN ══════════ -->
    <div class="main">

      <!-- Topbar -->
      <header class="topbar">
        <button
          class="hamburger"
          aria-label="Toggle sidebar"
          :aria-expanded="sidebarOpen"
          @click="sidebarOpen = !sidebarOpen"
        >
          <span></span><span></span><span></span>
        </button>
        <div class="topbar-title">
          <span class="logo-icon">⬡</span>
          RAG<span class="logo-accent">Chat</span>
        </div>
        <div class="topbar-status" :class="{ online: apiOnline, offline: apiOnline === false }">
          <span class="status-dot"></span>
          <span>{{ apiOnline === null ? '…' : apiOnline ? 'Online' : 'Offline' }}</span>
        </div>
      </header>

      <!-- Chat area -->
      <div class="chat-area" ref="chatContainer">
        <div v-if="apiOnline === false" class="offline-banner" role="alert">
          The backend is offline. Reconnect it to send messages.
          <button class="btn-ghost btn-sm" @click="checkStatus">Retry connection</button>
        </div>

        <!-- Empty state -->
        <Transition name="fade">
          <div v-if="isEmpty" class="empty-state">
            <div class="empty-hero">
              <div class="hero-icon">⬡</div>
              <h1 class="hero-title">Ask anything about your docs</h1>
              <p class="hero-sub">
                Powered by {{ chatModel || 'Gemini' }} + ChromaDB · history-aware RAG
              </p>
            </div>

            <div class="suggestions-grid">
              <button
                v-for="s in suggestions"
                :key="s"
                class="suggestion-chip"
                @click="useSuggestion(s)"
              >
                <span class="chip-arrow">→</span>
                {{ s }}
              </button>
            </div>
          </div>
        </Transition>

        <!-- Messages -->
        <TransitionGroup name="msg" tag="div" class="messages" aria-live="polite" aria-relevant="additions text">
          <div
            v-for="(msg, i) in messages"
            :key="msg.id"
            class="msg-row"
            :class="msg.role"
          >
            <div class="avatar">
              <span v-if="msg.role === 'user'">U</span>
              <span v-else>⬡</span>
            </div>

            <div class="bubble-wrap">
              <div class="bubble" :class="{ error: msg.isError }">
                <!-- dots only until the first token arrives -->
                <div v-if="msg.loading && !msg.text" class="typing-dots">
                  <span></span><span></span><span></span>
                </div>
                <!-- Text / Markdown -->
                <div
                  v-else-if="msg.role === 'bot' && !msg.isError"
                  class="msg-text markdown"
                  v-html="renderMarkdown(msg.text)"
                ></div>
                <div v-else class="msg-text">{{ msg.text }}</div>
              </div>

              <div v-if="msg.role === 'bot' && !msg.loading" class="message-actions">
                <button
                  v-if="msg.text && !msg.isError"
                  class="btn-ghost btn-sm"
                  :aria-label="copiedMessageId === msg.id ? 'Copied response' : 'Copy response'"
                  @click="copyMessage(msg)"
                >
                  {{ copiedMessageId === msg.id ? 'Copied' : 'Copy' }}
                </button>
                <button
                  v-if="msg.isError"
                  class="btn-ghost btn-sm"
                  :disabled="apiOnline === false"
                  @click="retryMessage(msg)"
                >
                  Retry
                </button>
              </div>

              <!-- show sources as soon as metadata arrives (drop the !msg.loading check) -->
              <div v-if="msg.role === 'bot' && (msg.sources?.length || msg.searchQuery)" class="msg-meta">
                <div v-if="msg.searchQuery && msg.searchQuery !== messages[i - 1]?.text" class="search-query">
                  🔍 Searched: <em>{{ msg.searchQuery }}</em>
                </div>
                <div v-if="msg.sources?.length" class="sources">
                  <span class="sources-label">Sources:</span>
                  <span v-for="src in msg.sources" :key="src" class="source-chip">
                    {{ src.split(/[/\\]/).pop() }}
                  </span>
                </div>
              </div>
            </div>
          </div>
        </TransitionGroup>

      </div>

      <!-- Input bar -->
      <div class="input-bar">
        <div class="input-wrap">
          <textarea
            ref="inputRef"
            id="chat-input"
            v-model="inputText"
            class="chat-input"
            placeholder="Ask a question about your documents…"
            rows="1"
            :disabled="apiOnline === false"
            @keydown="handleKeydown"
            @input="autoGrow"
          ></textarea>
          <!-- send button becomes Stop while streaming -->
          <button
            id="send-btn"
            class="send-btn"
            :disabled="!isLoading && !canSend"
            :aria-label="isLoading ? 'Stop generating' : 'Send message'"
            @click="isLoading ? stopGenerating() : sendMessage()"
          >
            <svg v-if="!isLoading" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <line x1="22" y1="2" x2="11" y2="13"></line>
              <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
            </svg>
            <span v-else class="stop-icon"></span>
          </button>
        </div>
        <p class="input-hint">Enter to send · Shift+Enter for new line</p>
      </div>

    </div>
  </div>
</template>

<style scoped>
/* ══════════════════════════════════════════
   LAYOUT
══════════════════════════════════════════ */
.app-shell {
  display: flex;
  height: 100vh;
  height: 100dvh;
  width: 100vw;
  overflow: hidden;
  background: var(--bg-primary);
}

/* ══════════════════════════════════════════
   SIDEBAR
══════════════════════════════════════════ */
.sidebar {
  width: 280px;
  min-width: 280px;
  background: var(--bg-secondary);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  gap: 0;
  overflow-y: auto;
  transition: transform var(--transition);
  z-index: 100;
}

.mobile-backdrop { display: none; }

.sidebar-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 22px 20px 16px;
  border-bottom: 1px solid var(--border);
}

.logo {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 1.2rem;
  font-weight: 700;
  letter-spacing: -0.5px;
}
.logo-icon { font-size: 1.4rem; color: var(--accent-light); }
.logo-accent { color: var(--accent-light); }

.sidebar-close { display: none; background: none; border: none; color: var(--text-secondary); cursor: pointer; font-size: 1rem; }

.sidebar-section {
  padding: 20px;
  border-bottom: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.sidebar-label {
  font-size: 0.7rem;
  font-weight: 600;
  letter-spacing: 1px;
  text-transform: uppercase;
  color: var(--text-muted);
}

.status-card {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  background: var(--bg-card);
  border-radius: var(--radius-sm);
  font-size: 0.85rem;
  border: 1px solid var(--border);
  color: var(--text-secondary);
}
.status-dot {
  width: 8px; height: 8px;
  border-radius: 50%;
  background: var(--text-muted);
  flex-shrink: 0;
  animation: pulse 2s infinite;
}
.status-card.online .status-dot { background: var(--success); }
.status-card.online { color: var(--text-primary); }
.status-card.offline .status-dot { background: var(--danger); animation: none; }

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}

.session-id code {
  background: var(--bg-card);
  padding: 8px 12px;
  border-radius: var(--radius-sm);
  font-size: 0.78rem;
  color: var(--text-secondary);
  border: 1px solid var(--border);
  display: block;
}

.kb-list {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.kb-list li {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 0.85rem;
  color: var(--text-secondary);
}
.kb-dot {
  width: 6px; height: 6px;
  border-radius: 50%;
  background: var(--accent);
  flex-shrink: 0;
}

/* ══════════════════════════════════════════
   MAIN
══════════════════════════════════════════ */
.main {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* Topbar */
.topbar {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 0 20px;
  height: 58px;
  min-height: 58px;
  background: var(--bg-secondary);
  border-bottom: 1px solid var(--border);
}

.hamburger {
  display: none;
  flex-direction: column;
  gap: 5px;
  background: none;
  border: none;
  cursor: pointer;
  padding: 4px;
}
.hamburger span {
  display: block;
  width: 20px; height: 2px;
  background: var(--text-secondary);
  border-radius: 2px;
  transition: var(--transition);
}

.topbar-title {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 1.1rem;
  font-weight: 700;
  flex: 1;
}

.topbar-status {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.8rem;
  color: var(--text-muted);
  padding: 5px 12px;
  background: var(--bg-card);
  border-radius: 99px;
  border: 1px solid var(--border);
}
.topbar-status.online { color: var(--success); }
.topbar-status.offline { color: var(--danger); }
.topbar-status .status-dot { width: 6px; height: 6px; }

/* Chat area */
.chat-area {
  flex: 1;
  overflow-y: auto;
  padding: 24px 20px 12px;
  display: flex;
  flex-direction: column;
}

.offline-banner {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  margin: 0 auto 16px;
  padding: 10px 14px;
  border: 1px solid rgba(239, 68, 68, 0.3);
  border-radius: var(--radius-sm);
  background: rgba(239, 68, 68, 0.1);
  color: #fca5a5;
  font-size: 0.88rem;
}

/* Empty state */
.empty-state {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 40px;
  padding: 40px 20px;
  max-width: 680px;
  margin: 0 auto;
  width: 100%;
}

.empty-hero {
  text-align: center;
}

.hero-icon {
  font-size: 3.5rem;
  color: var(--accent-light);
  margin-bottom: 20px;
  filter: drop-shadow(0 0 24px var(--accent-glow));
  animation: float 4s ease-in-out infinite;
}

@keyframes float {
  0%, 100% { transform: translateY(0); }
  50%       { transform: translateY(-8px); }
}

.hero-title {
  font-size: 1.9rem;
  font-weight: 700;
  letter-spacing: -0.5px;
  background: linear-gradient(135deg, #f0f2ff, var(--accent-light));
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  margin-bottom: 10px;
}

.hero-sub {
  color: var(--text-secondary);
  font-size: 0.92rem;
}

.suggestions-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 10px;
  width: 100%;
}

.suggestion-chip {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: 14px 16px;
  text-align: left;
  font-size: 0.87rem;
  color: var(--text-secondary);
  cursor: pointer;
  transition: all var(--transition);
  display: flex;
  align-items: flex-start;
  gap: 10px;
}
.suggestion-chip:hover {
  background: var(--bg-hover);
  border-color: var(--accent);
  color: var(--text-primary);
  transform: translateY(-1px);
  box-shadow: var(--shadow-glow);
}
.chip-arrow {
  color: var(--accent);
  font-size: 1rem;
  flex-shrink: 0;
  margin-top: 1px;
}

/* Messages */
.messages {
  display: flex;
  flex-direction: column;
  gap: 20px;
  width: 100%;
  max-width: 780px;
  margin: 0 auto;
}

.msg-row {
  display: flex;
  gap: 14px;
  align-items: flex-start;
}

.msg-row.user {
  flex-direction: row-reverse;
}

.avatar {
  width: 34px; height: 34px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.8rem;
  font-weight: 700;
  flex-shrink: 0;
  background: var(--bg-card);
  border: 1px solid var(--border);
  color: var(--text-secondary);
}
.msg-row.user .avatar {
  background: var(--bg-user);
  border-color: transparent;
  color: #fff;
}
.msg-row.bot .avatar {
  background: linear-gradient(135deg, #312e81, #4338ca);
  border-color: transparent;
  color: var(--accent-light);
}

.bubble-wrap {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-width: calc(100% - 50px);
}

.msg-row.user .bubble-wrap {
  align-items: flex-end;
}

.bubble {
  padding: 13px 17px;
  border-radius: var(--radius-md);
  background: var(--bg-card);
  border: 1px solid var(--border);
  box-shadow: var(--shadow-message);
  line-height: 1.65;
  white-space: normal;
  word-break: break-word;
}

.msg-row.user .bubble {
  background: var(--bg-user);
  border-color: transparent;
  color: #fff;
  white-space: pre-wrap;
}

.bubble.error {
  background: rgba(239, 68, 68, 0.1);
  border-color: rgba(239, 68, 68, 0.3);
  color: #fca5a5;
}

.msg-text { font-size: 0.93rem; }

.markdown :deep(p) { margin: 0 0 0.8em; }
.markdown :deep(p:last-child) { margin-bottom: 0; }
.markdown :deep(ul), .markdown :deep(ol) { padding-left: 1.4em; margin: 0.5em 0; }
.markdown :deep(pre) {
  overflow-x: auto;
  padding: 12px;
  border-radius: var(--radius-sm);
  background: var(--bg-secondary);
}
.markdown :deep(code) {
  padding: 0.1em 0.35em;
  border-radius: 4px;
  background: var(--bg-secondary);
  font-size: 0.9em;
}
.markdown :deep(pre code) { padding: 0; background: none; }
.markdown :deep(a) { color: var(--accent-light); }

.message-actions {
  display: flex;
  gap: 6px;
}

/* Typing dots */
.typing-dots {
  display: flex;
  gap: 6px;
  padding: 4px 0;
  align-items: center;
}
.typing-dots span {
  width: 7px; height: 7px;
  border-radius: 50%;
  background: var(--accent);
  animation: bounce 1.2s infinite;
}
.typing-dots span:nth-child(2) { animation-delay: 0.2s; }
.typing-dots span:nth-child(3) { animation-delay: 0.4s; }
@keyframes bounce {
  0%, 80%, 100% { transform: scale(1); opacity: 0.6; }
  40%           { transform: scale(1.3); opacity: 1; }
}

/* Meta */
.msg-meta {
  display: flex;
  flex-direction: column;
  gap: 5px;
}
.search-query {
  font-size: 0.78rem;
  color: var(--text-muted);
  font-style: italic;
}
.search-query em { color: var(--text-secondary); font-style: normal; }

.sources {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.sources-label {
  font-size: 0.75rem;
  color: var(--text-muted);
}
.source-chip {
  background: rgba(99, 102, 241, 0.1);
  border: 1px solid rgba(99, 102, 241, 0.25);
  color: var(--accent-light);
  border-radius: 99px;
  padding: 2px 10px;
  font-size: 0.75rem;
}

/* ══════════════════════════════════════════
   INPUT BAR
══════════════════════════════════════════ */
.input-bar {
  padding: 16px 20px 20px;
  background: var(--bg-secondary);
  border-top: 1px solid var(--border);
}

.input-wrap {
  display: flex;
  gap: 10px;
  align-items: flex-end;
  background: var(--bg-input);
  border: 1.5px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 10px 10px 10px 18px;
  transition: border-color var(--transition), box-shadow var(--transition);
  max-width: 780px;
  margin: 0 auto;
}
.input-wrap:focus-within {
  border-color: var(--border-focus);
  box-shadow: 0 0 0 3px var(--accent-glow);
}

.chat-input {
  flex: 1;
  background: none;
  border: none;
  outline: none;
  resize: none;
  font-family: inherit;
  font-size: 0.93rem;
  color: var(--text-primary);
  line-height: 1.5;
  max-height: 160px;
  min-height: 24px;
}
.chat-input::placeholder { color: var(--text-muted); }
.chat-input:disabled { opacity: 0.5; }

.send-btn {
  width: 40px; height: 40px;
  border-radius: 12px;
  border: none;
  background: var(--accent);
  color: #fff;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  transition: all var(--transition);
}
.send-btn svg { width: 17px; height: 17px; }
.send-btn:hover:not(:disabled) {
  background: var(--accent-light);
  transform: scale(1.05);
  box-shadow: 0 0 16px var(--accent-glow);
}
.send-btn:disabled { opacity: 0.35; cursor: not-allowed; transform: none; }

.stop-icon {
  width: 12px;
  height: 12px;
  background: #fff;
  border-radius: 2px;
}

.input-hint {
  text-align: center;
  font-size: 0.72rem;
  color: var(--text-muted);
  margin-top: 8px;
}

/* ══════════════════════════════════════════
   UTILITY BUTTONS
══════════════════════════════════════════ */
.btn-ghost, .btn-danger {
  padding: 7px 14px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  font-size: 0.8rem;
  cursor: pointer;
  transition: all var(--transition);
  font-family: inherit;
  align-self: flex-start;
}
.btn-sm { padding: 5px 10px; font-size: 0.75rem; }
.btn-ghost {
  background: transparent;
  color: var(--text-secondary);
}
.btn-ghost:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}
.btn-danger {
  background: transparent;
  color: var(--danger);
  border-color: rgba(239, 68, 68, 0.3);
}
.btn-danger:hover {
  background: rgba(239, 68, 68, 0.1);
}

/* ══════════════════════════════════════════
   TRANSITIONS
══════════════════════════════════════════ */
.fade-enter-active, .fade-leave-active { transition: opacity 0.3s; }
.fade-enter-from, .fade-leave-to { opacity: 0; }

.msg-enter-active { transition: all 0.3s ease; }
.msg-enter-from { opacity: 0; transform: translateY(10px); }

/* ══════════════════════════════════════════
   RESPONSIVE
══════════════════════════════════════════ */
@media (max-width: 768px) {
  .sidebar {
    position: fixed;
    top: 0; left: 0; bottom: 0;
    transform: translateX(-100%);
  }
  .mobile-backdrop {
    display: block;
    position: fixed;
    inset: 0;
    z-index: 99;
    border: 0;
    background: rgba(0, 0, 0, 0.48);
  }
  .sidebar.open {
    transform: translateX(0);
    box-shadow: 4px 0 40px rgba(0,0,0,0.5);
  }
  .sidebar-close { display: block; }
  .hamburger { display: flex; }
  .hero-title { font-size: 1.4rem; }
  .suggestions-grid { grid-template-columns: 1fr; }
  .offline-banner { align-items: flex-start; flex-direction: column; }
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    scroll-behavior: auto !important;
    transition-duration: 0.01ms !important;
  }
}
</style>