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
  if (opts.ok) toast(opts.ok);
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
