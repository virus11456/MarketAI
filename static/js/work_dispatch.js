// State
let wdQuotationText = '';
let wdResult = null;
let wdRoles = [];
let wdSelectedRoles = new Set();

let wdReadyResolve;
window.wdReady = new Promise(resolve => { wdReadyResolve = resolve; });

// --- Init ---
document.addEventListener('DOMContentLoaded', async () => {
  try {
    const res = await fetch('/api/work-dispatch/roles');
    wdRoles = await res.json();
    // Select all by default
    wdRoles.forEach(r => wdSelectedRoles.add(r.id));
    renderRolesGrid();
  } catch (err) {
    console.error('Failed to load roles:', err);
  } finally { wdReadyResolve(); }

  // File upload handler
  const fileInput = document.getElementById('wd-file-input');
  fileInput.addEventListener('change', async (e) => {
    if (e.target.files.length > 0) {
      await wdHandleFile(e.target.files[0]);
    }
  });
});

function renderRolesGrid() {
  const grid = document.getElementById('wd-roles-grid');
  grid.innerHTML = wdRoles.map(r => {
    const selected = wdSelectedRoles.has(r.id) ? 'selected' : '';
    const custom = r.custom ? 'custom' : '';
    return `
      <div class="role-card ${selected} ${custom}" data-role-id="${escapeHtml(r.id)}">
        ${r.custom ? `<span class="role-remove" data-remove-role="true">✕</span>` : ''}
        <div class="role-icon">${escapeHtml(r.icon)}</div>
        <div class="role-name">${escapeHtml(r.name)}</div>
        <div class="role-desc">${escapeHtml(r.desc)}</div>
      </div>`;
  }).join('');
  grid.onclick = event => {
    const card = event.target.closest('[data-role-id]');
    if (!card) return;
    if (event.target.closest('[data-remove-role]')) wdRemoveRole(card.dataset.roleId);
    else wdToggleRole(card.dataset.roleId);
  };
}

function wdToggleRole(id) {
  if (wdSelectedRoles.has(id)) {
    wdSelectedRoles.delete(id);
  } else {
    wdSelectedRoles.add(id);
  }
  const card = Array.from(document.querySelectorAll('.role-card')).find(card => card.dataset.roleId === id);
  if (card) card.classList.toggle('selected');
}

function wdRemoveRole(id) {
  wdRoles = wdRoles.filter(r => r.id !== id);
  wdSelectedRoles.delete(id);
  renderRolesGrid();
}

function wdAddCustomRole() {
  const name = document.getElementById('wd-new-role-name').value.trim();
  const desc = document.getElementById('wd-new-role-desc').value.trim();
  if (!name) {
    showToast('請輸入角色名稱', 'error');
    return;
  }

  const id = 'custom_' + crypto.randomUUID().replace(/-/g, '');
  if (wdRoles.find(r => r.name === name)) {
    showToast('此角色已存在', 'error');
    return;
  }

  wdRoles.push({ id, name, icon: '👤', desc: desc || name, custom: true });
  wdSelectedRoles.add(id);
  renderRolesGrid();

  document.getElementById('wd-new-role-name').value = '';
  document.getElementById('wd-new-role-desc').value = '';
  showToast('角色已新增', 'success');
}

// --- Input Toggle ---
function wdToggleInput(mode) {
  const btns = event.target.parentElement.querySelectorAll('button');
  btns.forEach(b => b.classList.remove('active'));
  event.target.classList.add('active');

  document.getElementById('wd-input-paste').style.display = mode === 'paste' ? 'block' : 'none';
  document.getElementById('wd-input-file').style.display = mode === 'file' ? 'block' : 'none';
}

// --- File Upload ---
async function wdHandleFile(file) {
  if (!file) {
    showToast('請先選擇檔案', 'error');
    return;
  }

  document.getElementById('wd-file-name').textContent = file.name;
  document.getElementById('wd-file-info').style.display = 'flex';

  const formData = new FormData();
  formData.append('file', file);

  showLoading('正在處理檔案...');
  try {
    const res = await fetch('/api/upload', { method: 'POST', body: formData });

    // 後端若 crash / 逾時，可能回傳非 JSON（Vercel 錯誤頁），先安全解析
    let data;
    const raw = await res.text();
    try {
      data = JSON.parse(raw);
    } catch (_) {
      throw new Error(
        res.status === 413 ? '檔案太大，請壓縮或改用較小的檔案' :
        `伺服器回應異常（HTTP ${res.status}）` + (raw ? '：' + raw.slice(0, 120) : '')
      );
    }

    if (data.error) {
      showToast(data.error, 'error');
      wdRemoveFile();
      return;
    }
    wdQuotationText = data.text;
    document.getElementById('wd-quotation-text').value = data.text;
    // Switch to paste view
    const btn = document.querySelector('#wd-section-input .input-toggle button:first-child');
    wdToggleInput.call(null, 'paste');
    btn.classList.add('active');
    btn.nextElementSibling.classList.remove('active');
    document.getElementById('wd-input-paste').style.display = 'block';
    document.getElementById('wd-input-file').style.display = 'none';
    showToast('檔案已匯入', 'success');
  } catch (err) {
    showToast('檔案處理失敗：' + (err.message || err), 'error');
    wdRemoveFile();
  } finally {
    hideLoading();
  }
}

function wdRemoveFile() {
  document.getElementById('wd-file-input').value = '';
  document.getElementById('wd-file-info').style.display = 'none';
}

// --- Step Navigation ---
function wdSetStep(num) {
  for (let i = 1; i <= 4; i++) {
    const el = document.getElementById(`wd-step${i}`);
    el.classList.remove('active', 'done');
    if (i < num) el.classList.add('done');
    if (i === num) el.classList.add('active');
  }
}

function wdGoToStep(num) {
  if (num === 2) {
    const projectName = document.getElementById('wd-project-name').value.trim();
    const text = document.getElementById('wd-quotation-text').value.trim();
    if (!projectName) {
      showToast('請輸入專案名稱', 'error');
      return;
    }
    if (!text && !wdQuotationText) {
      showToast('請輸入報價單內容', 'error');
      return;
    }
    wdQuotationText = text || wdQuotationText;
  }

  document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
  wdSetStep(num);

  const map = {
    1: 'wd-section-input',
    2: 'wd-section-roles',
    4: 'wd-section-results'
  };

  if (map[num]) {
    document.getElementById(map[num]).classList.add('active');
  }
}

// --- Processing ---
async function wdStartProcessing() {
  const selectedRoles = wdRoles.filter(r => wdSelectedRoles.has(r.id));
  if (selectedRoles.length === 0) {
    showToast('請至少選擇一個團隊角色', 'error');
    return;
  }
  if (!getDeepSeekKey()) {
    showToast('請先點右上角「API 設定」填入 DeepSeek API Key', 'error');
    toggleApiSettings();
    return;
  }

  const payload = {
    project_name: document.getElementById('wd-project-name').value.trim(),
    quotation_text: wdQuotationText,
    roles: selectedRoles,
    api_key: getDeepSeekKey()
  };

  wdSetStep(3);
  showLoading('AI 正在分析報價單並拆分工作包...');

  try {
    const res = await fetch('/api/work-dispatch/process', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });

    const data = await res.json();
    if (data.error) {
      showToast(data.error, 'error');
      wdGoToStep(2);
      return;
    }

    wdResult = data;
    if (typeof libraryMarkGenerated === 'function') libraryMarkGenerated();
    renderDispatchResults(data);
    wdGoToStep(4);
    showToast('工作包拆分完成！', 'success');
  } catch (err) {
    showToast('處理失敗：' + err.message, 'error');
    wdGoToStep(2);
  } finally {
    hideLoading();
  }
}

// --- Render Results ---
function renderDispatchResults(data) {
  const container = document.getElementById('wd-results-content');

  // Calculate totals
  const totalDays = (data.role_summary || []).reduce((sum, r) => sum + (r.total_days || 0), 0);
  const totalPackages = (data.work_packages || []).length || data.total_items || 0;
  const totalRoles = (data.role_summary || []).length;

  let html = '';

  // Summary header
  html += `
    <div class="dispatch-summary">
      <h3>${escapeHtml(data.project_name || '專案')}</h3>
      <div class="summary-text">${escapeHtml(data.summary || '')}</div>
      <div class="summary-stats">
        <div class="stat-item">
          <div class="stat-num">${escapeHtml(totalPackages)}</div>
          <div class="stat-label">工作包</div>
        </div>
        <div class="stat-item">
          <div class="stat-num">${escapeHtml(totalRoles)}</div>
          <div class="stat-label">參與角色</div>
        </div>
        <div class="stat-item">
          <div class="stat-num">${escapeHtml(totalDays)}</div>
          <div class="stat-label">預估總工作天</div>
        </div>
      </div>
    </div>`;

  // View toggle
  html += `
    <div class="view-toggle">
      <button class="active" onclick="wdSwitchView('role')">依角色查看</button>
      <button onclick="wdSwitchView('all')">全部工作包</button>
    </div>`;

  // Role summary cards
  html += '<div class="role-summary-grid" id="wd-role-summary">';
  (data.role_summary || []).forEach(rs => {
    const roleObj = wdRoles.find(r => r.id === rs.role_id);
    const icon = roleObj ? roleObj.icon : '👤';
    html += `
      <div class="role-summary-card" data-filter-role="${escapeHtml(rs.role_id)}">
        <div class="rs-icon">${escapeHtml(icon)}</div>
        <div class="rs-name">${escapeHtml(rs.role_name)}</div>
        <div class="rs-stats">${escapeHtml(rs.package_count)} 個工作包 · ${escapeHtml(rs.total_days)} 天</div>
      </div>`;
  });
  html += '</div>';

  // Role-based view
  html += '<div class="view-section active" id="wd-view-role">';
  (data.role_summary || []).forEach(rs => {
    const roleObj = wdRoles.find(r => r.id === rs.role_id);
    const icon = roleObj ? roleObj.icon : '👤';
    html += `<div class="wp-section-title" id="role-${escapeHtml(rs.role_id)}">${escapeHtml(icon)} ${escapeHtml(rs.role_name)} (${escapeHtml(rs.package_count)} 個工作包)</div>`;

    const packages = (data.work_packages || []).filter(wp => wp.assigned_to === rs.role_id);
    packages.forEach(wp => {
      html += renderWorkPackage(wp);
    });
  });
  html += '</div>';

  // All packages view
  html += '<div class="view-section" id="wd-view-all">';
  (data.work_packages || []).forEach(wp => {
    html += renderWorkPackage(wp, true);
  });
  html += '</div>';

  // Timeline
  if (data.timeline_suggestion) {
    html += `
      <div class="timeline-box">
        <h3>建議時程安排</h3>
        <p>${escapeHtml(data.timeline_suggestion)}</p>
      </div>`;
  }

  // Notes
  if (data.notes) {
    html += `
      <div class="timeline-box">
        <h3>整體注意事項</h3>
        <p>${escapeHtml(data.notes)}</p>
      </div>`;
  }

  container.innerHTML = html;
  container.querySelectorAll('[data-filter-role]').forEach(card => {
    card.addEventListener('click', () => wdFilterByRole(card.dataset.filterRole));
  });
}

function renderWorkPackage(wp, showRole) {
  const priorityClass = ['high', 'medium', 'low'].includes(wp.priority) ? wp.priority : 'medium';
  const priorityLabel = { high: '高', medium: '中', low: '低' }[priorityClass] || wp.priority;

  let html = `
    <div class="wp-card">
      <div class="wp-header">
        <span class="wp-id">${escapeHtml(wp.id)}</span>
        <span class="wp-name">${escapeHtml(wp.name)}</span>
        <span class="wp-priority ${escapeHtml(priorityClass)}">${escapeHtml(priorityLabel)}</span>
      </div>
      <div class="wp-meta">`;

  if (showRole) {
    html += `<span>👤 ${escapeHtml(wp.assigned_role_name || wp.assigned_to)}</span>`;
  }
  html += `<span>⏱ ${escapeHtml(wp.estimated_days || '?')} 天</span>`;
  if (wp.module) {
    html += `<span>🗂 ${escapeHtml(wp.module)}</span>`;
  }
  if (wp.amount) {
    html += `<span>💰 ${escapeHtml(wp.amount)}</span>`;
  }
  html += `
      </div>
      <div class="wp-desc">${escapeHtml(wp.description || '')}</div>`;

  if (wp.deliverables && wp.deliverables.length > 0) {
    html += `
      <div class="wp-deliverables">
        <h5>交付物：</h5>
        <ul>${wp.deliverables.map(d => `<li>${escapeHtml(d)}</li>`).join('')}</ul>
      </div>`;
  }

  if (wp.dependencies && wp.dependencies.length > 0 && wp.dependencies[0]) {
    html += `<div class="wp-deps">依賴：${escapeHtml(wp.dependencies.join(', '))}</div>`;
  }

  if (wp.notes) {
    html += `<div class="wp-notes">${escapeHtml(wp.notes)}</div>`;
  }

  html += '</div>';
  return html;
}

// --- View Toggle ---
function wdSwitchView(mode) {
  const btns = document.querySelectorAll('.view-toggle button');
  btns.forEach(b => b.classList.remove('active'));
  const button = document.querySelectorAll('.view-toggle button')[mode === 'role' ? 0 : 1];
  if (button) button.classList.add('active');

  document.querySelectorAll('.view-section').forEach(s => s.classList.remove('active'));
  document.getElementById(`wd-view-${mode}`).classList.add('active');
}

function wdFilterByRole(roleId) {
  // Switch to role view and scroll to role section
  wdSwitchView.call(null, 'role');
  const btns = document.querySelectorAll('.view-toggle button');
  btns[0].classList.add('active');
  btns[1].classList.remove('active');
  document.getElementById('wd-view-role').classList.add('active');
  document.getElementById('wd-view-all').classList.remove('active');

  const el = document.getElementById(`role-${roleId}`);
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// --- Export ---
async function wdExportDocx() {
  if (!wdResult) {
    showToast('沒有可匯出的內容', 'error');
    return;
  }

  try {
    const res = await fetch('/api/work-dispatch/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(wdResult)
    });

    await downloadDocxResponse(res, '工作分派.docx');
    showToast('DOCX 已開始下載', 'success');
  } catch (err) {
    showToast('匯出失敗：' + err.message, 'error');
  }
}

// --- Loading & Toast ---
function showLoading(msg) {
  document.getElementById('loading').classList.add('show');
  document.getElementById('loading-step').textContent = msg || '處理中...';
}

function hideLoading() {
  document.getElementById('loading').classList.remove('show');
}

function showToast(msg, type) {
  const existing = document.querySelector('.toast');
  if (existing) existing.remove();

  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.textContent = msg;
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), 3000);
}
