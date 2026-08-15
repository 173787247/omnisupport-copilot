const state = {
  token: sessionStorage.getItem("omni_token"),
  user: JSON.parse(sessionStorage.getItem("omni_user") || "null"),
  cases: [],
  activeCase: null,
  conversationId: null,
  citations: new Map(),
  actionIdempotencyKey: null,
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  const response = await fetch(path, { ...options, headers });
  if (response.status === 401 && !path.includes("/auth/login")) logout();
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || body.message || `HTTP ${response.status}`);
  return body;
}

function toast(message, error = false) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.toggle("error", error);
  node.classList.remove("hidden");
  setTimeout(() => node.classList.add("hidden"), 4200);
}

function escapeHtml(value = "") {
  return String(value).replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]);
}

function shortDate(value) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("zh-CN", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

function initials(name = "") {
  return name.split(/\s+/).map((part) => part[0]).join("").slice(0, 2).toUpperCase();
}

function canReviewApprovals() {
  return ["support_lead", "support_ops", "billing_ops", "admin", "auditor"].includes(state.user?.role);
}

function canDecideApprovals() {
  return ["support_lead", "support_ops", "billing_ops", "admin"].includes(state.user?.role);
}

function bootUser() {
  $("#user-name").textContent = state.user.display_name;
  $("#user-role").textContent = state.user.role;
  $("#user-initials").textContent = initials(state.user.display_name);
  $("#login-view").classList.add("hidden");
  $("#app-view").classList.remove("hidden");
  $(`[data-view="approvals"]`).classList.toggle("hidden", !canReviewApprovals());
  refreshHealth();
  loadCases();
  if (canReviewApprovals()) loadApprovals(true);
}

function logout() {
  sessionStorage.removeItem("omni_token");
  sessionStorage.removeItem("omni_user");
  location.reload();
}

$("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("#login-error").textContent = "";
  try {
    const result = await api("/api/v1/auth/login", {
      method: "POST",
      body: JSON.stringify({ email: $("#email").value, password: $("#password").value }),
    });
    state.token = result.access_token;
    state.user = result.user;
    sessionStorage.setItem("omni_token", state.token);
    sessionStorage.setItem("omni_user", JSON.stringify(state.user));
    bootUser();
  } catch (error) {
    $("#login-error").textContent = `登录失败：${error.message}`;
  }
});

$("#logout").addEventListener("click", logout);

async function refreshHealth() {
  try {
    const result = await api("/health");
    $("#health-dot").className = result.status;
    $("#health-text").textContent = result.status === "ok" ? "All systems operational" : "System degraded";
  } catch {
    $("#health-dot").className = "degraded";
    $("#health-text").textContent = "Health unavailable";
  }
}

async function loadCases() {
  const params = new URLSearchParams();
  if ($("#case-status").value) params.set("status", $("#case-status").value);
  if ($("#case-search").value.trim()) params.set("search", $("#case-search").value.trim());
  try {
    const result = await api(`/api/v1/cases?${params}`);
    state.cases = result.items;
    $("#case-count").textContent = result.count;
    $("#case-list").innerHTML = result.items.length ? result.items.map((item) => `
      <button class="case-card ${state.activeCase?.ticket_id === item.ticket_id ? "active" : ""}" data-ticket="${item.ticket_id}">
        <div class="row"><code>${item.ticket_id}</code><span class="priority ${item.priority}"></span></div>
        <strong>${escapeHtml(item.subject)}</strong>
        <div class="row"><small>${escapeHtml(item.org_name || item.org_id || "Unknown org")}</small><small>${escapeHtml(item.status)}</small></div>
      </button>`).join("") : `<div class="empty-state"><h3>No cases found</h3><p>Adjust queue filters.</p></div>`;
    $$(".case-card").forEach((button) => button.addEventListener("click", () => openCase(button.dataset.ticket)));
  } catch (error) {
    $("#case-list").innerHTML = `<div class="empty-state"><p>${escapeHtml(error.message)}</p></div>`;
  }
}

let searchTimer;
$("#case-search").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(loadCases, 280); });
$("#case-status").addEventListener("change", loadCases);

async function openCase(ticketId) {
  const result = await api(`/api/v1/cases/${ticketId}`);
  state.activeCase = result.case;
  state.conversationId = result.conversations[0]?.conversation_id || null;
  $("#case-empty").classList.add("hidden");
  $("#case-content").classList.remove("hidden");
  $("#case-id").textContent = result.case.ticket_id;
  $("#case-subject").textContent = result.case.subject;
  $("#case-status-pill").textContent = result.case.status;
  $("#case-priority-pill").textContent = result.case.priority;
  $("#case-org").textContent = result.case.org_name || result.case.org_id;
  $("#case-product").textContent = result.case.product_line.replaceAll("_", " ");
  $("#case-sla").textContent = `SLA ${shortDate(result.case.sla_due_at)}`;
  $("#case-comments").innerHTML = result.comments.length ? result.comments.map((item) => `
    <div class="timeline-item"><header><strong>${escapeHtml(item.author_role || "system")}</strong><time>${shortDate(item.created_at)}</time></header><p>${escapeHtml(item.body)}</p></div>`).join("") : `<p class="form-error">No comments yet.</p>`;
  if (state.conversationId) await loadMessages(); else resetChat();
  loadCases();
}

function resetChat() {
  state.citations.clear();
  $("#chat").innerHTML = `<div class="chat-empty"><div class="orb"></div><h3>Ask from the case.</h3><p>答案只基于已发布证据。没有足够证据时，系统会明确拒答。</p><div class="suggestions"><button>如何恢复 Workspace 管理员访问？</button><button>这个错误码的排查顺序是什么？</button><button>汇总跨文档的恢复策略。</button></div></div>`;
  bindSuggestions();
}

function bindSuggestions() {
  $$(".suggestions button").forEach((button) => button.addEventListener("click", () => {
    $("#question").value = button.textContent;
    $("#question").focus();
  }));
}

function renderMessages(items) {
  state.citations.clear();
  $("#chat").innerHTML = items.map((item) => {
    const citations = Array.isArray(item.citations) ? item.citations : [];
    citations.forEach((citation) => state.citations.set(citation.evidence_id, citation));
    return `<article class="message ${item.role}"><div class="bubble">${escapeHtml(item.content)}</div>
      ${citations.length ? `<div class="evidence-buttons">${citations.map((citation, index) => `<button data-evidence="${citation.evidence_id}">Source ${index + 1} · ${escapeHtml(citation.title || citation.section_path || citation.source_id)}</button>`).join("")}</div>` : ""}
      <div class="message-meta"><span>${item.role === "assistant" ? `confidence ${Number(item.confidence || 0).toFixed(2)}` : "support agent"}</span>${item.role === "assistant" && item.generation_mode ? `<span>${escapeHtml(item.generation_mode === "llm" ? `${item.generation_provider} · ${item.generation_model}` : item.generation_mode)}</span>` : ""}<span>${shortDate(item.created_at)}</span>${item.trace_id ? `<code>${item.trace_id.slice(0, 12)}</code>` : ""}${item.role === "assistant" ? `<span class="feedback"><button data-feedback="1" data-message="${item.message_id}">Useful</button><button data-feedback="-1" data-message="${item.message_id}">Needs work</button></span>` : ""}</div></article>`;
  }).join("");
  $("#chat").scrollTop = $("#chat").scrollHeight;
  $$(`[data-evidence]`).forEach((button) => button.addEventListener("click", () => showEvidence(button.dataset.evidence)));
  $$(`[data-feedback]`).forEach((button) => button.addEventListener("click", () => sendFeedback(button.dataset.message, Number(button.dataset.feedback))));
}

async function loadMessages() {
  const result = await api(`/api/v1/conversations/${state.conversationId}/messages`);
  renderMessages(result.items);
}

$("#chat-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.activeCase) return toast("Select a case first.", true);
  const question = $("#question").value.trim();
  if (!question) return;
  if (!state.conversationId) {
    const conversation = await api(`/api/v1/cases/${state.activeCase.ticket_id}/conversations`, { method: "POST", body: JSON.stringify({}) });
    state.conversationId = conversation.conversation_id;
  }
  $("#question").value = "";
  const current = $("#chat").querySelector(".chat-empty") ? [] : null;
  if (current) $("#chat").innerHTML = "";
  $("#chat").insertAdjacentHTML("beforeend", `<article class="message user"><div class="bubble">${escapeHtml(question)}</div></article><div id="thinking" class="skeleton thinking"></div>`);
  $("#chat").scrollTop = $("#chat").scrollHeight;
  try {
    await api(`/api/v1/conversations/${state.conversationId}/messages`, {
      method: "POST",
      body: JSON.stringify({ question, retrieval_mode: $("#retrieval-mode").value }),
    });
    await loadMessages();
  } catch (error) {
    $("#thinking")?.remove();
    toast(`Copilot failed: ${error.message}`, true);
  }
});

async function sendFeedback(messageId, rating) {
  try {
    await api(`/api/v1/messages/${messageId}/feedback`, { method: "POST", body: JSON.stringify({ rating, reason_code: rating > 0 ? "helpful" : "needs_review" }) });
    toast("Feedback recorded for the evaluation loop.");
  } catch (error) { toast(error.message, true); }
}

function showEvidence(evidenceId) {
  const item = state.citations.get(evidenceId);
  if (!item) return;
  $("#evidence-content").innerHTML = `<div class="evidence-body"><blockquote>${escapeHtml(item.quote || "No preview available")}</blockquote><dl class="evidence-grid">
    <dt>Evidence ID</dt><dd><code>${escapeHtml(item.evidence_id)}</code></dd><dt>Document</dt><dd>${escapeHtml(item.title || item.doc_id)}</dd><dt>Section</dt><dd>${escapeHtml(item.section_path || "—")}</dd><dt>Page</dt><dd>${item.page_no || "—"}</dd><dt>Source</dt><dd>${escapeHtml(item.source_url || item.source_id)}</dd><dt>Score</dt><dd>${Number(item.score || 0).toFixed(4)}</dd></dl></div>`;
  $("#evidence-drawer").classList.remove("hidden");
}

$("#close-drawer").addEventListener("click", () => $("#evidence-drawer").classList.add("hidden"));

$$(".action-trigger").forEach((button) => button.addEventListener("click", () => {
  const operation = button.dataset.operation;
  $("#action-operation").value = operation;
  state.actionIdempotencyKey = `ui-${operation}-${state.activeCase.ticket_id}-${crypto.randomUUID()}`;
  $("#action-title").textContent = operation.replaceAll("_", " ");
  $("#action-status-field").classList.toggle("hidden", operation !== "update_status");
  $("#action-amount-field").classList.toggle("hidden", operation !== "grant_service_credit");
  $("#action-reason").value = "";
  $("#action-dialog").showModal();
}));

$("#action-form").addEventListener("submit", async (event) => {
  if (event.submitter?.value === "cancel") return;
  event.preventDefault();
  const operation = $("#action-operation").value;
  const payload = { operation, reason: $("#action-reason").value, evidence_ids: [...state.citations.keys()], idempotency_key: state.actionIdempotencyKey };
  if (operation === "update_status") payload.new_status = $("#action-status").value;
  if (operation === "grant_service_credit") payload.amount_cents = Math.round(Number($("#action-amount").value) * 100);
  try {
    const result = await api(`/api/v1/cases/${state.activeCase.ticket_id}/actions`, { method: "POST", body: JSON.stringify(payload) });
    state.actionIdempotencyKey = null;
    $("#action-dialog").close();
    toast(result.status === "awaiting_approval" ? `Action paused for approval: ${result.approval_id}` : "Action completed and audited.");
    await openCase(state.activeCase.ticket_id);
    if (canReviewApprovals()) await loadApprovals(true);
  } catch (error) { toast(error.message, true); }
});

async function loadApprovals(badgeOnly = false) {
  if (!canReviewApprovals()) return;
  try {
    const result = await api("/api/v1/approvals?status=pending");
    $("#approval-badge").textContent = result.count;
    $("#approval-badge").classList.toggle("hidden", result.count === 0);
    if (badgeOnly) return;
    $("#approval-list").innerHTML = result.items.length ? result.items.map((item) => `<article class="approval-card"><div><p class="eyebrow">${escapeHtml(item.action)} / ${shortDate(item.created_at)}</p><h3>${escapeHtml(item.payload.operation)} · ${escapeHtml(item.payload.ticket_id)}</h3><p>${escapeHtml(item.payload.reason)}</p><pre>${escapeHtml(JSON.stringify({ risk_level: item.payload.risk_level, amount_cents: item.payload.amount_cents, evidence_ids: item.payload.evidence_ids, trace_id: item.trace_id }, null, 2))}</pre></div>${canDecideApprovals() ? `<div class="approval-actions"><button class="secondary" data-decision="reject" data-approval="${item.approval_id}">Reject</button><button class="primary" data-decision="approve" data-approval="${item.approval_id}">Approve & resume</button></div>` : `<p class="form-error">Read-only audit view</p>`}</article>`).join("") : `<div class="empty-state"><h3>No pending approvals</h3><p>Risky actions will appear here.</p></div>`;
    $$(`[data-decision]`).forEach((button) => button.addEventListener("click", () => decideApproval(button.dataset.approval, button.dataset.decision === "approve")));
  } catch (error) {
    if (!badgeOnly) $("#approval-list").innerHTML = `<div class="empty-state"><p>${escapeHtml(error.message)}</p></div>`;
  }
}

async function decideApproval(approvalId, approved) {
  if (!canDecideApprovals()) return toast("This role can review approvals but cannot decide them.", true);
  const reason = prompt(approved ? "Approval reason" : "Rejection reason", approved ? "Policy verified by support lead" : "Insufficient evidence for this action");
  if (!reason) return;
  try {
    const result = await api(`/api/v1/approvals/${approvalId}/decision`, { method: "POST", body: JSON.stringify({ approved, reason }) });
    toast(`Approval ${result.status}.`);
    await loadApprovals();
  } catch (error) { toast(error.message, true); }
}

async function loadOperations() {
  try {
    const result = await api("/api/v1/operations/overview");
    const values = [
      ["Open cases", result.case_queue.open_cases],
      ["P1 open", result.case_queue.p1_open],
      ["SLA breached", result.case_queue.sla_breached],
      ["Avg confidence", result.copilot_quality.avg_confidence ?? "—"],
    ];
    $("#metric-grid").innerHTML = values.map(([label, value]) => `<article class="metric-card"><small>${label}</small><strong>${value}</strong></article>`).join("");
    const release = result.release || {};
    $("#release-card").innerHTML = `<div class="release-bindings">${["release_id", "data_release_id", "index_release_id", "prompt_release_id", "graph_release_id"].map((key) => `<div class="binding"><span>${key}</span><code>${escapeHtml(release[key] || "not promoted")}</code></div>`).join("")}</div>`;
    $("#component-list").innerHTML = Object.entries(result.components).map(([name, url]) => `<div class="component"><span>${name}</span><a href="${url}" target="_blank" rel="noreferrer">Open ↗</a></div>`).join("");
    if (!$("#kpi-from").value && result.data_window?.date_to) {
      const end = new Date(`${result.data_window.date_to}T00:00:00Z`);
      const start = new Date(end);
      start.setUTCDate(start.getUTCDate() - 30);
      const availableStart = new Date(`${result.data_window.date_from}T00:00:00Z`);
      $("#kpi-from").value = (start < availableStart ? availableStart : start).toISOString().slice(0, 10);
      $("#kpi-to").value = result.data_window.date_to;
    }
  } catch (error) { toast(error.message, true); }
}

function renderKpiRows(rows) {
  if (!rows.length) {
    $("#kpi-results").innerHTML = `<div class="empty-state compact"><p>No governed KPI rows matched this window.</p></div>`;
    return;
  }
  const columns = Object.keys(rows[0]).filter((key) => key !== "generated_at");
  $("#kpi-results").innerHTML = `<table><thead><tr>${columns.map((key) => `<th>${escapeHtml(key)}</th>`).join("")}</tr></thead><tbody>${rows.map((row) => `<tr>${columns.map((key) => `<td>${key === "metric_value" ? Number(row[key] || 0).toFixed(3) : escapeHtml(row[key] ?? "—")}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
}

$("#kpi-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const metrics = [...$("#kpi-metrics").selectedOptions].map((option) => option.value);
  if (!metrics.length) return toast("Select at least one governed metric.", true);
  $("#kpi-policy").textContent = "Validating tool contract and semantic policy…";
  try {
    const result = await api("/api/v1/analytics/kpis", {
      method: "POST",
      body: JSON.stringify({ metrics, dimensions: [$("#kpi-dimension").value], filters: {}, date_from: $("#kpi-from").value, date_to: $("#kpi-to").value, limit: 100 }),
    });
    renderKpiRows(result.rows || []);
    $("#kpi-policy").innerHTML = `<strong>${result.status}</strong> · ${result.policy_applied.map(escapeHtml).join(" · ")} · audit <code>${escapeHtml(result.audit_id)}</code>`;
  } catch (error) {
    $("#kpi-policy").textContent = `Query rejected: ${error.message}`;
    toast(error.message, true);
  }
});

const viewMeta = {
  workspace: ["LIVE CASE OPERATIONS", "Agent Workspace"],
  approvals: ["HUMAN CONTROL", "Approvals"],
  operations: ["SYSTEM CONTROL", "Operations"],
};

$$(".nav-item").forEach((button) => button.addEventListener("click", () => {
  $$(".nav-item").forEach((item) => item.classList.toggle("active", item === button));
  $$(".view").forEach((view) => view.classList.add("hidden"));
  $(`#${button.dataset.view}-view`).classList.remove("hidden");
  $("#view-kicker").textContent = viewMeta[button.dataset.view][0];
  $("#view-title").textContent = viewMeta[button.dataset.view][1];
  if (button.dataset.view === "approvals") loadApprovals();
  if (button.dataset.view === "operations") loadOperations();
}));

bindSuggestions();

function renderPlanCard(card) {
  const action = card.proposed_action || {};
  const control = action.control || "none";
  const clarify = card.needs_clarification;
  const pills = [
    `<span class="pill">${escapeHtml(action.operation || "none")}</span>`,
    `<span class="pill ${control === "hitl" ? "hitl" : control === "confirm" ? "confirm" : ""}">${escapeHtml(control)}</span>`,
    clarify ? `<span class="pill clarify">needs clarification</span>` : "",
    card.abstain_reason ? `<span class="pill clarify">${escapeHtml(card.abstain_reason)}</span>` : "",
    ...(card.protected_identifiers || []).map((id) => `<span class="pill">${escapeHtml(id)}</span>`),
  ].filter(Boolean).join("");
  const steps = (card.steps || []).map((step) => `<li>${escapeHtml(step)}</li>`).join("");
  const citations = (card.citations || []).map((item, index) => {
    state.citations.set(item.evidence_id, item);
    return `<button type="button" data-plan-evidence="${escapeHtml(item.evidence_id)}">Source ${index + 1} · ${escapeHtml(item.source || item.doc_id || item.evidence_id)}</button>`;
  }).join("");
  $("#plan-card").classList.remove("empty");
  $("#plan-card").innerHTML = `
    <h4>Problem handling plan</h4>
    <div class="plan-meta">${pills}</div>
    <p class="plan-summary">${escapeHtml(card.summary || "")}</p>
    ${steps ? `<ol>${steps}</ol>` : ""}
    ${citations ? `<div class="plan-citations">${citations}</div>` : ""}
    <div class="plan-trace">release ${escapeHtml(card.release_id || "—")} · confidence ${Number(card.confidence || 0).toFixed(3)} · trace ${escapeHtml(card.trace_id || "—")}</div>
  `;
  $$(`[data-plan-evidence]`).forEach((button) => button.addEventListener("click", () => showEvidence(button.dataset.planEvidence)));
}

$("#plan-generate")?.addEventListener("click", async () => {
  if (!state.activeCase) return toast("Select a case first.", true);
  const question = $("#question").value.trim() || "Customer deliveries fail with WS-WEBHOOK-401 after signing secret rotation on Workspace 3.2. What is the recovery order?";
  $("#plan-card").classList.remove("empty");
  $("#plan-card").innerHTML = `<p>Generating governed handling plan…</p>`;
  try {
    const card = await api("/api/v1/handling-plan", {
      method: "POST",
      body: JSON.stringify({
        question,
        ticket_id: state.activeCase.ticket_id,
        product_line: state.activeCase.product_line || "northstar_workspace",
        retrieval_mode: $("#retrieval-mode").value === "auto" ? "hybrid" : $("#retrieval-mode").value,
        include_debug: false,
      }),
    });
    renderPlanCard(card);
    toast("Handling plan card generated.");
  } catch (error) {
    $("#plan-card").innerHTML = `<p class="form-error">${escapeHtml(error.message)}</p>`;
    toast(error.message, true);
  }
});

if (state.token && state.user) bootUser();
