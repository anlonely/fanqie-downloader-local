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

async function getActiveTab() {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  return tabs[0] || null;
}

async function getFanqieCookies() {
  const cookies = await chrome.cookies.getAll({ domain: 'fanqienovel.com' });
  cookies.sort((a, b) => a.name.localeCompare(b.name));
  return cookies;
}

function buildCookieHeader(cookies) {
  return cookies.map((cookie) => `${cookie.name}=${cookie.value}`).join('; ');
}

async function buildPayload() {
  const tab = await getActiveTab();
  if (!tab || !tab.url || !tab.url.startsWith('https://fanqienovel.com/')) {
    throw new Error('请先切到番茄网页标签页再导出');
  }

  const cookies = await getFanqieCookies();
  if (!cookies.length) {
    throw new Error('没有读取到 fanqienovel.com Cookie，请确认你已在该网站登录');
  }

  return {
    source: 'chrome-extension',
    pageUrl: tab.url,
    title: tab.title || '',
    exportedAt: new Date().toISOString(),
    userAgent: navigator.userAgent,
    cookieNames: cookies.map((cookie) => cookie.name),
    cookieHeader: buildCookieHeader(cookies),
  };
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
