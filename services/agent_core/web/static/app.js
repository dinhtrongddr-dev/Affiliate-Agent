// Small helper layer for the local UI. All POSTs carry the anti-CSRF header.
function toast(msg, isErr) {
  const box = document.getElementById("toast");
  const el = document.createElement("div");
  el.className = "toast" + (isErr ? " err" : "");
  el.textContent = msg;
  box.appendChild(el);
  setTimeout(() => el.remove(), isErr ? 9000 : 4000);
}

async function api(path, body, opts = {}) {
  const init = { method: "POST", headers: { "X-Requested-With": "affiliate-agent" } };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  const res = await fetch(path, init);
  let data = {};
  try { data = await res.json(); } catch (e) { }
  if (!res.ok) {
    const msg = data.error || data.detail || ("Lỗi HTTP " + res.status);
    toast(msg, true);
    throw new Error(msg);
  }
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

function pollJob(jobId) {
  const tick = async () => {
    const r = await fetch("/api/jobs/" + jobId).then(r => r.json()).catch(() => null);
    if (r && !r.busy && document.body.dataset.busy === "1") location.reload();
    if (r) document.querySelectorAll("[data-live-status]").forEach(el => (el.textContent = r.label));
  };
  setInterval(tick, 4000);
}

function tabs(root) {
  root.querySelectorAll(".tabs button").forEach(btn => btn.addEventListener("click", () => {
    root.querySelectorAll(".tabs button").forEach(b => b.classList.toggle("active", b === btn));
    root.querySelectorAll(".tab").forEach(t => t.classList.toggle("active", t.id === btn.dataset.tab));
    try { localStorage.setItem("tab:" + location.pathname, btn.dataset.tab); } catch (e) { }
  }));
  let saved = null;
  try { saved = localStorage.getItem("tab:" + location.pathname); } catch (e) { }
  const b = saved && root.querySelector(`.tabs button[data-tab="${saved}"]`);
  if (b) b.click();
}

async function workerStatus() {
  const el = document.getElementById("worker");
  if (!el) return;
  const r = await fetch("/api/status").then(r => r.json()).catch(() => null);
  if (!r) { el.textContent = "Mất kết nối"; return; }
  el.textContent = !r.worker ? "Worker tắt" : r.task ? `⚙ ${r.task.kind} ${r.task.job_id || ""}` : "● Worker sẵn sàng";
}
setInterval(workerStatus, 4000);
document.addEventListener("DOMContentLoaded", workerStatus);

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
  btn.textContent = `⬆ Cập nhật (${rem.behind} bản mới)`;
  // result of an update that just finished (shown once per update)
  const last = v.last_update;
  let seen = null;
  try { seen = localStorage.getItem("aa-update-seen"); } catch (e) { }
  if (last && last.at && last.at !== seen) {
    try { localStorage.setItem("aa-update-seen", last.at); } catch (e) { }
    if (Date.now() - new Date(last.at).getTime() < 30 * 60 * 1000) {
      if (last.ok) toast("Đã cập nhật xong: " + (last.message || ""));
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
  loadVersion(false);
  setInterval(() => loadVersion(false), 10 * 60 * 1000);
});
