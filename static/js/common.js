// Treat model output and user input as text, including in HTML attributes.
function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[char]));
}

async function downloadDocxResponse(response, filename) {
  if (!response.ok) {
    let message = `匯出失敗（HTTP ${response.status}）`;
    try { message = (await response.json()).error || message; } catch (_) {}
    throw new Error(message);
  }
  if (!(response.headers.get('Content-Type') || '').includes('application/vnd.openxmlformats-officedocument.wordprocessingml.document')) {
    throw new Error('伺服器未回傳 Word 文件，請重新整理後再試。');
  }
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60000);
}


function marketaiAuthEnabled() {
  return document.querySelector('meta[name="marketai-auth-enabled"]')?.content === 'true';
}
function marketaiKeyStorage() { return marketaiAuthEnabled() ? sessionStorage : localStorage; }
function marketaiKeyName(provider) {
  const user = document.querySelector('meta[name="marketai-user"]')?.content;
  return marketaiAuthEnabled() ? `marketai_${user}_${provider}_key` : `marketai_${provider}_key`;
}

if (marketaiAuthEnabled()) {
  // Never inherit keys saved by an earlier unauthenticated/shared-browser user.
  localStorage.removeItem('marketai_deepseek_key');
  localStorage.removeItem('marketai_groq_key');
  window.addEventListener('storage', event => {
    if (event.key !== 'marketai_logout_event' || !event.newValue) return;
    for (let i = sessionStorage.length - 1; i >= 0; i--) {
      const key = sessionStorage.key(i);
      if (key.startsWith('marketai_')) sessionStorage.removeItem(key);
    }
    window.location.assign('/login');
  });
  // A shared wrapper protects every existing POST, including multipart uploads,
  // without adding the CSRF token to Groq or other cross-origin requests.
  const originalFetch = window.fetch.bind(window);
  window.fetch = async (input, init = {}) => {
    const target = new URL(input instanceof Request ? input.url : input, window.location.href);
    const method = (init.method || (input instanceof Request ? input.method : 'GET')).toUpperCase();
    if (target.origin === window.location.origin && target.pathname.startsWith('/api/')) {
      if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
        const headers = new Headers(init.headers || (input instanceof Request ? input.headers : undefined));
        headers.set('X-CSRF-Token', document.querySelector('meta[name="csrf-token"]')?.content || '');
        init = { ...init, headers };
      }
      const response = await originalFetch(input, init);
      if (response.status === 401 && !document.getElementById('marketai-login-notice')) {
        const notice = document.createElement('p');
        notice.id = 'marketai-login-notice';
        notice.setAttribute('role', 'alert');
        notice.textContent = '登入已過期。請先保存目前文字，再重新登入。 ';
        const link = document.createElement('a');
        link.href = '/login';
        link.textContent = '重新登入';
        notice.append(link);
        document.querySelector('main')?.prepend(notice);
      }
      return response;
    }
    return originalFetch(input, init);
  };
}
