const dom = {
  copyJsonButton: document.getElementById('copyJsonButton'),
  copyCookieButton: document.getElementById('copyCookieButton'),
  status: document.getElementById('status'),
  output: document.getElementById('output'),
};

function setStatus(message, tone = '') {
  dom.status.className = `status ${tone}`.trim();
  dom.status.textContent = message;
}

async function buildPayload() {
  const response = await chrome.runtime.sendMessage({ type: 'fanqie-export-payload' });
  if (!response?.ok || !response.payload) {
    throw new Error(response?.error || '导出失败');
  }
  return response.payload;
}

async function copyText(text) {
  await navigator.clipboard.writeText(text);
}

async function exportJson() {
  setStatus('正在读取 Cookie…');
  const payload = await buildPayload();
  const text = JSON.stringify(payload, null, 2);
  dom.output.value = text;
  await copyText(text);
  setStatus('JSON 已复制，直接粘贴到下载器即可', 'ok');
}

async function exportCookieHeader() {
  setStatus('正在读取 Cookie…');
  const payload = await buildPayload();
  dom.output.value = payload.cookieHeader;
  await copyText(payload.cookieHeader);
  setStatus('原始 Cookie 已复制', 'ok');
}

dom.copyJsonButton.addEventListener('click', () => {
  exportJson().catch((error) => {
    setStatus(error.message, 'bad');
    dom.output.value = '';
  });
});

dom.copyCookieButton.addEventListener('click', () => {
  exportCookieHeader().catch((error) => {
    setStatus(error.message, 'bad');
    dom.output.value = '';
  });
});
