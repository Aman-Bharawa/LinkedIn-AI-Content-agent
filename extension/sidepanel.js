const APP_ORIGIN = "https://linkedin-ai-content-agent-zpv4.onrender.com";
const API_ROOT = `${APP_ORIGIN}/extension/api`;
const tokenKey = "linkedinAgentToken";
const ui = {
  connect: document.querySelector("#connect-card"),
  connected: document.querySelector("#connected-view"),
  footer: document.querySelector("#footer"),
  pairForm: document.querySelector("#pair-form"),
  pairCode: document.querySelector("#pair-code"),
  openPairing: document.querySelector("#open-pairing"),
  generateForm: document.querySelector("#generate-form"),
  keyword: document.querySelector("#keyword"),
  generate: document.querySelector("#generate"),
  count: document.querySelector("#character-count"),
  refresh: document.querySelector("#refresh"),
  notice: document.querySelector("#notice"),
  drafts: document.querySelector("#drafts"),
  draftCount: document.querySelector("#draft-count"),
  disconnect: document.querySelector("#disconnect"),
};

let activeToken = "";
let pollTimer = 0;
const pendingGenerations = new Set();

function showNotice(message, isError = false) {
  ui.notice.textContent = message;
  ui.notice.classList.toggle("error", isError);
  ui.notice.classList.remove("hidden");
}

function clearNotice() {
  ui.notice.textContent = "";
  ui.notice.classList.add("hidden");
  ui.notice.classList.remove("error");
}

async function readJson(response) {
  let body = {};
  try {
    body = await response.json();
  } catch {
    body = {};
  }
  if (!response.ok) throw new Error(body.error || `Request failed (${response.status}).`);
  return body;
}

async function api(path, options = {}) {
  const response = await fetch(`${API_ROOT}${path}`, {
    ...options,
    headers: {
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(activeToken ? { Authorization: `Bearer ${activeToken}` } : {}),
      ...options.headers,
    },
  });
  return readJson(response);
}

function setConnected(connected) {
  ui.connect.classList.toggle("hidden", connected);
  ui.connected.classList.toggle("hidden", !connected);
  ui.footer.classList.toggle("hidden", !connected);
}

function dateLabel(value) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

function appendText(parent, tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = value || "";
  parent.append(node);
  return node;
}

function renderDraft(post) {
  const card = document.createElement("article");
  card.className = "draft-card";
  appendText(card, "h3", "", post.topic || "Untitled draft");

  const meta = document.createElement("div");
  meta.className = "draft-meta";
  appendText(meta, "span", "status-pill", post.status.replaceAll("_", " "));
  if (post.scheduled_for) appendText(meta, "span", "", dateLabel(post.scheduled_for));
  card.append(meta);

  if (post.hook) appendText(card, "p", "draft-hook", post.hook);
  if (post.content) appendText(card, "p", "draft-content", post.content);
  if (Array.isArray(post.hashtags) && post.hashtags.length) {
    appendText(card, "p", "hashtags", post.hashtags.map((tag) => `#${tag.replace(/^#+/, "")}`).join(" "));
  }
  if (post.error) appendText(card, "p", "schedule-note", post.error);

  const actions = document.createElement("div");
  actions.className = "card-actions";
  const openLink = document.createElement("a");
  openLink.href = post.url;
  openLink.target = "_blank";
  openLink.rel = "noreferrer";
  openLink.textContent = "Open full draft ↗";
  actions.append(openLink);
  if (["pending_approval", "needs_manual_review"].includes(post.status)) {
    const approve = document.createElement("button");
    approve.className = "button primary";
    approve.type = "button";
    approve.textContent = "Approve & queue";
    approve.addEventListener("click", () => approveDraft(post.id, approve));
    actions.append(approve);
  }
  card.append(actions);
  if (post.status === "approved" && post.scheduled_for) {
    appendText(card, "p", "schedule-note", `Queued to publish ${dateLabel(post.scheduled_for)}.`);
  }
  return card;
}

async function loadDrafts() {
  const result = await api("/drafts/", { method: "POST", body: JSON.stringify({ action: "list" }) });
  ui.drafts.replaceChildren();
  ui.draftCount.textContent = String(result.drafts.length);
  if (!result.drafts.length) {
    appendText(ui.drafts, "p", "empty-state", "Your drafts will show up here. Add a topic above to get started.");
    return;
  }
  const waiting = result.drafts.some((post) => ["generating", "reviewing"].includes(post.status));
  for (const post of result.drafts) {
    ui.drafts.append(renderDraft(post));
    if (pendingGenerations.has(post.id) && !["generating", "reviewing"].includes(post.status)) {
      pendingGenerations.delete(post.id);
      showNotice(post.status === "failed" ? "Draft generation finished with an issue. See the draft card for details." : "Your draft is ready for review.");
    }
  }
  if (waiting && !pollTimer) pollTimer = window.setInterval(() => loadDrafts().catch(handleApiError), 3000);
  if (!waiting && pollTimer) {
    window.clearInterval(pollTimer);
    pollTimer = 0;
  }
}

function handleApiError(error) {
  showNotice(error.message || "Could not reach the application.", true);
  if (error.message.includes("connection expired")) {
    activeToken = "";
    chrome.storage.local.remove(tokenKey);
    setConnected(false);
  }
}

async function approveDraft(id, button) {
  button.disabled = true;
  try {
    const result = await api(`/drafts/${id}/approve/`, { method: "POST", body: "{}" });
    const schedule = dateLabel(result.scheduled_for);
    showNotice(schedule ? `${result.message} Scheduled for ${schedule}.` : result.message);
    await loadDrafts();
  } catch (error) {
    button.disabled = false;
    handleApiError(error);
  }
}

async function connectWithCode(event) {
  event.preventDefault();
  const code = ui.pairCode.value.trim();
  if (!code) return;
  const button = ui.pairForm.querySelector("button");
  button.disabled = true;
  try {
    const result = await readJson(await fetch(`${API_ROOT}/token/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code }),
    }));
    activeToken = result.token;
    await chrome.storage.local.set({ [tokenKey]: activeToken });
    ui.pairCode.value = "";
    setConnected(true);
    showNotice("Side panel connected. Your account drafts are ready here.");
    await loadDrafts();
  } catch (error) {
    showNotice(error.message || "Pairing failed.", true);
  } finally {
    button.disabled = false;
  }
}

async function startGeneration(event) {
  event.preventDefault();
  const keyword = ui.keyword.value.trim();
  if (keyword.length < 2) {
    showNotice("Enter a topic with at least two characters.", true);
    return;
  }
  clearNotice();
  ui.generate.disabled = true;
  ui.generate.firstElementChild.textContent = "Sending to agent…";
  try {
    const result = await api("/drafts/", { method: "POST", body: JSON.stringify({ keyword }) });
    pendingGenerations.add(result.id);
    ui.keyword.value = "";
    ui.count.textContent = "0 / 255";
    showNotice("Draft requested. It will appear here when generation and review finish.");
    await loadDrafts();
  } catch (error) {
    handleApiError(error);
  } finally {
    ui.generate.disabled = false;
    ui.generate.firstElementChild.textContent = "Generate draft";
  }
}

async function disconnect() {
  try {
    await api("/revoke/", { method: "POST", body: "{}" });
  } catch {
  }
  activeToken = "";
  await chrome.storage.local.remove(tokenKey);
  setConnected(false);
  clearNotice();
}

ui.openPairing.addEventListener("click", () => {
  chrome.tabs.create({ url: `${APP_ORIGIN}/extension/pair/` });
});
ui.pairForm.addEventListener("submit", connectWithCode);
ui.generateForm.addEventListener("submit", startGeneration);
ui.refresh.addEventListener("click", () => loadDrafts().catch(handleApiError));
ui.disconnect.addEventListener("click", disconnect);
ui.keyword.addEventListener("input", () => { ui.count.textContent = `${ui.keyword.value.length} / 255`; });

chrome.storage.local.get(tokenKey).then((saved) => {
  activeToken = saved[tokenKey] || "";
  setConnected(Boolean(activeToken));
  if (activeToken) loadDrafts().catch(handleApiError);
});
