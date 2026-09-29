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
