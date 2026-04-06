// State
let meetingText = '';
let uploadedFilename = '';
let processResults = {};
let transcriptTimestamped = '';
let transcriptSegments = [];

// --- Input Toggle ---
function toggleInput(mode) {
  const toggle = event.target.closest('.input-toggle');
  toggle.querySelectorAll('button').forEach(b => b.classList.remove('active'));
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

  // Add Whisper settings for audio files
  const audioExts = ['.mp3', '.wav', '.m4a', '.ogg', '.webm', '.mp4'];
  const ext = '.' + file.name.split('.').pop().toLowerCase();
  const isAudio = audioExts.includes(ext);

  if (isAudio) {
    const colabUrl = document.getElementById('colab-url').value.trim();
    const promptInput = document.getElementById('initial-prompt');
    formData.append('initial_prompt', promptInput.value);

    if (colabUrl) {
      formData.append('colab_url', colabUrl);
      showLoading('透過 Google Colab GPU 轉錄中（large-v3）...');
    } else {
      const modelSelect = document.getElementById('whisper-model');
      formData.append('whisper_model', modelSelect.value);
      showLoading(`使用本機 Whisper ${modelSelect.value} 模型轉錄中...`);
    }
  } else {
    showLoading('正在處理檔案...');
  }

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

    if (data.is_audio) {
      transcriptTimestamped = data.timestamped || '';
      transcriptSegments = data.segments || [];

      // Show transcription preview
      document.getElementById('transcription-preview').style.display = 'block';
      document.getElementById('transcribed-text-ts').value = transcriptTimestamped;
      document.getElementById('transcribed-text').value = meetingText;

      // Show segment count
      const duration = transcriptSegments.length > 0
        ? Math.ceil(transcriptSegments[transcriptSegments.length - 1].end)
        : 0;
      const mm = Math.floor(duration / 60);
      const ss = duration % 60;
      document.getElementById('transcript-info').textContent =
        `${transcriptSegments.length} 段落 · 總長 ${mm} 分 ${ss} 秒`;

      showToast('語音轉文字完成！', 'success');
    } else {
      showToast('檔案處理完成！', 'success');
    }
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
  transcriptTimestamped = '';
  transcriptSegments = [];
  document.getElementById('file-info').style.display = 'none';
  document.getElementById('transcription-preview').style.display = 'none';
}

// --- Transcript View Toggle ---
function switchTranscriptView(mode) {
  const toggle = event.target.closest('.input-toggle');
  toggle.querySelectorAll('button').forEach(b => b.classList.remove('active'));
  event.target.classList.add('active');

  document.getElementById('transcript-timestamped').style.display = mode === 'timestamped' ? 'block' : 'none';
  document.getElementById('transcript-plain').style.display = mode === 'plain' ? 'block' : 'none';
}

// --- Download Transcript ---
function downloadTranscript() {
  const text = transcriptTimestamped || meetingText;
  if (!text) {
    showToast('沒有可下載的逐字稿', 'error');
    return;
  }

  const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = '逐字稿.txt';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
  showToast('逐字稿已下載', 'success');
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
  const plainText = document.getElementById('transcribed-text').value.trim();

  if (plainText) {
    meetingText = plainText;
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
        html += `<div style="display:flex;gap:12px;margin-bottom:8px;">
          <input style="flex:1;padding:8px 12px;border:1px solid var(--border);border-radius:6px;font-size:0.88rem;" value="${s.title}" data-type="${type}" data-index="${i}" data-field="title" placeholder="段落標題">
          <input style="flex:2;padding:8px 12px;border:1px solid var(--border);border-radius:6px;font-size:0.88rem;" value="${s.description}" data-type="${type}" data-index="${i}" data-field="description" placeholder="說明">
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
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.*?)\*/g, '<em>$1</em>')
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
        if (line.trim().match(/^\|[\s\-:|]+\|$/)) continue;
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

  html = html.replace(/^- (.*$)/gm, '<li>$1</li>');
  html = html.replace(/(<li>.*<\/li>\n?)+/g, '<ul>$&</ul>');

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
    await new Promise(r => setTimeout(r, 500));
  }
}

// --- Loading (use global from base.html if available, fallback) ---
function updateLoadingStep(msg) {
  document.getElementById('loading-step').textContent = msg;
}

// --- Colab Connection Test ---
async function testColabConnection() {
  const url = document.getElementById('colab-url').value.trim();
  const statusEl = document.getElementById('colab-status');
  const btn = document.getElementById('btn-test-colab');

  if (!url) {
    showToast('請輸入 Colab API URL', 'error');
    return;
  }

  btn.disabled = true;
  btn.textContent = '測試中...';
  statusEl.className = 'colab-status';
  statusEl.style.display = 'none';

  try {
    const res = await fetch('/api/colab-health', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url })
    });

    const data = await res.json();

    if (data.error) {
      statusEl.className = 'colab-status error';
      statusEl.textContent = '連線失敗：' + data.error;
    } else {
      statusEl.className = 'colab-status success';
      statusEl.textContent = `連線成功！模型：${data.model} · GPU：${data.gpu}`;
      // Save to localStorage
      localStorage.setItem('marketai_colab_url', url);
    }
  } catch (err) {
    statusEl.className = 'colab-status error';
    statusEl.textContent = '連線失敗：' + err.message;
  } finally {
    btn.disabled = false;
    btn.textContent = '測試連線';
  }
}

// --- Init: restore saved Colab URL ---
document.addEventListener('DOMContentLoaded', () => {
  const savedUrl = localStorage.getItem('marketai_colab_url');
  if (savedUrl) {
    const input = document.getElementById('colab-url');
    if (input) input.value = savedUrl;
  }
});
