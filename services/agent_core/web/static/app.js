// Small helper layer for the local UI. All POSTs carry the anti-CSRF header.
function toast(msg, isErr) {
  const box = document.getElementById("toast");
  const el = document.createElement("div");
  el.className = "toast" + (isErr ? " err" : "");
  el.textContent = msg;
  box.appendChild(el);
  setTimeout(() => el.remove(), isErr ? 9000 : 4000);
}

// The button that started a request is locked until it finishes (no double sends).
// opts.btn names it; otherwise the button that has focus (the one just clicked) is used.
function busyButton(btn, on) {
  if (!btn || btn.tagName !== "BUTTON") return;
  btn.disabled = on;
  btn.classList.toggle("is-busy", on);
  btn.setAttribute("aria-busy", on ? "true" : "false");
}

async function api(path, body, opts = {}) {
  const init = { method: "POST", headers: { "X-Requested-With": "affiliate-agent" } };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  const active = document.activeElement;
  const btn = opts.btn || (active && active.tagName === "BUTTON" && !active.disabled ? active : null);
  busyButton(btn, true);
  let res, data = {};
  try {
    res = await fetch(path, init);
    try { data = await res.json(); } catch (e) { }
  } catch (e) {
    busyButton(btn, false);
    toast("Mất kết nối tới app", true);
    throw e;
  }
  // stays locked when the page is about to reload or navigate
  if (!res.ok || !(opts.reload || opts.go)) busyButton(btn, false);
  if (!res.ok) {
    const msg = data.error || data.detail || ("Lỗi HTTP " + res.status);
    toast(msg, true);
    throw new Error(msg);
  }
  if (!opts.reload && !opts.go && opts.keepDirty !== true) document.body.dataset.dirty = "0";  // saved
  if (opts.ok) toast(typeof opts.ok === "function" ? opts.ok(data) : opts.ok);
  if (opts.reload) setTimeout(() => location.reload(), opts.delay || 600);
  if (opts.go) setTimeout(() => (location.href = opts.go(data)), 500);
  return data;
}

function setupDrop(el) {
  if (!el) return;
  const input = el.querySelector("input[type=file]");
  el.addEventListener("click", () => input.click());
  ["dragenter", "dragover"].forEach(e => el.addEventListener(e, ev => { ev.preventDefault(); el.classList.add("over"); }));
  ["dragleave", "drop"].forEach(e => el.addEventListener(e, ev => { ev.preventDefault(); el.classList.remove("over"); }));
  el.addEventListener("drop", ev => upload(ev.dataTransfer.files));
  input.addEventListener("change", () => upload(input.files));
  async function upload(files) {
    for (const f of files) {
      const fd = new FormData();
      fd.append("file", f);
      toast("Đang tải lên " + f.name + "…");
      const r = await api("/api/upload", fd);
      toast(r.inserted ? "Đã nhận video, AI đang xử lý" : "Video đã có trong hệ thống");
    }
    setTimeout(() => location.reload(), 800);
  }
}

// One poller for every live element. It pauses while the tab/window is hidden, backs off
// from `min` to `max` while nothing changes, and returns to `min` as soon as something does.
// `fn` returns true when it saw a change, or "stop" to end polling.
function livePoll(fn, opts = {}) {
  const min = opts.min || 4000, max = opts.max || 15000;
  let delay = min, timer = null, running = false;
  const run = async () => {
    timer = null;
    if (document.hidden) return;           // resumed by visibilitychange
    running = true;
    let res = false;
    try { res = await fn(); } catch (e) { res = false; }
    running = false;
    if (res === "stop") return;
    delay = res ? min : Math.min(max, Math.round(delay * 1.5));
    timer = setTimeout(run, delay);
  };
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && !timer && !running) { delay = min; run(); }
  });
  if (opts.immediate) run(); else timer = setTimeout(run, delay);
}

async function getJSON(path) {
  return fetch(path, {cache: "no-store"}).then(r => (r.ok ? r.json() : null)).catch(() => null);
}

function fmtDur(sec) {
  if (sec == null) return "";
  sec = Math.max(0, Math.round(sec));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return h ? `${h} giờ ${m} phút` : m ? `${m} phút${m < 10 && s ? " " + s + " giây" : ""}` : `${s} giây`;
}

function renderTimeline(box, p) {
  if (!box) return;
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
  const head = el("div", "tl-head");
  head.append(el("b", null, p.pct != null ? p.pct + "%" : ""),
    el("span", "small", [p.elapsed_sec != null ? "đã chạy " + fmtDur(p.elapsed_sec) : "",
                          p.eta_sec ? "còn khoảng " + fmtDur(p.eta_sec) : ""].filter(Boolean).join(" · ")));
  const bar = el("div", "tl-bar"); const fill = el("i"); fill.style.width = (p.pct || 0) + "%"; bar.append(fill);
  const list = el("ol", "tl-steps");
  (p.steps || []).forEach(st => {
    const li = el("li", "tl-" + st.state);
    li.append(el("span", "tl-dot"), el("span", "tl-label", st.label));
    const info = st.state === "done" ? fmtDur(st.elapsed_sec)
      : st.state === "running" ? `${st.pct}% · ${fmtDur(st.elapsed_sec)}` : "~" + fmtDur(st.expected_sec);
    li.append(el("span", "tl-info small", info));
    if (st.key === "muse" && (p.clips || []).length && st.state !== "pending") {
      const clips = el("div", "tl-clips");
      p.clips.forEach(c => {
        const chip = el("span", "tl-clip " + (c.status === "DONE" ? "ok" : c.status === "FAILED" ? "err" : ""),
          `${c.kind === "image" ? "Ảnh NV" : "Cảnh " + (c.scene + 1)} ${c.pct}%${c.attempt > 1 ? " (lần " + c.attempt + ")" : ""}`);
        clips.append(chip);
      });
      li.append(clips);
    }
    list.append(li);
  });
  box.replaceChildren(head, bar, list);
}

function pollJob(jobId) {
  let last = null;
  const box = document.querySelector("[data-job-progress]");
  livePoll(async () => {
    const r = await getJSON("/api/jobs/" + jobId + "/progress");
    if (!r) return false;
    if (!r.busy && document.body.dataset.busy === "1") { softReload(); return "stop"; }
    document.querySelectorAll("[data-live-status]").forEach(el => (el.textContent = r.label));
    if (r.busy) renderTimeline(box, r);
    const sig = r.updated_at + ":" + r.pct;
    const changed = last !== null && last !== sig;
    last = sig;
    return changed;
  }, {immediate: true});
}

// List pages: rows marked data-job/data-status. Reload once when a busy row changes state.
function watchJobRows() {
  const rows = [...document.querySelectorAll("[data-job][data-busy='1']")];
  if (!rows.length) return;
  const ids = rows.map(r => r.dataset.job);
  livePoll(async () => {
    const r = await getJSON("/api/jobs-live?ids=" + encodeURIComponent(ids.join(",")));
    if (!r) return false;
    let changed = false;
    r.jobs.forEach(j => {
      const row = document.querySelector(`[data-job="${CSS.escape(j.id)}"]`);
      if (!row) return;
      if (row.dataset.status !== j.status) changed = true;
      const cell = row.querySelector("[data-job-pct]");
      if (cell && j.pct != null) cell.textContent = `${j.pct}%` + (j.eta_sec ? ` · còn ~${fmtDur(j.eta_sec)}` : "");
    });
    if (changed) { softReload(); return "stop"; }
    return false;
  }, {min: 5000, max: 20000, immediate: true});
}

function watchTasks() {
  let sig = null;
  livePoll(async () => {
    const r = await getJSON("/api/tasks-live");
    if (!r) return false;
    if (sig !== null && r.signature !== sig) { softReload(); return "stop"; }
    sig = r.signature;
    return false;
  }, {min: 5000, max: 30000, immediate: true});
}

function tabs(root) {
  // one saved tab per kind of page (all job pages share it), never one key per job
  const path = location.pathname;
  const key = "tab:" + (path.split("/").length > 2 ? path.replace(/\/[^/]+$/, "/*") : path);
  const buttons = [...root.querySelectorAll(".tabs button")];
  root.querySelector(".tabs")?.setAttribute("role", "tablist");
  const select = btn => {
    buttons.forEach(b => { b.classList.toggle("active", b === btn); b.setAttribute("aria-selected", b === btn); });
    root.querySelectorAll(".tab").forEach(t => t.classList.toggle("active", t.id === btn.dataset.tab));
  };
  buttons.forEach(btn => {
    btn.setAttribute("role", "tab");
    btn.addEventListener("click", () => {
      select(btn);
      try { localStorage.setItem(key, btn.dataset.tab); } catch (e) { }
    });
  });
  // a tab named in the URL (?tab=x or #t-x) wins over the remembered one
  const wanted = new URLSearchParams(location.search).get("tab");
  const fromUrl = (wanted && root.querySelector(`.tabs button[data-tab="t-${wanted}"]`))
    || (location.hash && root.querySelector(`.tabs button[data-tab="${location.hash.slice(1)}"]`));
  if (fromUrl) { select(fromUrl); return; }
  let saved = null;
  try { saved = localStorage.getItem(key); } catch (e) { }
  const b = saved && root.querySelector(`.tabs button[data-tab="${saved}"]`);
  if (b) select(b);
  else { const cur = buttons.find(x => x.classList.contains("active")); if (cur) select(cur); }
}

// True while the person is typing or has unsaved edits: auto-refresh must not throw them away.
function userEditing() {
  const a = document.activeElement;
  if (a && (a.tagName === "TEXTAREA" || a.isContentEditable ||
            (a.tagName === "INPUT" && !["checkbox", "radio", "button", "submit"].includes(a.type)))) return true;
  return document.body.dataset.dirty === "1";
}

function softReload() {
  if (!userEditing()) { location.reload(); return; }
  if (document.getElementById("refresh-bar")) return;
  const bar = document.createElement("button");
  bar.id = "refresh-bar"; bar.type = "button"; bar.className = "refresh-bar";
  bar.textContent = "Có cập nhật mới – bấm để tải lại (đang giữ nội dung bạn đang sửa)";
  bar.onclick = () => location.reload();
  document.body.append(bar);
}
document.addEventListener("input", e => {
  if (e.target.closest && e.target.closest("main") && !e.target.closest("[data-no-dirty]")) document.body.dataset.dirty = "1";
});

function iconSvg(name) {
  return `<svg class="ico" aria-hidden="true"><use href="#i-${name}"/></svg>`;
}

async function workerStatus() {
  const el = document.getElementById("worker");
  if (!el) return "stop";
  const r = await getJSON("/api/status");
  const [text, cls] = !r ? ["Mất kết nối", "err"] : !r.worker ? ["Worker tắt", "err"]
    : r.task ? [`Đang chạy: ${r.task.kind} ${r.task.job_id || ""}`, "run"] : ["Worker sẵn sàng", "ok"];
  const label = el.querySelector(".worker-text") || el, dot = el.querySelector(".dot");
  const changed = label.textContent !== text;
  label.textContent = text;
  if (dot) dot.className = "dot " + cls;
  return changed;
}
document.addEventListener("DOMContentLoaded", () => {
  livePoll(workerStatus, {min: 5000, max: 30000, immediate: true});
  watchJobRows();
});

// ---------------------------------------------------------------- version + in-app update
let versionInfo = null;

function fmtVNDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return "";
  const p = Object.fromEntries(new Intl.DateTimeFormat("en-GB", {timeZone: "Asia/Ho_Chi_Minh", day: "2-digit",
    month: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false}).formatToParts(d).map(x => [x.type, x.value]));
  return `${p.day}/${p.month} ${p.hour}:${p.minute}`;
}

function updModal(children) {
  const old = document.querySelector(".upd-modal");
  if (old) old.remove();
  const wrap = document.createElement("div");
  wrap.className = "upd-modal";
  const box = document.createElement("div");
  box.className = "box";
  children.forEach(c => box.append(c));
  wrap.append(box);
  document.body.append(wrap);
  return wrap;
}
function elt(tag, text, cls) {
  const n = document.createElement(tag);
  if (text !== undefined) n.textContent = text;
  if (cls) n.className = cls;
  return n;
}

async function loadVersion(recheck) {
  const el = document.getElementById("version");
  const btn = document.getElementById("update-btn");
  if (!el) return;
  if (recheck) { el.textContent = "Đang kiểm tra bản mới…"; await fetch("/api/version/check", {method: "POST",
    headers: {"X-Requested-With": "affiliate-agent"}}).catch(() => null); }
  const v = await fetch("/api/version").then(r => r.json()).catch(() => null);
  if (!v) return;
  versionInfo = v;
  const loc = v.local || {}, rem = v.remote || {};
  el.replaceChildren(elt("b", loc.build ? `Bản #${loc.build}` : "Bản dev"),
                     ` · ${fmtVNDate(loc.date)} · ${loc.hash || ""}`);
  const note = rem.behind ? `Có ${rem.behind} bản mới` : rem.error ? "Chưa kiểm tra được bản mới" : rem.checked ? "Đang dùng bản mới nhất" : "";
  if (note) { el.append(document.createElement("br"), note); }
  el.title = (loc.title ? "Thay đổi gần nhất: " + loc.title + "\n" : "") + "Bấm để kiểm tra bản mới";
  btn.hidden = !rem.behind;
  btn.innerHTML = `${iconSvg("update")} Cập nhật (${rem.behind} bản mới)`;
  // result of an update that just finished (shown once per update)
  const last = v.last_update;
  let seen = null;
  try { seen = localStorage.getItem("aa-update-seen"); } catch (e) { }
  if (last && last.at && last.at !== seen) {
    try { localStorage.setItem("aa-update-seen", last.at); } catch (e) { }
    if (Date.now() - new Date(last.at).getTime() < 30 * 60 * 1000) {
      if (last.ok) toast(`Đã cập nhật xong: đang chạy bản #${loc.build} (${loc.hash})`);
      else showUpdateError(last);
    }
  }
}

function showUpdateError(last) {
  const close = elt("button", "Đóng", "btn");
  const m = updModal([elt("h3", "Cập nhật chưa xong"), elt("p", last.message || "Lỗi không rõ"),
    elt("p", "App đã được mở lại với bản cũ, dữ liệu không mất. Nhật ký: " + (last.log || "logs\\update-*.log"), "small muted"),
    Object.assign(elt("div", undefined, "row-actions"), {})]);
  m.querySelector(".row-actions").append(close);
  close.onclick = () => m.remove();
}

function confirmUpdate() {
  const v = versionInfo || {};
  const rem = v.remote || {};
  const list = elt("ul");
  (rem.changes || []).forEach(c => list.append(elt("li", c.title)));
  if (!list.children.length) list.append(elt("li", `${rem.behind || 1} bản mới trên GitHub`));
  const warn = v.busy ? elt("p", `Đang chạy việc "${v.busy.kind}". Cập nhật sẽ dừng việc này; sau khi mở lại app sẽ tự chạy tiếp.`, "small") : elt("p", "");
  if (v.busy) warn.style.color = "#b42318";
  const go = elt("button", "Cập nhật ngay", "btn primary");
  const cancel = elt("button", "Để sau", "btn");
  const actions = elt("div", undefined, "row-actions");
  actions.append(cancel, go);
  const m = updModal([elt("h3", `Có ${rem.behind || ""} bản mới`), list, warn,
    elt("p", "App sẽ tự sao lưu dữ liệu, tắt, cập nhật rồi mở lại (thường 1–3 phút, lâu hơn nếu cần cài thêm thư viện).", "small muted"),
    actions]);
  cancel.onclick = () => m.remove();
  go.onclick = async () => {
    go.disabled = cancel.disabled = true;
    try {
      await api("/api/version/update", {});
    } catch (e) { m.remove(); return; }
    waitForRestart();
  };
}

function waitForRestart() {
  const status = elt("p", "Đang cập nhật… trang sẽ tự tải lại khi app chạy lại.");
  status.prepend(elt("span", undefined, "spin"));
  updModal([elt("h3", "Đang cập nhật Affiliate Agent"), status,
    elt("p", "Đừng tắt máy. Có thể đóng tab này; app sẽ tự mở lại.", "small muted")]);
  const before = (versionInfo && versionInfo.local && versionInfo.local.hash) || "";
  let wentDown = false;
  const started = Date.now();
  const tick = async () => {
    const v = await fetch("/api/version", {cache: "no-store"}).then(r => r.json()).catch(() => null);
    if (!v) wentDown = true;
    const changed = v && v.local && v.local.hash && v.local.hash !== before;
    // a result written after the click (done, nothing new, or an error) ends the wait as well
    const finished = v && v.last_update && new Date(v.last_update.at).getTime() > started - 5000;
    if (v && (changed || finished || (wentDown && Date.now() - started > 15000))) { location.reload(); return; }
    if (Date.now() - started > 15 * 60 * 1000) {
      status.textContent = "Cập nhật lâu hơn bình thường. Xem logs\\update-*.log hoặc mở lại app từ shortcut.";
      return;
    }
    setTimeout(tick, 3000);
  };
  setTimeout(tick, 4000);
}

document.addEventListener("DOMContentLoaded", () => {
  const btn = document.getElementById("update-btn");
  if (btn) btn.addEventListener("click", confirmUpdate);
  const el = document.getElementById("version");
  if (el) el.addEventListener("click", () => loadVersion(true));
  loadVersion(new URLSearchParams(location.search).has("check-update"));   // tray "check for updates"
  livePoll(() => loadVersion(false).then(() => false), {min: 10 * 60 * 1000, max: 10 * 60 * 1000});
});
