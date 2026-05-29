// State
let arReportResult = null;
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
  [1, 3, 4].forEach(i => {
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
        <h3>${config.icon} ${config.label} 廣告數據</h3>
        <button class="btn-icon" onclick="removePlatformPanel('${platform}')" title="移除">✕</button>
      </div>
      <div class="input-toggle" style="margin-bottom:12px;">
        <button class="active" onclick="togglePanelInput(this, '${platform}', 'upload')">上傳檔案</button>
        <button onclick="togglePanelInput(this, '${platform}', 'paste')">貼上數據</button>
      </div>
      <div class="panel-input-upload" id="${platform}-upload">
        <div class="upload-zone mini-upload">
          <input type="file" accept=".csv,.xlsx,.xls,.txt" onchange="handlePlatformFile(this, '${platform}')">
          <p><strong>上傳 ${config.label} 後台匯出的 CSV / Excel</strong></p>
        </div>
        <div class="file-info" id="${platform}-file-info" style="display:none;">
          <span>📎</span>
          <span class="file-name" id="${platform}-file-name"></span>
        </div>
      </div>
      <div class="panel-input-paste" id="${platform}-paste" style="display:none;">
        <textarea class="text-input platform-data" data-platform="${platform}"
          placeholder="${config.placeholder}"></textarea>
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

  const key = name.trim().toLowerCase().replace(/\s+/g, '_');

  if (activePlatforms.has(key)) {
    showToast('此平台已存在', 'error');
    return;
  }

  // Add chip
  const selector = document.querySelector('.platform-selector');
  const addBtn = selector.querySelector('.add-custom');
  const chip = document.createElement('div');
  chip.className = 'platform-chip selected';
  chip.dataset.platform = key;
  chip.innerHTML = `<span class="chip-icon">📈</span> ${name.trim()}`;
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

// --- Processing ---
async function arStartProcessing() {
  // Validate basic info (now on the same page)
  const client = document.getElementById('client-name').value.trim();
  const company = document.getElementById('company-name').value.trim();
  const month = document.getElementById('report-month').value;
  if (!client || !company || !month) {
    showToast('請填寫客戶名稱、公司名稱與報告月份', 'error');
    return;
  }

  // Collect data
  const platformsData = {};
  let hasData = false;

  activePlatforms.forEach(platform => {
    const textarea = document.querySelector(`#panel-${platform} .platform-data`);
    if (textarea && textarea.value.trim()) {
      platformsData[platform] = textarea.value.trim();
      hasData = true;
    }
  });

  if (!hasData) {
    showToast('請至少輸入一個平台的數據', 'error');
    return;
  }
  if (!getDeepSeekKey()) {
    showToast('請先點右上角「API 設定」填入 DeepSeek API Key', 'error');
    toggleApiSettings();
    return;
  }

  const payload = {
    client_name: document.getElementById('client-name').value.trim(),
    company_name: document.getElementById('company-name').value.trim(),
    report_month: document.getElementById('report-month').value,
    platforms: platformsData,
    api_key: getDeepSeekKey()
  };

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
      <h1>${cover.title || '廣告月報'}</h1>
      <div class="meta-info">
        <div>客戶：${cover.client_name || ''}</div>
        <div>製作：${cover.company_name || ''}</div>
        <div>報告月份：${cover.report_month || ''}</div>
      </div>
    </div>`;

  // Sections
  (data.sections || []).forEach(section => {
    html += `
      <div class="report-section">
        <h2>${section.title}</h2>
        <div class="section-content">${markdownToHtml(section.content || '')}</div>
      </div>`;
  });

  // Closing
  const closing = data.closing || {};
  html += `
    <div class="report-closing">
      <h2>${closing.title || 'Thank You'}</h2>
      <p>${closing.content || ''}</p>
    </div>`;

  container.innerHTML = html;
}

// --- Markdown to HTML ---
function markdownToHtml(text) {
  if (!text) return '';

  let html = text
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

    const data = await res.json();
    if (data.error) {
      showToast(data.error, 'error');
      return;
    }

    window.location.href = `/api/download/${data.filename}`;
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
