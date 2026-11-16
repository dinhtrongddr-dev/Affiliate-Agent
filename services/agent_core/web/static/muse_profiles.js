// Update status in place so polling never clears a cookie being edited.
(() => {
  const rows = [...document.querySelectorAll('.muse-profile[data-profile-id]')];
  if (!rows.length) return;
  const date = value => value ? new Date(typeof value === 'number' ? value * 1000 : value)
    .toLocaleString('vi-VN', {timeZone: 'Asia/Ho_Chi_Minh', hour12: false}) : '';
  const text = (row, name, value) => { row.querySelector(`[data-health="${name}"]`).textContent = value; };
  const badge = (row, name, value, tone) => {
    const node = row.querySelector(`[data-health="${name}"]`);
    node.textContent = value;
    node.className = 'badge ' + tone;
  };
  async function refresh() {
    const response = await fetch('/api/muse/profiles');
    if (!response.ok) throw new Error('Chưa tải được trạng thái profile');
    const data = await response.json();
    for (const p of data.profiles) {
      const row = rows.find(r => r.dataset.profileId === p.profile_id);
      if (!row) continue;
      const h = p.health;
      row.classList.toggle('cookie-dead', h.cookie_tone === 'err');
      badge(row, 'cookie', h.cookie_label, h.cookie_tone);
      badge(row, 'session', h.session_label, h.session_state === 'ok' ? 'ok' : h.session_state === 'error' ? 'err' : 'warn');
      badge(row, 'proxy', h.proxy_label, h.proxy_state === 'ok' ? 'ok' : h.proxy_state === 'error' ? 'err' : 'warn');
      text(row, 'expiry', h.expires_at ? 'Hết hạn: ' + date(h.expires_at) : 'Cookie không có ngày hết hạn');
      text(row, 'remaining', h.remaining || '');
      text(row, 'count', `${p.cookie_count} cookie`);
      text(row, 'sync', p.pushed_at ? 'Đã đồng bộ' : 'Chờ đồng bộ');
      text(row, 'session-time', h.session_checked_at ? 'Test: ' + date(h.session_checked_at) : '');
      text(row, 'proxy-time', h.proxy_checked_at ? 'Test: ' + date(h.proxy_checked_at) : '');
      text(row, 'exit-ip', h.exit_ip ? 'IP ra: ' + h.exit_ip : '');
      text(row, 'test-hint', h.can_test ? '' : 'Cần đồng bộ với gateway');
      row.dataset.canTest = String(h.can_test);
      row.querySelectorAll('[data-check]').forEach(b => { b.disabled = !h.can_test || row.dataset.busy === 'true'; });
    }
  }
  for (const row of rows) {
    row.dataset.canTest = String(!row.querySelector('[data-check]').disabled);
    async function action(button, label, work) {
      if (row.dataset.busy === 'true') return;
      row.dataset.busy = 'true';
      row.querySelectorAll('button').forEach(b => { b.disabled = true; });
      const original = button.textContent;
      button.textContent = label;
      const feedback = row.querySelector('[data-health="feedback"]');
      feedback.textContent = label;
      feedback.className = 'small muted';
      try {
        await work(feedback);
        await refresh();
      } catch (error) {
        feedback.textContent = error.message || 'Chưa nhận được kết quả; hãy tải lại trạng thái trước khi thử lại';
        feedback.className = 'small err-text';
      } finally {
        row.dataset.busy = 'false';
        button.textContent = original;
        row.querySelectorAll('button').forEach(b => { b.disabled = b.hasAttribute('data-check') && row.dataset.canTest !== 'true'; });
      }
    }
    row.querySelectorAll('[data-check]').forEach(button => button.addEventListener('click', () => {
      action(button, 'Đang kiểm tra…', async feedback => {
        const result = await api(`/api/muse/profiles/${encodeURIComponent(row.dataset.profileId)}/check/${button.dataset.check}`);
        feedback.textContent = result.message;
        feedback.className = 'small' + (result.ok ? '' : ' err-text');
      });
    }));
    row.querySelector('[data-save-cookie]').addEventListener('click', event => {
      const input = row.querySelector('[data-cookie-json]');
      if (!input.value.trim()) { input.focus(); return toast('Dán JSON cookie mới trước', true); }
      action(event.currentTarget, 'Đang lưu cookie…', async feedback => {
        const result = await api(`/api/muse/profiles/${encodeURIComponent(row.dataset.profileId)}/cookies`, {json: input.value});
        input.value = '';
        feedback.textContent = result.push_error ? 'Đã lưu cookie; chưa đồng bộ: ' + result.push_error : 'Đã cập nhật và đồng bộ cookie. Bấm Test phiên Muse để xác nhận.';
        feedback.className = 'small' + (result.push_error ? ' err-text' : '');
      });
    });
  }
  setInterval(() => refresh().catch(() => {}), 30000);
})();
