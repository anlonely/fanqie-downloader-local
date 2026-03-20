const PAGE_SOURCE = 'local-fanqie-console';
const EXTENSION_SOURCE = 'fanqie-cookie-exporter';

window.addEventListener('message', async (event) => {
  if (event.source !== window) return;
  const data = event.data || {};
  if (data.source !== PAGE_SOURCE || !data.requestId) return;

  try {
    const response = await chrome.runtime.sendMessage({
      type: data.type,
      payload: data.payload || {},
    });
    window.postMessage(
      {
        source: EXTENSION_SOURCE,
        requestId: data.requestId,
        ok: Boolean(response?.ok),
        response,
      },
      '*',
    );
  } catch (error) {
    window.postMessage(
      {
        source: EXTENSION_SOURCE,
        requestId: data.requestId,
        ok: false,
        response: {
          ok: false,
          error: error?.message || '插件通信失败',
          code: 'BRIDGE_ERROR',
        },
      },
      '*',
    );
  }
});

window.postMessage(
  {
    source: EXTENSION_SOURCE,
    type: 'fanqie-extension-ready',
  },
  '*',
);
