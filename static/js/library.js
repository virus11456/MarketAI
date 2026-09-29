// Persistent documents are opt-in and always tied to the authenticated owner.
const libraryKind = { '/meeting': 'meeting', '/ad-report': 'ad-report', '/work-dispatch': 'work-dispatch' }[location.pathname];
let libraryDocument = null;
let libraryResultInput = null;
let librarySaving = false;
let libraryDirty = false;
const libraryEl = id => document.getElementById(id);
const libraryCopy = value => JSON.parse(JSON.stringify(value));

async function libraryRequest(path, options = {}) {
  const response = await fetch('/api/library/' + path, {
    ...options, headers: { 'Content-Type': 'application/json', ...options.headers }
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '專案資料讀取失敗');
  return data;
}
async function libraryAll(path) {
  const items = [];
  for (let offset = 0; ; offset += 100) {
    const page = await libraryRequest(path + (path.includes('?') ? '&' : '?') + 'offset=' + offset);
    items.push(...page.items);
    if (page.items.length < 100) return items;
  }
}
function libraryOptions(select, items, placeholder) {
  select.replaceChildren(new Option(placeholder, ''));
  items.forEach(item => select.add(new Option(item.client_name ? `${item.client_name} / ${item.name}` : item.name, item.id)));
}
function libraryInput() {
  if (libraryKind === 'meeting') return { text: libraryEl('transcribed-text').value || libraryEl('meeting-text').value };
  if (libraryKind === 'ad-report') {
    const { period_confirmed, ...input } = arCollectReport();
    return input;
  }
  return { project_name: libraryEl('wd-project-name').value,
    quotation_text: libraryEl('wd-quotation-text').value,
    roles: wdRoles.filter(role => wdSelectedRoles.has(role.id)) };
}
function libraryResult() {
  if (libraryKind === 'meeting') return meetingNotes ? { notes: meetingNotes } : null;
  return libraryKind === 'ad-report' ? arReportResult : wdResult;
}
function libraryMarkGenerated() {
  if (!libraryEl('library-toolbar')) return;
  libraryResultInput = JSON.stringify(libraryInput());
  libraryDirty = true;
  libraryStatus('結果已產生，尚未儲存。');
  libraryEditors();
}
function libraryStatus(message, error = false) {
  const el = libraryEl('document-status');
  if (!el) return;
  el.textContent = message;
  el.dataset.error = String(error);
}
function librarySnapshot() {
  const input = libraryInput();
  const result = JSON.stringify(input) === libraryResultInput ? libraryResult() : null;
  return libraryCopy({ input, result });
}
function libraryRenderResult(result) {
  if (libraryKind === 'meeting') {
    meetingNotes = result?.notes || '';
    libraryEl('notes-content').innerHTML = markdownToHtml(meetingNotes);
    goToStep(result ? 3 : 2);
  } else if (libraryKind === 'ad-report') {
    arReportResult = result;
    if (result) renderAdReport(result);
    arGoToStep(result ? 4 : 1);
  } else {
    wdResult = result;
    if (result) renderDispatchResults(result);
    wdGoToStep(result ? 4 : 1);
  }
}
async function libraryRestore(record) {
  const input = record.snapshot.input;
  if (libraryKind === 'meeting') {
    meetingText = input.text || '';
    libraryEl('meeting-text').value = meetingText;
    libraryEl('transcribed-text').value = meetingText;
    libraryEl('transcribed-text-ts').value = meetingText;
    libraryEl('transcription-preview').style.display = 'block';
    libraryEl('transcript-timestamped').style.display = 'none';
    libraryEl('transcript-plain').style.display = 'block';
    libraryEl('transcript-info').textContent = '已還原儲存的逐字稿（不含音檔）';
  } else if (libraryKind === 'ad-report') {
    libraryEl('client-name').value = input.client_name || '';
    libraryEl('company-name').value = input.company_name || '';
    libraryEl('report-month').value = input.report_month || '';
    [...activePlatforms].forEach(removePlatformPanel);
    Object.entries(input.platforms || {}).forEach(([label, text]) => {
      const key = Object.hasOwn(PLATFORM_CONFIG, label) && !label.startsWith('custom_') ? label : 'custom_' + crypto.randomUUID().replaceAll('-', '');
      if (key.startsWith('custom_')) PLATFORM_CONFIG[key] = { label, icon: '📈', placeholder: '貼上廣告數據' };
      activePlatforms.add(key);
      addPlatformPanel(key);
      const panel = document.getElementById('panel-' + key);
      panel.querySelector('.platform-data').value = text;
      panel.querySelector('.platform-currency').value = input.platform_metadata?.[label]?.currency || '';
      panel.querySelector('.platform-conversion-type').value = input.platform_metadata?.[label]?.conversion_type || '';
      document.getElementById(key + '-paste').style.display = 'block';
      document.getElementById(key + '-upload').style.display = 'none';
      panel.querySelectorAll('.input-toggle button').forEach((btn, i) => btn.classList.toggle('active', i === 1));
      document.querySelector(`.platform-chip[data-platform="${key}"]`)?.classList.add('selected');
    });
    arAudit = null;
    arAuditPayload = null;
    libraryEl('ar-period-confirmed').checked = false;
    libraryEl('ar-review-confirmed').checked = false;
  } else {
    await window.wdReady;
    libraryEl('wd-project-name').value = input.project_name || '';
    wdQuotationText = input.quotation_text || '';
    libraryEl('wd-quotation-text').value = wdQuotationText;
    wdRoles = input.roles || [];
    wdSelectedRoles = new Set(wdRoles.map(role => role.id));
    renderRolesGrid();
  }
  libraryRenderResult(record.snapshot.result);
  libraryResultInput = JSON.stringify(libraryInput());
  libraryDocument = record;
  libraryEl('document-title').value = record.title;
  libraryEl('document-project').value = record.project_id;
  libraryEl('document-project').disabled = true;
  libraryStatus(`已開啟版本 ${record.version}（最新版本 ${record.latest_version}）。儲存會新增版本，不會刪除歷史。`);
  libraryEditors();
  libraryDirty = false;
}
let libraryEditFields = [];
function libraryEditors() {
  const container = libraryEl('document-edit-fields');
  if (!container) return;
  container.replaceChildren();
  libraryEditFields = [];
  const result = libraryResult();
  libraryEl('document-edit').hidden = !result;
  if (!result) return;
  const add = (label, object, key, prefix = '') => {
    const field = document.createElement('label');
    field.textContent = label;
    const input = document.createElement('textarea');
    input.className = 'library-editor'; input.value = (object[key] || '').slice(prefix.length);
    field.append(input); container.append(field);
    libraryEditFields.push({ input, object, key, prefix });
  };
  if (libraryKind === 'meeting') add('會議記錄', result, 'notes');
  else if (libraryKind === 'ad-report') {
    (result.sections || []).forEach(section => {
      const marker = '### AI 解讀（需人工覆核）\n';
      const content = section.content || '';
      const at = content.indexOf(marker);
      const prefix = at >= 0 ? content.slice(0, at + marker.length) : '';
      if (section.id?.startsWith('platform_') && !prefix) return;
      add(section.title || '段落', section, 'content', prefix);
    });
    if (result.closing) add('結語', result.closing, 'content');
  } else {
    add('專案摘要', result, 'summary');
    (result.work_packages || []).forEach(wp => add(wp.name || '工作包', wp, 'description'));
    add('時程建議', result, 'timeline_suggestion'); add('注意事項', result, 'notes');
  }
}
async function librarySave() {
  if (librarySaving) return;
  const project = libraryEl('document-project').value;
  const name = libraryEl('document-title').value.trim();
  if (!project || !name) return libraryStatus('請先選擇專案並填寫文件名稱。', true);
  librarySaving = true;
  libraryEl('document-save').disabled = true;
  libraryApplyEdits(false);
  const snapshot = librarySnapshot();
  try {
    const data = await libraryRequest('documents' + (libraryDocument ? '/' + libraryDocument.id : ''), {
      method: libraryDocument ? 'PUT' : 'POST', body: JSON.stringify({ project_id: project, title: name,
        kind: libraryKind, snapshot, expected_version: libraryDocument?.latest_version })
    });
    libraryDocument = { ...data, latest_version: data.version };
    libraryEl('document-project').disabled = true;
    history.replaceState(null, '', location.pathname + '?document=' + data.id);
    libraryDirty = JSON.stringify(librarySnapshot()) !== JSON.stringify(snapshot) || libraryEl('document-title').value.trim() !== name;
    libraryStatus(`已儲存版本 ${data.version}。${snapshot.result ? '包含目前結果。' : '已保存輸入草稿；輸入變更後需重新產生結果。'}${libraryDirty ? '儲存期間有新修改，請再儲存一次。' : ''}`);
  } catch (error) { libraryStatus(error.message, true); }
  finally { librarySaving = false; libraryEl('document-save').disabled = false; }
}
function libraryApplyEdits(render) {
  if (!libraryResult() || !libraryEditFields.length) return;
  if (JSON.stringify(libraryInput()) !== libraryResultInput) {
    if (render) libraryStatus('輸入已變更，請先重新產生結果。', true);
    return;
  }
  libraryEditFields.forEach(({ input, object, key, prefix }) => { object[key] = prefix + input.value; });
  const result = libraryKind === 'meeting' ? { notes: libraryEditFields[0].input.value } : libraryResult();
  if (render) libraryRenderResult(result);
  else if (libraryKind === 'meeting') meetingNotes = result.notes;
  libraryDirty = true;
  if (render) libraryStatus('文字修改已套用，尚未儲存。');
}
async function libraryInitToolbar() {
  libraryEl('document-save').addEventListener('click', librarySave);
  libraryEl('document-edit-apply').addEventListener('click', () => libraryApplyEdits(true));
  document.querySelector('main').addEventListener('input', event => {
    if (libraryKind === 'meeting' && ['meeting-text', 'transcribed-text'].includes(event.target.id)) {
      meetingText = event.target.value;
      libraryEl('meeting-text').value = meetingText;
      libraryEl('transcribed-text').value = meetingText;
    }
    libraryDirty = true;
    libraryStatus('有尚未儲存的修改。輸入變更後，舊結果不會與新輸入一起保存。');
  });
  window.addEventListener('beforeunload', event => { if (libraryDirty) { event.preventDefault(); event.returnValue = ''; } });
  try {
    libraryOptions(libraryEl('document-project'), await libraryAll('projects'), '請選擇專案');
    const params = new URLSearchParams(location.search);
    if (params.get('document')) {
      libraryEl('document-save').disabled = true;
      const record = await libraryRequest('documents/' + encodeURIComponent(params.get('document')) + (params.has('version') ? '?version=' + encodeURIComponent(params.get('version')) : ''));
      if (record.kind !== libraryKind) throw new Error('文件類型與目前工具不符。');
      await libraryRestore(record);
      libraryEl('document-save').disabled = false;
    }
  } catch (error) { libraryStatus(error.message, true); }
}
async function libraryInitPage() {
  const message = text => { libraryEl('library-message').textContent = text; };
  const refresh = async () => {
    libraryOptions(libraryEl('library-client'), await libraryAll('clients'), '請選擇客戶');
    libraryOptions(libraryEl('library-filter'), await libraryAll('projects'), '所有專案');
  };
  let offset = 0;
  const link = (record, version) => {
    const a = document.createElement('a');
    a.href = '/' + record.kind + '?document=' + encodeURIComponent(record.id) + (version ? '&version=' + version : '');
    a.textContent = version ? `開啟版本 ${version}` : '開啟最新版本';
    return a;
  };
  const list = async (append = false) => {
    if (!append) { offset = 0; libraryEl('library-documents').replaceChildren(); }
    const params = new URLSearchParams({ project_id: libraryEl('library-filter').value, q: libraryEl('library-query').value, offset });
    const data = await libraryRequest('documents?' + params);
    data.items.forEach(record => {
      const card = document.createElement('article'); card.className = 'library-document';
      const heading = document.createElement('h3'); heading.textContent = record.title;
      const meta = document.createElement('p'); meta.textContent = `${{ meeting: '會議記錄', 'ad-report': '廣告月報', 'work-dispatch': '工作包' }[record.kind]} · 版本 ${record.version} · ${new Date(record.updated_at).toLocaleString()}`;
      const versions = document.createElement('button'); versions.type = 'button'; versions.className = 'btn btn-secondary'; versions.textContent = '版本歷史';
      const history = document.createElement('div');
      let versionOffset = 0;
      versions.onclick = async () => {
        try {
          const page = await libraryRequest(`documents/${record.id}/versions?offset=${versionOffset}`);
          page.items.forEach(version => { const p = document.createElement('p'); p.className = 'library-version'; p.append(link(record, version.version), document.createTextNode(`${version.title} · ${new Date(version.created_at).toLocaleString()}`)); history.append(p); });
          versionOffset += page.items.length;
          versions.hidden = page.items.length < 100; versions.textContent = '更多版本';
        } catch (error) { message(error.message); }
      };
      card.append(heading, meta, link(record), versions, history); libraryEl('library-documents').append(card);
    });
    offset += data.items.length;
    libraryEl('library-more').hidden = data.items.length < 100;
    message(offset ? `已載入 ${offset} 份文件。` : '尚無文件，到工具頁儲存第一份草稿。');
  };
  libraryEl('library-client-form').onsubmit = async event => {
    event.preventDefault();
    try { await libraryRequest('clients', { method: 'POST', body: JSON.stringify({ name: libraryEl('library-client-name').value }) }); libraryEl('library-client-name').value = ''; await refresh(); message('客戶已新增。'); }
    catch (error) { message(error.message); }
  };
  libraryEl('library-project-form').onsubmit = async event => {
    event.preventDefault();
    try { await libraryRequest('projects', { method: 'POST', body: JSON.stringify({ name: libraryEl('library-project-name').value, client_id: libraryEl('library-client').value }) }); libraryEl('library-project-name').value = ''; await refresh(); message('專案已新增。'); }
    catch (error) { message(error.message); }
  };
  libraryEl('library-search').onsubmit = event => { event.preventDefault(); list().catch(error => message(error.message)); };
  libraryEl('library-more').onclick = () => list(true).catch(error => message(error.message));
  try { await refresh(); await list(); } catch (error) { message(error.message); }
}
document.addEventListener('DOMContentLoaded', () => {
  if (libraryEl('library-toolbar')) libraryInitToolbar();
  if (libraryEl('library-search')) libraryInitPage();
});
