// State
let meetingText = '';
let uploadedFilename = '';
let processResults = {};

// --- Input Toggle ---
function toggleInput(mode) {
  const btns = document.querySelectorAll('.input-toggle button');
  btns.forEach(b => b.classList.remove('active'));
  event.target.classList.add('active');

  document.getElementById('input-file').style.display = mode === 'file' ? 'block' : 'none';
  document.getElementById('input-text').style.display = mode === 'text' ? 'block' : 'none';
}

// --- File Upload ---
const uploadZone = document.getElementById('upload-zone');
const fileInput = document.getElementById('file-input');

uploadZone.addEventListener('dragover', (e) => {
  e.preventDefault();
  uploadZone.classList.add('dragover');
});

uploadZone.addEventListener('dragleave', () => {
  uploadZone.classList.remove('dragover');
});

uploadZone.addEventListener('drop', (e) => {
  e.preventDefault();
  uploadZone.classList.remove('dragover');
  if (e.dataTransfer.files.length > 0) {
    fileInput.files = e.dataTransfer.files;
    handleFileSelect(e.dataTransfer.files[0]);
  }
});

fileInput.addEventListener('change', (e) => {
  if (e.target.files.length > 0) {
    handleFileSelect(e.target.files[0]);
  }
});

async function handleFileSelect(file) {
  document.getElementById('file-name').textContent = file.name;
  document.getElementById('file-info').style.display = 'flex';

  const formData = new FormData();
  formData.append('file', file);

  showLoading('正在處理檔案...');

  try {
    const res = await fetch('/api/upload', { method: 'POST', body: formData });
    const data = await res.json();

    if (data.error) {
      showToast(data.error, 'error');
      removeFile();
      return;
    }

    meetingText = data.text;
    uploadedFilename = data.filename;

    // Show transcription preview for audio files
    const audioExts = ['.mp3', '.wav', '.m4a', '.ogg', '.webm', '.mp4'];
    const ext = '.' + file.name.split('.').pop().toLowerCase();
    if (audioExts.includes(ext)) {
      document.getElementById('transcription-preview').style.display = 'block';
      document.getElementById('transcribed-text').value = meetingText;
    }

    showToast('檔案處理完成！', 'success');
  } catch (err) {
    showToast('檔案上傳失敗：' + err.message, 'error');
    removeFile();
  } finally {
    hideLoading();
  }
}

function removeFile() {
  fileInput.value = '';
  uploadedFilename = '';
  meetingText = '';
  document.getElementById('file-info').style.display = 'none';
  document.getElementById('transcription-preview').style.display = 'none';
}

// --- Step Navigation ---
function setStep(num) {
  document.querySelectorAll('.step').forEach((s, i) => {
    s.classList.remove('active', 'done');
    if (i + 1 < num) s.classList.add('done');
    if (i + 1 === num) s.classList.add('active');
  });
}

function goToStep(num) {
  document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
  setStep(num);

  if (num === 1) {
    document.getElementById('section-upload').classList.add('active');
  } else if (num === 2) {
    document.getElementById('section-select').classList.add('active');
  } else if (num === 4) {
    document.getElementById('section-results').classList.add('active');
  }
}

function goToStep2() {
  // Get text from either file upload or direct input
  const directText = document.getElementById('meeting-text').value.trim();
  const transcribedText = document.getElementById('transcribed-text').value.trim();

  if (transcribedText) {
    meetingText = transcribedText; // Allow edited transcription
  } else if (directText) {
    meetingText = directText;
  }

  if (!meetingText) {
    showToast('請先上傳檔案或輸入會議記錄內容', 'error');
    return;
  }

  goToStep(2);
  loadTemplateEditor();
}

// --- Document Type Selection ---
function toggleDocType(el) {
  el.classList.toggle('selected');
}

function getSelectedTypes() {
  return Array.from(document.querySelectorAll('.doc-option.selected'))
    .map(el => el.dataset.type);
}

// --- Template Editor ---
async function loadTemplateEditor() {
  try {
    const res = await fetch('/api/templates');
    const templates = await res.json();

    const editor = document.getElementById('template-editor');
    let html = '';

    const typeNames = {
      research: '🔍 市場研究報告',
      proposal: '📑 行銷提案書',
      quotation: '💰 專案報價單'
    };

    for (const [type, tpl] of Object.entries(templates)) {
      html += `<div style="margin-bottom:20px;">
        <h4 style="margin-bottom:8px;">${typeNames[type] || tpl.name}</h4>`;
      tpl.sections.forEach((s, i) => {
        html += `<div class="template-section-item">
          <input value="${s.title}" data-type="${type}" data-index="${i}" data-field="title" placeholder="段落標題">
          <input value="${s.description}" data-type="${type}" data-index="${i}" data-field="description" placeholder="說明">
        </div>`;
      });
      html += `</div>`;
    }

    editor.innerHTML = html;
  } catch (err) {
    console.error('Failed to load templates:', err);
  }
}

// --- Processing ---
async function startProcessing() {
  const types = getSelectedTypes();
  if (types.length === 0) {
    showToast('請至少選擇一種文件類型', 'error');
    return;
  }

  setStep(3);
  showLoading('AI 正在分析中...');

  const typeNames = {
    research: '市場研究報告',
    proposal: '行銷提案書',
    quotation: '專案報價單'
  };

  try {
    // Update loading step for each type
    for (let i = 0; i < types.length; i++) {
      updateLoadingStep(`正在產出 ${typeNames[types[i]]}（${i + 1}/${types.length}）`);
    }

    const res = await fetch('/api/process', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: meetingText, types })
    });

    const data = await res.json();
    processResults = data;

    renderResults(data, types);
    goToStep(4);
    showToast('文件產出完成！', 'success');
  } catch (err) {
    showToast('處理失敗：' + err.message, 'error');
    goToStep(2);
  } finally {
    hideLoading();
  }
}

// --- Render Results ---
function renderResults(data, types) {
  const tabBar = document.getElementById('result-tabs');
  const contents = document.getElementById('result-contents');

  const typeNames = {
    research: '🔍 研究報告',
    proposal: '📑 提案書',
    quotation: '💰 報價單'
  };

  let tabHtml = '';
  let contentHtml = '';

  types.forEach((type, i) => {
    const result = data[type];
    const isActive = i === 0 ? 'active' : '';

    tabHtml += `<button class="result-tab ${isActive}" onclick="switchTab('${type}')" data-tab="${type}">
      ${typeNames[type] || type}
    </button>`;

    if (result.error) {
      contentHtml += `<div class="result-content ${isActive}" data-content="${type}">
        <p style="color:var(--error);">產出失敗：${result.error}</p>
      </div>`;
    } else {
      contentHtml += `<div class="result-content ${isActive}" data-content="${type}">
        <h3 style="margin-bottom:16px; font-size:1.3rem;">${result.title}</h3>
        <p style="color:var(--text-muted); margin-bottom:20px;">日期：${result.date || '-'}</p>`;

      result.sections.forEach(section => {
        contentHtml += `<div class="result-section">
          <h3>${section.title}</h3>
          <div class="content">${markdownToHtml(section.content)}</div>
        </div>`;
      });

      contentHtml += `<div class="btn-group" style="margin-top:12px;">
        <button class="btn btn-success" onclick="exportDoc('${type}')">
          📥 匯出 ${typeNames[type]} DOCX
        </button>
      </div></div>`;
    }
  });

  tabBar.innerHTML = tabHtml;
  contents.innerHTML = contentHtml;
}

function switchTab(type) {
  document.querySelectorAll('.result-tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.result-content').forEach(c => c.classList.remove('active'));
  document.querySelector(`[data-tab="${type}"]`).classList.add('active');
  document.querySelector(`[data-content="${type}"]`).classList.add('active');
}

// --- Simple Markdown to HTML ---
function markdownToHtml(text) {
  if (!text) return '';

  let html = text
    // Bold
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    // Italic
    .replace(/\*(.*?)\*/g, '<em>$1</em>')
    // Headers
    .replace(/^### (.*$)/gm, '<h4>$1</h4>')
    .replace(/^## (.*$)/gm, '<h3>$1</h3>');

  // Handle tables
  if (html.includes('|')) {
    const lines = html.split('\n');
    let inTable = false;
    let tableHtml = '';

    const processed = [];
    for (const line of lines) {
      if (line.trim().startsWith('|') && line.trim().endsWith('|')) {
        if (line.trim().match(/^\|[\s\-:|]+\|$/)) continue; // separator row
        const cells = line.trim().split('|').filter(c => c.trim());
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

  // Paragraphs
  html = html.replace(/\n\n/g, '</p><p>');
  html = html.replace(/\n/g, '<br>');

  return `<p>${html}</p>`;
}

// --- Export ---
async function exportDoc(type) {
  const result = processResults[type];
  if (!result || result.error) {
    showToast('沒有可匯出的內容', 'error');
    return;
  }

  try {
    const res = await fetch(`/api/export/${type}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(result)
    });

    const data = await res.json();
    if (data.error) {
      showToast(data.error, 'error');
      return;
    }

    // Trigger download
    window.location.href = `/api/download/${data.filename}`;
    showToast('檔案已開始下載', 'success');
  } catch (err) {
    showToast('匯出失敗：' + err.message, 'error');
  }
}

async function exportAll() {
  const types = Object.keys(processResults).filter(t => !processResults[t].error);
  for (const type of types) {
    await exportDoc(type);
    // Small delay between downloads
    await new Promise(r => setTimeout(r, 500));
  }
}

// --- Loading ---
function showLoading(msg) {
  document.getElementById('loading').classList.add('show');
  updateLoadingStep(msg || '處理中...');
}

function updateLoadingStep(msg) {
  document.getElementById('loading-step').textContent = msg;
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
