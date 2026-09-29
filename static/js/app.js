// State
let meetingText = '';
let uploadedFilename = '';
let transcriptTimestamped = '';
let transcriptSegments = [];
let meetingNotes = '';

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

  const groqKey = (typeof getGroqKey === 'function') ? getGroqKey() : '';
  const colabUrl = document.getElementById('colab-url').value.trim();

  if (!groqKey && !colabUrl) {
    showToast('請先在右上角「API 設定」填入 Groq API Key（推薦），或在下方填入 Colab API URL', 'error');
    removeFile();
    toggleApiSettings();
    return;
  }

  const initialPrompt = document.getElementById('initial-prompt').value;

  try {
    let result;
    if (groqKey) {
      // 直接從瀏覽器呼叫 Groq，繞過 Vercel 4.5MB 請求上限（Groq 上限 25MB）
      showLoading('透過 Groq Whisper 轉錄中（large-v3），長音檔請耐心等候...');
      result = await transcribeWithGroq(file, groqKey, initialPrompt);
    } else {
      // 備援：經後端轉發到 Colab
      showLoading('透過 Google Colab GPU 轉錄中（large-v3），長音檔請耐心等候...');
      const formData = new FormData();
      formData.append('file', file);
      formData.append('initial_prompt', initialPrompt);
      formData.append('colab_url', colabUrl);
      const res = await fetch('/api/upload', { method: 'POST', body: formData });
      const raw = await res.text();
      let data;
      try { data = JSON.parse(raw); }
      catch (_) {
        throw new Error(res.status === 413
          ? '檔案太大（Colab 經後端轉發有 4.5MB 限制），建議改用 Groq'
          : `伺服器回應異常（HTTP ${res.status}）`);
      }
      if (data.error) { showToast(data.error, 'error'); removeFile(); return; }
      result = data;
    }

    meetingText = result.text;
    uploadedFilename = result.filename || file.name;
    transcriptTimestamped = result.timestamped || '';
    transcriptSegments = result.segments || [];

    // Show transcription preview
    document.getElementById('transcription-preview').style.display = 'block';
    document.getElementById('transcribed-text-ts').value = transcriptTimestamped;
    document.getElementById('transcribed-text').value = meetingText;

    const duration = transcriptSegments.length > 0
      ? Math.ceil(transcriptSegments[transcriptSegments.length - 1].end)
      : 0;
    const mm = Math.floor(duration / 60);
    const ss = duration % 60;
    document.getElementById('transcript-info').textContent =
      `${transcriptSegments.length} 段落 · 總長 ${mm} 分 ${ss} 秒`;

    setStep(2);
    showToast('語音轉文字完成！', 'success');
  } catch (err) {
    showToast('檔案上傳失敗：' + (err.message || err), 'error');
    removeFile();
  } finally {
    hideLoading();
  }
}

// 瀏覽器直接呼叫 Groq Whisper（OpenAI 相容），回傳 {text, timestamped, segments}
async function transcribeWithGroq(file, groqKey, initialPrompt) {
  const fd = new FormData();
  fd.append('file', file);
  fd.append('model', 'whisper-large-v3');
  fd.append('language', 'zh');
  fd.append('response_format', 'verbose_json');
  if (initialPrompt) fd.append('prompt', initialPrompt);

  const res = await fetch('https://api.groq.com/openai/v1/audio/transcriptions', {
    method: 'POST',
    headers: { 'Authorization': 'Bearer ' + groqKey },
    body: fd
  });

  const raw = await res.text();
  let data;
  try { data = JSON.parse(raw); }
  catch (_) { throw new Error(`Groq 回應異常（HTTP ${res.status}）`); }

  if (!res.ok) {
    const msg = (data.error && (data.error.message || data.error)) || ('HTTP ' + res.status);
    throw new Error('Groq：' + msg);
  }

  const segments = (data.segments || []).map(s => ({
    start: s.start || 0, end: s.end || 0, text: (s.text || '').trim()
  }));
  const timestamped = segments.map(s => {
    const t = Math.floor(s.start);
    const mm = String(Math.floor(t / 60)).padStart(2, '0');
    const ss = String(t % 60).padStart(2, '0');
    return `[${mm}:${ss}] ${s.text}`;
  }).join('\n');

  return { text: data.text || '', timestamped, segments };
}

function removeFile() {
  fileInput.value = '';
  uploadedFilename = '';
  meetingText = '';
  transcriptTimestamped = '';
  transcriptSegments = [];
  document.getElementById('file-info').style.display = 'none';
  document.getElementById('transcription-preview').style.display = 'none';
  setStep(1);
}

// --- Direct text input ---
function useDirectText() {
  const directText = document.getElementById('meeting-text').value.trim();
  if (!directText) {
    showToast('請先貼上逐字稿內容', 'error');
    return;
  }
  meetingText = directText;
  transcriptTimestamped = '';
  transcriptSegments = [];

  document.getElementById('transcription-preview').style.display = 'block';
  document.getElementById('transcribed-text-ts').value = directText;
  document.getElementById('transcribed-text').value = directText;
  document.getElementById('transcript-info').textContent = '已使用手動輸入的逐字稿';
  setStep(2);
  showToast('已載入逐字稿，可進行整理', 'success');
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
function downloadText(text, filename) {
  const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function downloadTranscript() {
  const text = transcriptTimestamped || meetingText;
  if (!text) {
    showToast('沒有可下載的逐字稿', 'error');
    return;
  }
  downloadText(text, '逐字稿.txt');
  showToast('逐字稿已下載', 'success');
}

// --- Organize into meeting notes (DeepSeek) ---
async function organizeNotes() {
  // Prefer the (possibly edited) plain transcript text
  const plainText = document.getElementById('transcribed-text').value.trim();
  const text = plainText || meetingText;
  if (!text) {
    showToast('沒有可整理的逐字稿', 'error');
    return;
  }
  if (!getDeepSeekKey()) {
    showToast('請先點右上角「API 設定」填入 DeepSeek API Key', 'error');
    toggleApiSettings();
    return;
  }

  showLoading('DeepSeek 正在整理會議記錄...');
  try {
    const res = await fetch('/api/meeting/organize', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, api_key: getDeepSeekKey() })
    });
    const data = await res.json();
    if (data.error) {
      showToast(data.error, 'error');
      return;
    }
    meetingNotes = data.notes;
    if (typeof libraryMarkGenerated === 'function') libraryMarkGenerated();
    document.getElementById('notes-content').innerHTML = markdownToHtml(meetingNotes);
    goToStep(3);
    showToast('會議記錄整理完成！', 'success');
  } catch (err) {
    showToast('整理失敗：' + err.message, 'error');
  } finally {
    hideLoading();
  }
}

function downloadNotesTxt() {
  if (!meetingNotes) {
    showToast('沒有可下載的會議記錄', 'error');
    return;
  }
  downloadText(meetingNotes, '會議記錄.txt');
  showToast('會議記錄已下載', 'success');
}

async function exportNotesDocx() {
  if (!meetingNotes) {
    showToast('沒有可匯出的會議記錄', 'error');
    return;
  }
  showLoading('正在產生 DOCX...');
  try {
    const res = await fetch('/api/meeting/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ notes: meetingNotes, title: '會議記錄' })
    });
    await downloadDocxResponse(res, '會議記錄.docx');
    showToast('DOCX 已開始下載', 'success');
  } catch (err) {
    showToast('匯出失敗：' + err.message, 'error');
  } finally {
    hideLoading();
  }
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

  if (num === 3) {
    document.getElementById('section-notes').classList.add('active');
  } else {
    document.getElementById('section-upload').classList.add('active');
  }
}

// --- Simple Markdown to HTML ---
function markdownToHtml(text) {
  if (!text) return '';

  let html = text
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    .replace(/^#### (.*$)/gm, '<h4>$1</h4>')
    .replace(/^### (.*$)/gm, '<h3>$1</h3>')
    .replace(/^## (.*$)/gm, '<h2>$1</h2>')
    .replace(/^# (.*$)/gm, '<h1>$1</h1>');

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

  html = html.replace(/^[-*] (.*$)/gm, '<li>$1</li>');
  html = html.replace(/(<li>.*<\/li>\n?)+/g, '<ul>$&</ul>');
  html = html.replace(/\n{2,}/g, '</p><p>');
  html = html.replace(/\n/g, '<br>');

  return `<p>${html}</p>`;
}

// --- Loading helpers (fallback if base.html doesn't define) ---
function updateLoadingStep(msg) {
  const el = document.getElementById('loading-step');
  if (el) el.textContent = msg;
}

// --- Open Colab in a popup window (not a new tab; stays beside the app) ---
const COLAB_NOTEBOOK_URL = 'https://colab.research.google.com/drive/1WEF7aEny9olSGP34H9Voc03aC1WJ9Wpn';
function openColabPopup() {
  const w = Math.min(1100, Math.floor(window.screen.availWidth * 0.7));
  const h = Math.min(900, Math.floor(window.screen.availHeight * 0.85));
  const left = window.screenX + (window.outerWidth - w) / 2;
  const top = window.screenY + (window.outerHeight - h) / 2;
  const popup = window.open(
    COLAB_NOTEBOOK_URL,
    'colab_whisper',
    `popup=yes,width=${w},height=${h},left=${left},top=${top},resizable=yes,scrollbars=yes`
  );
  if (!popup) {
    // Popup blocked — fall back to a new tab
    window.open(COLAB_NOTEBOOK_URL, '_blank', 'noopener');
    showToast('瀏覽器擋了彈出視窗，已改用新分頁開啟', 'error');
  } else {
    popup.focus();
  }
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
