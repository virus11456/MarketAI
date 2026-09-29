// State
let arReportResult = null;
let arAuditPayload = null;
let arAudit = null;
const activePlatforms = new Set(['meta', 'google']);

const PLATFORM_CONFIG = {
  meta: { icon: '📘', label: 'Meta (Facebook / Instagram)', placeholder: '從 Meta 廣告後台複製數據貼在這裡...\n\n例如：\n廣告活動：品牌知名度 Q1\n花費：NT$ 150,000\n曝光：1,200,000\n點擊：45,000\nCTR：3.75%\nCPC：NT$ 3.33\n轉換：850\nCPA：NT$ 176.47\n\n或直接貼上從後台匯出的報表數據...' },
  google: { icon: '🔴', label: 'Google Ads', placeholder: '從 Google Ads 後台複製數據貼在這裡...\n\n例如：\n廣告活動：搜尋廣告 - 品牌字\n花費：NT$ 80,000\n曝光：500,000\n點擊：25,000\nCTR：5.0%\nCPC：NT$ 3.20\n轉換：320\nCPA：NT$ 250\nROAS：4.5' },
  line: { icon: '💚', label: 'LINE Ads', placeholder: '從 LINE 廣告後台複製數據貼在這裡...\n\n例如：\n廣告活動：好友招募\n花費：NT$ 50,000\n曝光：800,000\n點擊：12,000\nCTR：1.5%\n好友增加：2,000\n每位好友成本：NT$ 25' },
  tiktok: { icon: '🎵', label: 'TikTok Ads', placeholder: '從 TikTok 廣告後台複製數據貼在這裡...\n\n例如：\n廣告活動：品牌曝光影片\n花費：NT$ 60,000\n曝光：2,000,000\n影片觀看：800,000\n點擊：18,000\nCTR：0.9%\n互動：5,000' }
};

// --- Step Navigation ---
// Steps in the UI: 1 填寫資料, 3 AI 產出月報, 4 檢視與匯出
function arSetStep(num) {
  [1, 2, 3, 4].forEach(i => {
    const el = document.getElementById(`ar-step${i}`);
    if (!el) return;
    el.classList.remove('active', 'done');
    if (i < num) el.classList.add('done');
    if (i === num) el.classList.add('active');
  });
}

function arGoToStep(num) {
  document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
  arSetStep(num);

  const sectionMap = {
    1: 'ar-section-info',
    2: 'ar-section-review',
    4: 'ar-section-results'
  };

  const sectionId = sectionMap[num];
  if (sectionId) {
    document.getElementById(sectionId).classList.add('active');
  }
}

// --- Platform Management ---
function togglePlatform(chip) {
  const platform = chip.dataset.platform;

  if (chip.classList.contains('selected')) {
    chip.classList.remove('selected');
    activePlatforms.delete(platform);
    removePlatformPanel(platform);
  } else {
    chip.classList.add('selected');
    activePlatforms.add(platform);
    addPlatformPanel(platform);
  }
}

function addPlatformPanel(platform) {
  const existing = document.getElementById(`panel-${platform}`);
  if (existing) return;

  const config = PLATFORM_CONFIG[platform] || {
    icon: '📈',
    label: platform,
    placeholder: `貼上 ${platform} 的廣告數據...`
  };

  const html = `
    <div class="platform-panel" id="panel-${platform}" data-platform="${platform}">
      <div class="panel-header">
        <h3>${escapeHtml(config.icon)} ${escapeHtml(config.label)} 廣告數據</h3>
        <button class="btn-icon" onclick="removePlatformPanel('${platform}')" title="移除">✕</button>
      </div>
      <div class="form-grid" style="margin-bottom:12px;">
        <label>資料幣別（與原始資料一致）
          <select class="platform-currency">
            <option value="">未確認</option>
            ${['TWD', 'USD', 'HKD', 'JPY', 'EUR', 'CNY', 'GBP', 'SGD', 'AUD', 'CAD'].map(code => `<option value="${code}">${code}</option>`).join('')}
          </select>
        </label>
        <label>成果定義（各列須一致）
          <input class="platform-conversion-type" maxlength="100" placeholder="例：purchase、訊息對話；無成果可留空">
        </label>
      </div>
      <div class="input-toggle" style="margin-bottom:12px;">
        <button class="active" onclick="togglePanelInput(this, '${platform}', 'upload')">上傳檔案</button>
        <button onclick="togglePanelInput(this, '${platform}', 'paste')">貼上數據</button>
      </div>
      <div class="panel-input-upload" id="${platform}-upload">
        <div class="upload-zone mini-upload">
          <input type="file" accept=".csv,.xlsx,.txt" onchange="handlePlatformFile(this, '${platform}')">
          <p><strong>上傳 ${escapeHtml(config.label)} 後台匯出的 CSV / Excel</strong></p>
        </div>
        <div class="file-info" id="${platform}-file-info" style="display:none;">
          <span>📎</span>
          <span class="file-name" id="${platform}-file-name"></span>
        </div>
      </div>
      <div class="panel-input-paste" id="${platform}-paste" style="display:none;">
        <textarea class="text-input platform-data" data-platform="${platform}"
          placeholder="${escapeHtml(config.placeholder)}"></textarea>
      </div>
    </div>`;

  document.getElementById('platform-panels').insertAdjacentHTML('beforeend', html);
}

function removePlatformPanel(platform) {
  const panel = document.getElementById(`panel-${platform}`);
  if (panel) panel.remove();

  activePlatforms.delete(platform);
  const chip = document.querySelector(`.platform-chip[data-platform="${platform}"]`);
  if (chip) chip.classList.remove('selected');
}

function togglePanelInput(btn, platform, mode) {
  const toggle = btn.parentElement;
  toggle.querySelectorAll('button').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');

  document.getElementById(`${platform}-paste`).style.display = mode === 'paste' ? 'block' : 'none';
  document.getElementById(`${platform}-upload`).style.display = mode === 'upload' ? 'block' : 'none';
}

function addCustomPlatform() {
  const name = prompt('請輸入平台名稱：');
  if (!name || !name.trim()) return;

  const key = 'custom_' + crypto.randomUUID().replace(/-/g, '');

  if (Object.hasOwn(PLATFORM_CONFIG, name.trim().toLowerCase()) ||
      Object.values(PLATFORM_CONFIG).some(config => config.label === name.trim())) {
    showToast('此平台已存在', 'error');
    return;
  }

  // Add chip
  const selector = document.querySelector('.platform-selector');
  const addBtn = selector.querySelector('.add-custom');
  const chip = document.createElement('div');
  chip.className = 'platform-chip selected';
  chip.dataset.platform = key;
  chip.innerHTML = `<span class="chip-icon">📈</span> ${escapeHtml(name.trim())}`;
  chip.onclick = function() { togglePlatform(this); };
  selector.insertBefore(chip, addBtn);

  // Add config
  PLATFORM_CONFIG[key] = {
    icon: '📈',
    label: name.trim(),
    placeholder: `貼上 ${name.trim()} 的廣告數據...`
  };

  activePlatforms.add(key);
  addPlatformPanel(key);
}

// --- File Upload for Platform ---
async function handlePlatformFile(input, platform) {
  const file = input.files[0];
  if (!file) return;

  document.getElementById(`${platform}-file-info`).style.display = 'flex';
  document.getElementById(`${platform}-file-name`).textContent = file.name;

  const formData = new FormData();
  formData.append('file', file);

  try {
    const res = await fetch('/api/upload', { method: 'POST', body: formData });
    const data = await res.json();
    if (data.error) {
      showToast(data.error, 'error');
      return;
    }
    // Put text into the paste textarea
    const textarea = document.querySelector(`#panel-${platform} .platform-data`);
    if (textarea) textarea.value = data.text;
    // Switch to paste view to show data
    const toggle = document.querySelector(`#panel-${platform} .input-toggle button:first-child`);
    if (toggle) togglePanelInput(toggle, platform, 'paste');
    showToast('檔案已匯入', 'success');
  } catch (err) {
    showToast('檔案處理失敗', 'error');
  }
}

// --- Validate locally entered data before paying for an AI report. ---
function arCollectReport() {
  const platforms = Object.create(null);
  const metadata = Object.create(null);
  activePlatforms.forEach(platform => {
    const panel = document.getElementById(`panel-${platform}`);
    const text = panel?.querySelector('.platform-data').value.trim();
    if (!text) return;
    const label = platform.startsWith('custom_') ? PLATFORM_CONFIG[platform].label : platform;
    platforms[label] = text;
    metadata[label] = {
      currency: panel.querySelector('.platform-currency').value,
      conversion_type: panel.querySelector('.platform-conversion-type').value.trim()
    };
  });
  return {
    client_name: document.getElementById('client-name').value.trim(),
    company_name: document.getElementById('company-name').value.trim(),
    report_month: document.getElementById('report-month').value,
    platforms,
    platform_metadata: metadata,
    period_confirmed: document.getElementById('ar-period-confirmed').checked
  };
}

async function arStartProcessing() {
  const payload = arCollectReport();
  if (!payload.client_name || !payload.company_name || !payload.report_month || !Object.keys(payload.platforms).length) {
    showToast('請填寫客戶、公司、月份，並至少輸入一個平台的數據', 'error');
    return;
  }
  arAudit = null;
  arAuditPayload = null;
  showLoading('核對來源與計算指標中（不使用 AI 額度）...');
  try {
    const res = await fetch('/api/ad-report/validate', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
    });
    const audit = await res.json();
    if (!res.ok || audit.error) throw new Error(audit.error || '資料核對失敗');
    // A response for stale input must never approve a newer draft.
    if (JSON.stringify(payload) !== JSON.stringify(arCollectReport())) throw new Error('資料已變更，請重新核對。');
    arAuditPayload = payload;
    arAudit = audit;
    renderAudit(audit);
    document.getElementById('ar-review-confirmed').checked = false;
    document.getElementById('ar-generate-button').disabled = !audit.valid;
    arGoToStep(2);
  } catch (err) {
    showToast(err.message, 'error');
  } finally { hideLoading(); }
}

function renderAudit(audit) {
  const labels = { spend: '花費', impressions: '曝光', clicks: '點擊', conversions: '成果', revenue: '轉換價值', ctr: 'CTR (%)', cpc: 'CPC', cpm: 'CPM', cpa: 'CPA', roas: 'ROAS' };
  let html = `<ul>${audit.warnings.map(w => `<li>${escapeHtml(w)}</li>`).join('')}</ul>`;
  audit.platforms.forEach(entry => {
    const label = PLATFORM_CONFIG[entry.platform]?.label || entry.platform;
    html += `<div class="report-section"><h3>${escapeHtml(label)}</h3>`;
    html += `<p>幣別：${escapeHtml(entry.currency || '未確認')} · 成果定義：${escapeHtml(entry.conversion_type || '未確認')}</p>`;
    if (entry.errors.length) html += `<div role="alert" class="audit-errors"><strong>請先修正</strong><ul>${entry.errors.map(e => `<li>${escapeHtml(e)}</li>`).join('')}</ul></div>`;
    html += `<table><thead><tr><th>指標</th><th>程式計算值</th><th>計算依據／限制</th></tr></thead><tbody>`;
    Object.entries(labels).forEach(([key, title]) => {
      html += `<tr><td>${title}</td><td>${escapeHtml(entry.metrics[key] ?? '未計算')}</td><td>${escapeHtml(entry.reasons[key] || audit.formulas[key] || '納入明細列加總')}</td></tr>`;
    });
    html += `</tbody></table><ul>${entry.warnings.map(w => `<li>${escapeHtml(w)}</li>`).join('')}</ul>`;
    html += `<p>納入來源行：${escapeHtml(entry.rows.flatMap(row => row.source_lines).join(', ')) || '無'}；排除總計行：${escapeHtml(entry.excluded_total_lines.join(', ')) || '無'}</p>`;
    const source = arAuditPayload.platforms[entry.platform].split('\n').map((line, i) => `${i + 1}: ${line}`).join('\n');
    html += `<details><summary>檢視原始資料與行號</summary><pre class="audit-source">${escapeHtml(source)}</pre></details></div>`;
  });
  document.getElementById('ar-audit-content').innerHTML = html;
}

async function arGenerateReport() {
  if (!arAudit?.valid || !arAuditPayload || JSON.stringify(arAuditPayload) !== JSON.stringify(arCollectReport())) {
    showToast('資料未通過核對或已變更，請重新核對數據', 'error');
    arGoToStep(1);
    return;
  }
  if (!arAuditPayload.period_confirmed) {
    showToast('請返回資料頁確認各平台資料期間與報告月份一致，再重新核對', 'error');
    return;
  }
  if (!document.getElementById('ar-review-confirmed').checked) {
    showToast('請先核對數字及待確認事項，勾選確認後再產生月報', 'error');
    return;
  }
  if (!getDeepSeekKey()) {
    showToast('請在 API 設定填入 DeepSeek Key 後再產生月報', 'error');
    toggleApiSettings();
    return;
  }
  const payload = { ...arAuditPayload, audit_fingerprint: arAudit.fingerprint, api_key: getDeepSeekKey() };

  arSetStep(3);
  showLoading('AI 正在分析廣告數據並產出月報...');

  try {
    const res = await fetch('/api/ad-report/process', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });

    const data = await res.json();
    if (data.error) {
      showToast(data.error, 'error');
      arGoToStep(1);
      return;
    }

    arReportResult = data;
    renderAdReport(data);
    arGoToStep(4);
    showToast('月報產出完成！', 'success');
  } catch (err) {
    showToast('處理失敗：' + err.message, 'error');
    arGoToStep(1);
  } finally {
    hideLoading();
  }
}

// --- Render Report ---
function renderAdReport(data) {
  const container = document.getElementById('ar-report-content');
  let html = '';

  // Cover
  const cover = data.cover || {};
  html += `
    <div class="report-cover">
      <h1>${escapeHtml(cover.title || '廣告月報')}</h1>
      <div class="meta-info">
        <div>客戶：${escapeHtml(cover.client_name || '')}</div>
        <div>製作：${escapeHtml(cover.company_name || '')}</div>
        <div>報告月份：${escapeHtml(cover.report_month || '')}</div>
      </div>
    </div>`;

  // Sections
  (data.sections || []).forEach(section => {
    html += `
      <div class="report-section">
        <h2>${escapeHtml(section.title)}</h2>
        <div class="section-content">${markdownToHtml(section.content || '')}</div>
      </div>`;
  });

  // Closing
  const closing = data.closing || {};
  html += `
    <div class="report-closing">
      <h2>${escapeHtml(closing.title || 'Thank You')}</h2>
      <p>${escapeHtml(closing.content || '')}</p>
    </div>`;

  container.innerHTML = html;
}

// --- Markdown to HTML ---
function markdownToHtml(text) {
  if (!text) return '';

  let html = escapeHtml(text)
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.*?)\*/g, '<em>$1</em>')
    .replace(/^#### (.*$)/gm, '<h5>$1</h5>')
    .replace(/^### (.*$)/gm, '<h4>$1</h4>')
    .replace(/^## (.*$)/gm, '<h3>$1</h3>');

  // Tables
  if (html.includes('|')) {
    const lines = html.split('\n');
    let inTable = false;
    let tableHtml = '';
    const processed = [];

    for (const line of lines) {
      const trimmed = line.trim();
      if (trimmed.startsWith('|') && trimmed.endsWith('|')) {
        if (trimmed.match(/^\|[\s\-:|]+\|$/)) continue;
        const cells = trimmed.split('|').filter(c => c.trim());
        if (!inTable) {
          inTable = true;
          tableHtml = '<table><thead><tr>' +
            cells.map(c => `<th>${c.trim()}</th>`).join('') +
            '</tr></thead><tbody>';
        } else {
          tableHtml += '<tr>' +
            cells.map(c => `<td>${c.trim()}</td>`).join('') +
            '</tr>';
        }
      } else {
        if (inTable) {
          tableHtml += '</tbody></table>';
          processed.push(tableHtml);
          inTable = false;
          tableHtml = '';
        }
        processed.push(line);
      }
    }
    if (inTable) {
      tableHtml += '</tbody></table>';
      processed.push(tableHtml);
    }
    html = processed.join('\n');
  }

  // Lists
  html = html.replace(/^- (.*$)/gm, '<li>$1</li>');
  html = html.replace(/(<li>.*<\/li>\n?)+/g, '<ul>$&</ul>');

  // Numbered lists
  html = html.replace(/^\d+\. (.*$)/gm, '<li>$1</li>');

  // Paragraphs
  html = html.replace(/\n\n/g, '</p><p>');
  html = html.replace(/\n/g, '<br>');

  return `<p>${html}</p>`;
}

// --- Export ---
async function arExportDocx() {
  if (!arReportResult) {
    showToast('沒有可匯出的內容', 'error');
    return;
  }

  try {
    const res = await fetch('/api/ad-report/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(arReportResult)
    });

    await downloadDocxResponse(res, '廣告月報.docx');
    showToast('DOCX 已開始下載', 'success');
  } catch (err) {
    showToast('匯出失敗：' + err.message, 'error');
  }
}

// --- Loading ---
function showLoading(msg) {
  document.getElementById('loading').classList.add('show');
  document.getElementById('loading-step').textContent = msg || '處理中...';
}

function hideLoading() {
  document.getElementById('loading').classList.remove('show');
}

// --- Toast ---
function showToast(msg, type) {
  const existing = document.querySelector('.toast');
  if (existing) existing.remove();

  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.textContent = msg;
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), 3000);
}

// --- Init: set default month + render pre-selected platform panels ---
document.addEventListener('DOMContentLoaded', () => {
  const monthInput = document.getElementById('report-month');
  const now = new Date();
  // Default to last month
  now.setMonth(now.getMonth() - 1);
  monthInput.value = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;

  // Render panels for platforms selected by default in the markup
  document.querySelectorAll('.platform-chip.selected[data-platform]').forEach(chip => {
    addPlatformPanel(chip.dataset.platform);
  });
});
