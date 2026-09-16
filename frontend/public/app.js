/* ==========================================================================
   환율 AI 비서 — 프론트엔드 (바닐라 JavaScript, 프레임워크 없음)

   구성
   1. 공통 도구       : DOM 생성, 숫자·시간 포맷, API 호출, 토스트
   2. 서버 깨우기     : Render 무료 서버 콜드스타트 대응
   3. 데이터 요약     : GET /api/data/summary → 상단 카드
   4. 채팅            : POST /api/chat
   5. 대화 기록       : GET/DELETE /api/conversations
   6. 데이터 관리     : /api/data CRUD
   7. 화면 전환·테마  : 좁은 화면 탭, 다크 모드

   보안: 서버에서 받은 글자는 전부 textContent 로만 넣는다(innerHTML 미사용 → XSS 차단).
   ========================================================================== */

"use strict";

const API_BASE = String((window.APP_CONFIG && window.APP_CONFIG.API_BASE_URL) || "").replace(/\/+$/, "");

const CHAT_TIMEOUT_MS = 90_000; // AI 응답 + 서버 깨우기 시간까지 고려
const DEFAULT_TIMEOUT_MS = 20_000;
const RECENT_ROWS = 30;
const CHAT_MAX = 2000;

const SUGGESTIONS = [
  "최근 환율 추세가 어때?",
  "가장 환율이 높았던 날은?",
  "이번 달 평균 환율은 얼마야?",
  "하루에 가장 크게 떨어진 날은 언제야?",
];

const TREND_BADGE = {
  up: "원화 약세 · 환율 상승",
  down: "원화 강세 · 환율 하락",
  flat: "보합",
  insufficient: "데이터 부족",
  none: "데이터 없음",
};

const state = {
  conversationId: null,
  sending: false,
  summary: null,
  rows: [],
  editingId: null,
  filter: null, // { start, end } 또는 null(최근 N건)
};

/* ==========================================================================
   1. 공통 도구
   ========================================================================== */

const $ = (selector, root = document) => root.querySelector(selector);
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** 안전한 DOM 생성기. 글자는 항상 텍스트 노드로 들어간다. */
function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "dataset") Object.assign(node.dataset, value);
    else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
    else if (value === true) node.setAttribute(key, "");
    else node.setAttribute(key, String(value));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

/** 아이콘 SVG (정적 문자열만 사용) */
function icon(pathD) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", pathD);
  svg.append(path);
  return svg;
}

const ICON = {
  close: "M6 6l12 12M18 6L6 18",
  data: "M4 19V9M10 19V5M16 19v-7M22 19H2",
};

const nf2 = new Intl.NumberFormat("ko-KR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const won = (v) => (v === null || v === undefined ? "–" : `${nf2.format(v)}원`);
const signedPct = (v) => (v === null || v === undefined ? "–" : `${v > 0 ? "+" : ""}${nf2.format(v)}%`);

function localDateString(date = new Date()) {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

function relativeTime(iso) {
  if (!iso) return "";
  const time = new Date(iso);
  if (Number.isNaN(time.getTime())) return "";
  const sec = (Date.now() - time.getTime()) / 1000;
  if (sec < 60) return "방금 전";
  if (sec < 3600) return `${Math.floor(sec / 60)}분 전`;
  if (sec < 86_400) return `${Math.floor(sec / 3600)}시간 전`;
  if (sec < 172_800) return "어제";
  if (sec < 604_800) return `${Math.floor(sec / 86_400)}일 전`;
  return time.toLocaleDateString("ko-KR", { year: "numeric", month: "short", day: "numeric" });
}

function clockTime(iso) {
  const time = new Date(iso);
  if (Number.isNaN(time.getTime())) return "";
  const sameDay = time.toDateString() === new Date().toDateString();
  return sameDay
    ? time.toLocaleTimeString("ko-KR", { hour: "numeric", minute: "2-digit" })
    : time.toLocaleString("ko-KR", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

/* ---------- API ---------- */

class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

const FIELD_LABELS = {
  date: "날짜",
  value: "환율",
  memo: "메모",
  message: "질문",
  conversation_id: "대화 ID",
  record_id: "날짜",
  title: "제목",
  messages: "메시지",
  start_date: "시작일",
  end_date: "종료일",
  limit: "개수",
};

/** FastAPI/Pydantic 422 오류 목록 → 한국어 문장 */
function describeValidation(items) {
  const lines = items.slice(0, 3).map((item) => {
    const loc = Array.isArray(item.loc) ? item.loc : [];
    const field = [...loc].reverse().find((p) => typeof p === "string" && !["body", "query", "path"].includes(p));
    const label = FIELD_LABELS[field] || field || "입력값";
    const ctx = item.ctx || {};
    switch (item.type) {
      case "missing":
      case "string_too_short":
        return `${label}을(를) 입력해 주세요.`;
      case "string_too_long":
        return `${label}은(는) ${ctx.max_length}자 이하로 입력해 주세요.`;
      case "greater_than_equal":
        return `${label}은(는) ${ctx.ge} 이상이어야 합니다.`;
      case "less_than_equal":
        return `${label}은(는) ${ctx.le} 이하여야 합니다.`;
      case "string_pattern_mismatch":
        return `${label} 형식이 올바르지 않습니다.`;
      case "float_parsing":
      case "float_type":
      case "finite_number":
        return `${label}은(는) 숫자로 입력해 주세요.`;
      case "extra_forbidden":
        return `허용되지 않는 항목(${field})이 포함되어 있습니다.`;
      case "value_error":
        return String(item.msg || "").replace(/^Value error,\s*/, "");
      default:
        return `${label}: ${item.msg}`;
    }
  });
  return lines.join(" ");
}

async function api(path, { method = "GET", body, timeout = DEFAULT_TIMEOUT_MS } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  let response;
  try {
    response = await fetch(API_BASE + path, {
      method,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    });
  } catch (error) {
    throw new ApiError(
      error.name === "AbortError"
        ? "응답 시간이 초과되었습니다. 서버가 깨어나는 중일 수 있으니 잠시 후 다시 시도해 주세요."
        : "서버에 연결할 수 없습니다. 인터넷 연결이나 서버 상태를 확인해 주세요.",
      0,
    );
  } finally {
    clearTimeout(timer);
  }

  const text = await response.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = null;
    }
  }

  if (!response.ok) {
    const detail = data && data.detail;
    const message = Array.isArray(detail)
      ? describeValidation(detail)
      : typeof detail === "string"
        ? detail
        : `요청을 처리하지 못했습니다. (${response.status})`;
    throw new ApiError(message, response.status);
  }
  return data;
}

/* ---------- 토스트 ---------- */

function toast(message, type = "info") {
  const node = el("div", { class: `toast ${type}`, role: type === "error" ? "alert" : "status" }, message);
  $("#toast-region").append(node);
  setTimeout(
    () => {
      node.classList.add("leaving");
      setTimeout(() => node.remove(), 300);
    },
    type === "error" ? 5200 : 2800,
  );
}

/* ==========================================================================
   2. 서버 깨우기 (Render 무료 티어 콜드스타트)
   ========================================================================== */

function setServerStatus(stateName) {
  const pill = $("#server-status");
  const labels = { checking: "서버 확인 중", online: "서버 연결됨", offline: "서버 연결 실패" };
  pill.dataset.state = stateName;
  pill.title = labels[stateName];
  $(".status-text", pill).textContent = labels[stateName];
}

async function wakeServer() {
  const banner = $("#server-banner");
  const bannerText = $("#banner-text");
  const elapsed = $("#banner-elapsed");
  const retry = $("#banner-retry");

  banner.classList.remove("error");
  retry.hidden = true;
  elapsed.textContent = "";
  bannerText.textContent =
    "서버를 깨우는 중입니다. 무료 서버는 한동안 쓰지 않으면 잠들어서, 첫 접속에 최대 50초가 걸릴 수 있어요.";
  setServerStatus("checking");

  const started = Date.now();
  // 2.5초 안에 응답하면 배너를 아예 보여주지 않는다 (서버가 이미 깨어 있는 경우)
  const showTimer = setTimeout(() => {
    banner.hidden = false;
  }, 2500);
  const ticker = setInterval(() => {
    elapsed.textContent = `${Math.round((Date.now() - started) / 1000)}초`;
  }, 1000);

  try {
    for (;;) {
      try {
        await api("/health", { timeout: 60_000 });
        break;
      } catch (error) {
        if (Date.now() - started > 90_000) throw error;
        await sleep(3000);
      }
    }
    setServerStatus("online");
    banner.hidden = true;
    return true;
  } catch {
    setServerStatus("offline");
    banner.hidden = false;
    banner.classList.add("error");
    bannerText.textContent = "서버에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.";
    elapsed.textContent = "";
    retry.hidden = false;
    return false;
  } finally {
    clearTimeout(showTimer);
    clearInterval(ticker);
  }
}

async function start() {
  if (!API_BASE) {
    setServerStatus("offline");
    const banner = $("#server-banner");
    banner.hidden = false;
    banner.classList.add("error");
    $("#banner-text").textContent = "API 서버 주소(API_BASE_URL)가 설정되지 않았습니다. config.js 를 확인하세요.";
    return;
  }
  if (await wakeServer()) {
    await Promise.all([loadSummary(), loadHistory(), loadData()]);
  }
}

/* ==========================================================================
   3. 데이터 요약
   ========================================================================== */

function summaryTile(label, value, sub, tone) {
  return el(
    "div",
    {},
    el("dt", {}, label),
    el("dd", { class: tone || null, title: sub ? `${value} (${sub})` : value }, value, sub ? el("span", { class: "sub" }, sub) : null),
  );
}

function renderSummarySkeleton() {
  const grid = $("#summary-grid");
  grid.replaceChildren(
    ...Array.from({ length: 6 }, () =>
      el("div", {}, el("span", { class: "skeleton", style: "width:45%" }), el("span", { class: "skeleton", style: "width:80%;height:18px;margin-top:8px" })),
    ),
  );
}

async function loadSummary() {
  const grid = $("#summary-grid");
  grid.setAttribute("aria-busy", "true");
  try {
    const summary = await api("/api/data/summary");
    state.summary = summary;
    renderSummary(summary);
  } catch (error) {
    $("#trend-card").dataset.dir = "none";
    $("#trend-badge").textContent = "불러오기 실패";
    $("#trend-change").textContent = "–";
    $("#trend-desc").textContent = error.message;
    grid.replaceChildren();
  } finally {
    grid.setAttribute("aria-busy", "false");
  }
}

function renderSummary(s) {
  const m = s.metrics;
  const card = $("#trend-card");
  card.dataset.dir = s.trend_direction;
  $("#trend-badge").textContent = TREND_BADGE[s.trend_direction] || s.trend_direction;
  $("#trend-change").textContent = s.change_pct === null ? "–" : signedPct(s.change_pct);
  $("#trend-desc").textContent =
    s.recent_avg === null
      ? s.trend
      : `최근 ${s.trend_window}영업일 평균 ${won(s.recent_avg)} · 직전 ${s.trend_window}영업일 ${won(s.previous_avg)}`;

  const grid = $("#summary-grid");
  if (!m) {
    grid.replaceChildren(summaryTile("데이터", "0건", "데이터 관리에서 추가해 주세요"));
  } else {
    const changeTone = m.total_change_pct > 0 ? "up" : m.total_change_pct < 0 ? "down" : null;
    grid.replaceChildren(
      summaryTile("데이터 기간", `${s.count.toLocaleString("ko-KR")}영업일`, s.period),
      summaryTile("최근 종가", won(m.last_rate), m.last_date),
      summaryTile("기간 평균", won(m.average), `중앙값 ${won(m.median)}`),
      summaryTile("최고", won(m.max), m.max_date, "up"),
      summaryTile("최저", won(m.min), m.min_date, "down"),
      summaryTile("변동폭", won(m.range), `표준편차 ${won(m.std)}`),
      summaryTile("기간 변화율", signedPct(m.total_change_pct), `${won(m.first_rate)} → ${won(m.last_rate)}`, changeTone),
    );
  }

  $("#chat-empty-desc").textContent = m
    ? `${s.period} · ${s.count.toLocaleString("ko-KR")}영업일의 환율 요약을 AI에게 함께 전달해, 내 데이터에 근거해서 답합니다.`
    : "아직 저장된 환율 데이터가 없어요. 데이터 관리에서 먼저 추가해 주세요.";
}

/* ==========================================================================
   4. 채팅
   ========================================================================== */

const chatLog = () => $("#chat-log");
const chatInput = () => $("#chat-input");

function renderSuggestions() {
  $("#suggestions").replaceChildren(
    ...SUGGESTIONS.map((q) =>
      el("button", { class: "chip", type: "button", onclick: () => sendMessage(q) }, q),
    ),
  );
}

function scrollChatToBottom() {
  const log = chatLog();
  log.scrollTop = log.scrollHeight;
}

function setChatHeader(title, messageCount) {
  $("#chat-title").textContent = title || "새 대화";
  $("#chat-sub").textContent = messageCount
    ? `메시지 ${messageCount}개 · 이어서 질문하면 최근 10번의 대화를 기억해요`
    : "저장된 환율 데이터 요약을 근거로 답합니다";
}

function appendMessage({ role, content, timestamp, context, error }) {
  $("#chat-empty").hidden = true;
  const meta = el(
    "div",
    { class: "msg-meta" },
    el("span", {}, role === "user" ? "나" : "AI 비서"),
    timestamp ? el("span", {}, clockTime(timestamp)) : null,
    context
      ? el(
          "span",
          { class: "msg-context", title: context.trend },
          icon(ICON.data),
          `근거: ${context.count.toLocaleString("ko-KR")}영업일 요약 (${context.period})`,
        )
      : null,
  );
  const node = el(
    "div",
    { class: `msg ${role}${error ? " error" : ""}` },
    el("div", { class: "msg-body" }, content),
    meta,
  );
  chatLog().append(node);
  scrollChatToBottom();
  return node;
}

function showTyping() {
  const text = el("span", {}, "저장된 환율 데이터를 살펴보는 중…");
  const node = el(
    "div",
    { class: "msg assistant typing", id: "typing", "aria-label": "AI가 답변을 준비하고 있습니다" },
    el("div", { class: "msg-body" }, el("span", { class: "dots", "aria-hidden": "true" }, el("i"), el("i"), el("i")), text),
  );
  chatLog().append(node);
  scrollChatToBottom();
  node._timers = [
    setTimeout(() => (text.textContent = "답변을 작성하는 중…"), 4000),
    setTimeout(() => (text.textContent = "조금 오래 걸리고 있어요. 서버가 깨어나는 중일 수 있어요…"), 15000),
  ];
}

function hideTyping() {
  const node = $("#typing");
  if (!node) return;
  (node._timers || []).forEach(clearTimeout);
  node.remove();
}

function setSending(on) {
  state.sending = on;
  const button = $("#chat-send");
  button.disabled = on;
  $(".send-label", button).textContent = on ? "답변 중…" : "보내기";
  chatLog().setAttribute("aria-busy", String(on));
}

function updateCharCount() {
  const length = chatInput().value.length;
  const counter = $("#chat-count");
  counter.textContent = `${length.toLocaleString("ko-KR")} / ${CHAT_MAX.toLocaleString("ko-KR")}`;
  counter.classList.toggle("near-limit", length > CHAT_MAX * 0.9);
}

function autoGrow() {
  const input = chatInput();
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight + 2, 168)}px`;
}

function clearChatLog() {
  hideTyping();
  chatLog()
    .querySelectorAll(".msg")
    .forEach((node) => node.remove());
  $("#chat-empty").hidden = false;
}

async function sendMessage(rawText) {
  const text = String(rawText || "").trim();
  if (!text || state.sending) return;
  if (text.length > CHAT_MAX) {
    toast(`질문은 ${CHAT_MAX.toLocaleString("ko-KR")}자 이하로 입력해 주세요.`, "error");
    return;
  }

  const input = chatInput();
  const userNode = appendMessage({ role: "user", content: text, timestamp: new Date().toISOString() });
  if (input.value.trim() === text) input.value = "";
  updateCharCount();
  autoGrow();
  setSending(true);
  showTyping();

  try {
    const res = await api("/api/chat", {
      method: "POST",
      body: { message: text, conversation_id: state.conversationId || undefined },
      timeout: CHAT_TIMEOUT_MS,
    });
    hideTyping();
    appendMessage({
      role: "assistant",
      content: res.reply,
      timestamp: new Date().toISOString(),
      context: res.context,
    });
    state.conversationId = res.conversation_id;
    setChatHeader(res.title, res.message_count);
    loadHistory();
  } catch (error) {
    hideTyping();
    // 서버는 AI 가 실패하면 아무것도 저장하지 않는다 → 화면에서도 '저장 안 됨'을 표시하고 입력을 되돌려준다
    userNode.classList.add("failed");
    $(".msg-meta", userNode).append(el("span", { class: "msg-failed-note" }, "전송 실패 · 저장되지 않음"));
    appendMessage({ role: "assistant", content: error.message, error: true });
    if (!input.value.trim()) {
      input.value = text;
      updateCharCount();
      autoGrow();
    }
    if (error.status === 404 && state.conversationId) {
      // 다른 탭에서 대화를 지운 경우 — 새 대화로 이어가도록 초기화
      state.conversationId = null;
      setChatHeader(null, 0);
      loadHistory();
    }
  } finally {
    setSending(false);
    if (window.matchMedia("(hover: hover)").matches) input.focus();
  }
}

function startNewChat() {
  if (state.sending) {
    toast("답변을 받는 중에는 새 대화를 시작할 수 없어요.");
    return;
  }
  state.conversationId = null;
  clearChatLog();
  setChatHeader(null, 0);
  markActiveHistory();
  switchTab("chat");
  chatInput().focus();
}

/* ==========================================================================
   5. 대화 기록
   ========================================================================== */

async function loadHistory() {
  const list = $("#history-list");
  list.setAttribute("aria-busy", "true");
  if (!list.children.length) {
    list.replaceChildren(
      ...Array.from({ length: 4 }, () =>
        el("li", { class: "history-skeleton" }, el("span", { class: "skeleton", style: "width:70%" }), el("span", { class: "skeleton", style: "width:95%" })),
      ),
    );
  }
  try {
    const items = await api("/api/conversations?limit=50");
    renderHistory(items);
  } catch (error) {
    list.replaceChildren(el("li", { class: "empty" }, error.message));
    $("#history-empty").hidden = true;
  } finally {
    list.setAttribute("aria-busy", "false");
  }
}

function renderHistory(items) {
  const list = $("#history-list");
  list.replaceChildren(
    ...items.map((item) =>
      el(
        "li",
        { class: "history-item", dataset: { id: item.id } },
        el(
          "button",
          { class: "history-open", type: "button", onclick: () => openConversation(item.id) },
          el("span", { class: "history-title" }, item.title),
          el("span", { class: "history-preview" }, item.preview),
          el("span", { class: "history-meta" }, `${relativeTime(item.updated_at)} · 메시지 ${item.message_count}개`),
        ),
        el(
          "button",
          {
            class: "icon-btn plain history-delete",
            type: "button",
            "aria-label": `'${item.title}' 대화 삭제`,
            title: "대화 삭제",
            onclick: () => deleteConversation(item),
          },
          icon(ICON.close),
        ),
      ),
    ),
  );
  $("#history-empty").hidden = items.length > 0;
  markActiveHistory();
}

function markActiveHistory() {
  document.querySelectorAll(".history-item").forEach((node) => {
    const active = node.dataset.id === state.conversationId;
    node.classList.toggle("active", active);
    const button = $(".history-open", node);
    if (active) button.setAttribute("aria-current", "true");
    else button.removeAttribute("aria-current");
  });
}

async function openConversation(id) {
  if (state.sending) {
    toast("답변을 받는 중에는 다른 대화를 열 수 없어요.");
    return;
  }
  try {
    const conv = await api(`/api/conversations/${encodeURIComponent(id)}`);
    state.conversationId = conv.id;
    clearChatLog();
    conv.messages.forEach((m) => appendMessage(m));
    setChatHeader(conv.title, conv.message_count);
    markActiveHistory();
    switchTab("chat");
  } catch (error) {
    toast(error.message, "error");
    if (error.status === 404) loadHistory();
  }
}

async function deleteConversation(item) {
  if (!window.confirm(`'${item.title}' 대화를 삭제할까요?\n삭제한 대화는 되돌릴 수 없어요.`)) return;
  try {
    await api(`/api/conversations/${encodeURIComponent(item.id)}`, { method: "DELETE" });
    toast("대화를 삭제했어요.", "success");
    if (state.conversationId === item.id) startNewChat();
    loadHistory();
  } catch (error) {
    toast(error.message, "error");
    if (error.status === 404) loadHistory();
  }
}

/* ==========================================================================
   6. 데이터 관리
   ========================================================================== */

function dataQuery() {
  const params = new URLSearchParams({ order: "desc" });
  if (state.filter) {
    if (state.filter.start) params.set("start_date", state.filter.start);
    if (state.filter.end) params.set("end_date", state.filter.end);
    params.set("limit", "1000");
  } else {
    params.set("limit", String(RECENT_ROWS));
  }
  return `/api/data?${params}`;
}

function tableMessage(text) {
  return el("tr", { class: "table-message" }, el("td", { colspan: "5" }, text));
}

async function loadData() {
  const body = $("#data-body");
  body.setAttribute("aria-busy", "true");
  if (!body.children.length) body.replaceChildren(tableMessage("불러오는 중…"));
  try {
    state.rows = await api(dataQuery());
    renderData();
  } catch (error) {
    body.replaceChildren(tableMessage(error.message));
    $("#data-count").textContent = "불러오기 실패";
  } finally {
    body.setAttribute("aria-busy", "false");
  }
}

function renderData() {
  const rows = state.rows;
  $("#data-count").textContent = state.filter
    ? `기간 조회 결과 ${rows.length.toLocaleString("ko-KR")}건 · 최신순`
    : `최근 ${rows.length}건 · 최신순`;

  const body = $("#data-body");
  if (!rows.length) {
    body.replaceChildren(tableMessage(state.filter ? "해당 기간에 데이터가 없어요." : "저장된 데이터가 없어요."));
    return;
  }
  body.replaceChildren(
    ...rows.map((row, i) => (row.id === state.editingId ? editRow(row) : viewRow(row, rows[i + 1]))),
  );
}

function viewRow(row, older) {
  // 목록이 최신순이므로 바로 아래 행이 직전 영업일이다
  const diff = older ? row.value - older.value : null;
  const diffTone = diff > 0 ? "up" : diff < 0 ? "down" : "";
  const diffText = diff === null ? "–" : `${diff > 0 ? "▲ " : diff < 0 ? "▼ " : ""}${nf2.format(Math.abs(diff))}`;
  return el(
    "tr",
    { dataset: { id: row.id } },
    el("td", { class: "date" }, row.date),
    el("td", { class: "num value" }, nf2.format(row.value)),
    el("td", { class: `num diff ${diffTone}` }, diffText),
    el("td", { class: "memo", title: row.memo || null }, row.memo || ""),
    el(
      "td",
      { class: "row-actions" },
      el("button", { class: "btn tiny ghost", type: "button", onclick: () => startEdit(row.id) }, "수정"),
      el("button", { class: "btn tiny danger-ghost", type: "button", onclick: () => deleteRow(row) }, "삭제"),
    ),
  );
}

function editRow(row) {
  const dateInput = el("input", { type: "date", value: row.date, required: true, "aria-label": "날짜" });
  const valueInput = el("input", {
    type: "number",
    step: "0.01",
    min: "500",
    max: "5000",
    value: String(row.value),
    required: true,
    inputmode: "decimal",
    "aria-label": "환율",
  });
  const memoInput = el("input", { type: "text", maxlength: "200", value: row.memo || "", placeholder: "메모", "aria-label": "메모" });

  const save = () => saveEdit(row, { dateInput, valueInput, memoInput });
  const onKey = (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      save();
    } else if (event.key === "Escape") {
      cancelEdit();
    }
  };
  [dateInput, valueInput, memoInput].forEach((input) => input.addEventListener("keydown", onKey));

  const tr = el(
    "tr",
    { class: "editing", dataset: { id: row.id } },
    el("td", { class: "date" }, dateInput),
    el("td", { class: "value" }, valueInput),
    el("td", { class: "memo-edit", colspan: "2" }, memoInput),
    el(
      "td",
      { class: "row-actions" },
      el("button", { class: "btn tiny primary", type: "button", onclick: save }, "저장"),
      el("button", { class: "btn tiny ghost", type: "button", onclick: cancelEdit }, "취소"),
    ),
  );
  requestAnimationFrame(() => valueInput.focus());
  return tr;
}

function startEdit(id) {
  state.editingId = id;
  renderData();
}

function cancelEdit() {
  state.editingId = null;
  renderData();
}

/** 입력값 확인 — 서버도 같은 규칙으로 다시 검사하지만, 요청 전에 바로 알려주기 위함 */
function validateRecordInputs(dateInput, valueInput) {
  let message = "";
  const value = Number(valueInput.value);
  dateInput.removeAttribute("aria-invalid");
  valueInput.removeAttribute("aria-invalid");
  if (!/^\d{4}-\d{2}-\d{2}$/.test(dateInput.value)) {
    dateInput.setAttribute("aria-invalid", "true");
    message = "날짜를 선택해 주세요.";
  } else if (valueInput.value === "" || !Number.isFinite(value)) {
    valueInput.setAttribute("aria-invalid", "true");
    message = "환율을 숫자로 입력해 주세요.";
  } else if (value < 500 || value > 5000) {
    valueInput.setAttribute("aria-invalid", "true");
    message = "환율은 500원 이상 5,000원 이하로 입력해 주세요.";
  }
  if (message) toast(message, "error");
  return !message;
}

function flashRow(id) {
  requestAnimationFrame(() => {
    const tr = document.querySelector(`#data-body tr[data-id="${CSS.escape(id)}"]`);
    if (!tr) return;
    tr.classList.add("flash");
    tr.scrollIntoView({ block: "nearest", behavior: "smooth" });
  });
}

async function refreshAfterWrite(focusId) {
  await Promise.all([loadData(), loadSummary()]);
  if (focusId) flashRow(focusId);
}

async function saveEdit(row, { dateInput, valueInput, memoInput }) {
  if (!validateRecordInputs(dateInput, valueInput)) return;

  const changes = {};
  const newValue = Math.round(Number(valueInput.value) * 100) / 100;
  const newMemo = memoInput.value.trim();
  if (dateInput.value !== row.date) changes.date = dateInput.value;
  if (newValue !== row.value) changes.value = newValue;
  if (newMemo !== (row.memo || "")) changes.memo = newMemo;

  if (!Object.keys(changes).length) {
    cancelEdit();
    return;
  }

  try {
    const updated = await api(`/api/data/${encodeURIComponent(row.id)}`, { method: "PUT", body: changes });
    state.editingId = null;
    toast(
      changes.date ? `${row.date} 데이터를 ${changes.date}로 옮겼어요.` : `${row.date} 데이터를 수정했어요.`,
      "success",
    );
    await refreshAfterWrite(updated.id);
  } catch (error) {
    toast(error.message, "error");
    if (error.status === 404) {
      state.editingId = null;
      loadData();
    }
  }
}

async function deleteRow(row) {
  if (!window.confirm(`${row.date} (${won(row.value)}) 데이터를 삭제할까요?`)) return;
  try {
    await api(`/api/data/${encodeURIComponent(row.id)}`, { method: "DELETE" });
    toast(`${row.date} 데이터를 삭제했어요.`, "success");
    if (state.editingId === row.id) state.editingId = null;
    await refreshAfterWrite();
  } catch (error) {
    toast(error.message, "error");
    if (error.status === 404) loadData();
  }
}

async function submitNewRecord(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const dateInput = form.elements.date;
  const valueInput = form.elements.value;
  const memoInput = form.elements.memo;
  if (!validateRecordInputs(dateInput, valueInput)) return;

  const payload = {
    date: dateInput.value,
    value: Math.round(Number(valueInput.value) * 100) / 100,
    memo: memoInput.value.trim(),
  };
  const button = $("#data-submit");
  button.disabled = true;
  button.textContent = "추가하는 중…";
  try {
    const created = await api("/api/data", { method: "POST", body: payload });
    const visible = !state.filter && state.rows.length > 0 && created.date >= state.rows[state.rows.length - 1].date;
    toast(
      visible
        ? `${created.date} ${won(created.value)}을(를) 추가했어요.`
        : `${created.date} ${won(created.value)}을(를) 추가했어요. (현재 목록 범위 밖이라 기간 조회로 확인할 수 있어요)`,
      "success",
    );
    valueInput.value = "";
    memoInput.value = "";
    await refreshAfterWrite(created.id);
  } catch (error) {
    if (error.status === 409) {
      dateInput.setAttribute("aria-invalid", "true");
      toast(`${payload.date} 데이터가 이미 있어요. 값을 바꾸려면 목록에서 '수정'을 눌러 주세요.`, "error");
    } else {
      toast(error.message, "error");
    }
  } finally {
    button.disabled = false;
    button.textContent = "데이터 추가";
  }
}

function applyFilter() {
  const start = $("#filter-start").value;
  const end = $("#filter-end").value;
  if (!start && !end) {
    toast("시작일이나 종료일 중 하나 이상을 선택해 주세요.");
    return;
  }
  if (start && end && start > end) {
    toast("시작일이 종료일보다 늦을 수 없어요.", "error");
    return;
  }
  state.filter = { start, end };
  state.editingId = null;
  loadData();
}

function resetFilter() {
  state.filter = null;
  state.editingId = null;
  $("#filter-start").value = "";
  $("#filter-end").value = "";
  loadData();
}

/* ==========================================================================
   7. 화면 전환 · 테마
   ========================================================================== */

function switchTab(name) {
  document.body.dataset.tab = name;
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.setAttribute("aria-pressed", String(tab.dataset.tab === name));
  });
}

function currentTheme() {
  const explicit = document.documentElement.dataset.theme;
  if (explicit) return explicit;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function updateThemeButton() {
  const next = currentTheme() === "dark" ? "라이트" : "다크";
  $("#theme-toggle").setAttribute("aria-label", `${next} 모드로 전환`);
}

function toggleTheme() {
  const next = currentTheme() === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try {
    localStorage.setItem("fx-theme", next);
  } catch {
    /* 저장 실패해도 현재 화면에는 적용된다 */
  }
  updateThemeButton();
}

/* ==========================================================================
   시작
   ========================================================================== */

function bindEvents() {
  $("#chat-form").addEventListener("submit", (event) => {
    event.preventDefault();
    sendMessage(chatInput().value);
  });
  chatInput().addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      sendMessage(chatInput().value);
    }
  });
  chatInput().addEventListener("input", () => {
    updateCharCount();
    autoGrow();
  });
  $("#new-chat").addEventListener("click", startNewChat);
  $("#history-refresh").addEventListener("click", loadHistory);

  $("#data-form").addEventListener("submit", submitNewRecord);
  $("#data-form").addEventListener("input", (event) => event.target.removeAttribute("aria-invalid"));
  $("#filter-apply").addEventListener("click", applyFilter);
  $("#filter-reset").addEventListener("click", resetFilter);

  document.querySelectorAll(".tab").forEach((tab) => tab.addEventListener("click", () => switchTab(tab.dataset.tab)));
  $("#theme-toggle").addEventListener("click", toggleTheme);
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", updateThemeButton);

  $("#banner-retry").addEventListener("click", start);
}

bindEvents();
renderSuggestions();
renderSummarySkeleton();
updateCharCount();
updateThemeButton();
$("#f-date").value = localDateString();
start();
