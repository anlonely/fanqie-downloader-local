function buildCookieHeader(cookies) {
  return cookies.map((cookie) => `${cookie.name}=${cookie.value}`).join('; ');
}

const LOCAL_CONSOLE_PATTERNS = ['http://127.0.0.1:18930/*', 'http://localhost:18930/*'];

async function injectBridgeIntoLocalTabs() {
  const tabs = await chrome.tabs.query({ url: LOCAL_CONSOLE_PATTERNS });
  await Promise.all(
    tabs
      .filter((tab) => typeof tab.id === 'number')
      .map((tab) =>
        chrome.scripting.executeScript({
          target: { tabId: tab.id },
          files: ['bridge.js'],
        }).catch(() => null)
      )
  );
}

async function getFanqieCookies() {
  const cookies = await chrome.cookies.getAll({ domain: 'fanqienovel.com' });
  cookies.sort((a, b) => a.name.localeCompare(b.name));
  return cookies;
}

async function getPreferredFanqieTab() {
  const tabs = await chrome.tabs.query({ url: ['https://fanqienovel.com/*'] });
  if (!tabs.length) {
    const error = new Error('未找到已打开的番茄网页，请先点击“打开番茄登录页”并完成登录');
    error.code = 'NO_FANQIE_TAB';
    throw error;
  }

  tabs.sort((left, right) => {
    const activeDelta = Number(Boolean(right.active)) - Number(Boolean(left.active));
    if (activeDelta) return activeDelta;
    return Number(right.lastAccessed || 0) - Number(left.lastAccessed || 0);
  });
  return tabs[0];
}

async function inspectFanqieTab(tabId) {
  const results = await chrome.scripting.executeScript({
    target: { tabId },
    func: () => {
      const state = window.__INITIAL_STATE__ || {};
      const common = state.common || {};
      return {
        pageUrl: location.href,
        title: document.title || '',
        userAgent: navigator.userAgent,
        hasAuthentication: Boolean(common.hasAuthentication),
        userName: common.name ? String(common.name) : '',
        avatar: common.avatar ? String(common.avatar) : '',
      };
    },
  });
  return results[0]?.result || null;
}

async function buildPayload({ requireAuthenticated = true } = {}) {
  const tab = await getPreferredFanqieTab();
  const info = await inspectFanqieTab(tab.id);
  if (!info) {
    const error = new Error('无法读取番茄页面状态，请刷新番茄网页后重试');
    error.code = 'TAB_INSPECT_FAILED';
    throw error;
  }

  if (requireAuthenticated && !info.hasAuthentication) {
    const error = new Error('已找到番茄网页，但当前页面仍未登录。请先确认右上角出现头像或昵称，再回到下载器点击“同步登录态”');
    error.code = 'NOT_LOGGED_IN';
    throw error;
  }

  const cookies = await getFanqieCookies();
  if (!cookies.length) {
    const error = new Error('没有读取到 fanqienovel.com Cookie，请先登录番茄网页');
    error.code = 'NO_COOKIES';
    throw error;
  }

  return {
    source: 'chrome-extension',
    pageUrl: info.pageUrl,
    title: info.title,
    exportedAt: new Date().toISOString(),
    userAgent: info.userAgent,
    cookieNames: cookies.map((cookie) => cookie.name),
    cookieHeader: buildCookieHeader(cookies),
    loginState: {
      hasAuthentication: info.hasAuthentication,
      userName: info.userName,
      avatar: info.avatar,
    },
  };
}

async function handleMessage(message) {
  switch (message?.type) {
    case 'fanqie-bridge-ping':
      return {
        ok: true,
        installed: true,
        version: chrome.runtime.getManifest().version,
      };
    case 'fanqie-export-payload':
      return {
        ok: true,
        payload: await buildPayload({ requireAuthenticated: true }),
      };
    default:
      return {
        ok: false,
        error: '未知请求',
        code: 'UNKNOWN_MESSAGE',
      };
  }
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  handleMessage(message)
    .then((payload) => sendResponse(payload))
    .catch((error) => {
      sendResponse({
        ok: false,
        error: error?.message || '插件请求失败',
        code: error?.code || 'UNKNOWN_ERROR',
      });
    });
  return true;
});

chrome.runtime.onInstalled.addListener(() => {
  void injectBridgeIntoLocalTabs();
});

chrome.runtime.onStartup.addListener(() => {
  void injectBridgeIntoLocalTabs();
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  const url = String(tab.url || '');
  if (changeInfo.status !== 'complete') return;
  if (!url.startsWith('http://127.0.0.1:18930/') && !url.startsWith('http://localhost:18930/')) return;
  void chrome.scripting.executeScript({
    target: { tabId },
    files: ['bridge.js'],
  }).catch(() => null);
});
