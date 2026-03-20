const dom = {
  noticeBar: document.getElementById('noticeBar'),
  heroOutputDir: document.getElementById('heroOutputDir'),
  heroSessionState: document.getElementById('heroSessionState'),
  sessionSummary: document.getElementById('sessionSummary'),
  syncSessionButton: document.getElementById('syncSessionButton'),
  cookieInput: document.getElementById('cookieInput'),
  saveCookieButton: document.getElementById('saveCookieButton'),
  clearCookieButton: document.getElementById('clearCookieButton'),
  openLoginButton: document.getElementById('openLoginButton'),
  refreshSessionButton: document.getElementById('refreshSessionButton'),
  targetInput: document.getElementById('targetInput'),
  outputDirInput: document.getElementById('outputDirInput'),
  previewButton: document.getElementById('previewButton'),
  downloadButton: document.getElementById('downloadButton'),
  detailPanel: document.getElementById('detailPanel'),
  jobsPanel: document.getElementById('jobsPanel'),
  queueSummary: document.getElementById('queueSummary'),
};

const state = {
  config: null,
  detail: null,
  jobs: [],
  noticeTimer: null,
  extensionReady: false,
};

const JOB_STATUS_LABELS = {
  queued: '排队中',
  running: '下载中',
  paused: '已暂停',
  completed: '已完成',
  failed: '失败',
  canceled: '已取消',
  canceling: '取消中',
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    method: options.method || 'GET',
    headers: {
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    },
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  const payload = await response.json();
  if (!response.ok || !payload.ok) {
    throw new Error(payload.error || `请求失败: ${response.status}`);
  }
  return payload.data;
}

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function showNotice(message, tone = 'info') {
  if (!dom.noticeBar) return;
  dom.noticeBar.textContent = message;
  dom.noticeBar.className = `notice-bar ${tone}`;
  if (state.noticeTimer) {
    clearTimeout(state.noticeTimer);
  }
  state.noticeTimer = setTimeout(() => {
    dom.noticeBar.className = 'notice-bar hidden';
    dom.noticeBar.textContent = '';
  }, 3200);
}

function sessionTone(session) {
  const validation = session?.validation || {};
  if (!session?.configured) return 'idle';
  if (validation.valid) return 'good';
  if (validation.state === 'unauthenticated') return 'warn';
  if (validation.state === 'expired') return 'bad';
  return 'warn';
}

function sessionStateLabel(session) {
  const validation = session?.validation || {};
  if (validation.valid) return '登录态有效';
  if (!session?.configured) return '尚未登录';
  if (validation.state === 'unauthenticated') return '未检测到番茄登录态';
  return '登录态失效';
}

function importSourceLabel(source) {
  const normalized = String(source || '').trim();
  if (normalized === 'local-chrome') return '本机 Chrome';
  if (normalized === 'chrome-extension') return 'Cookie 插件';
  return normalized || '--';
}

function renderSession() {
  const session = state.config?.session;
  if (!session) {
    dom.sessionSummary.textContent = '会话状态未知';
    return;
  }

  const validation = session.validation || {};
  const names = (session.cookie_names || []).join(', ') || '无';
  const tone = sessionTone(session);
  const userName = validation.user_name || '未识别出登录用户';
  const message = validation.message || '未保存登录态';
  const lastImport = session.last_import || {};
  const profilePath = lastImport.profile_path || '--';

  dom.heroOutputDir.textContent = state.config?.default_output_dir || '--';
  dom.heroSessionState.textContent = validation.valid ? `已登录：${userName}` : message;
  dom.heroSessionState.className = `hero-chip-value ${tone}`;

  dom.sessionSummary.className = `session-summary ${tone}`;
  dom.sessionSummary.innerHTML = `
    <div class="session-topline">
      <div>
        <div class="session-state">${sessionStateLabel(session)}</div>
        <div class="session-note">${escapeHtml(message)}</div>
      </div>
      <div class="session-user">${escapeHtml(userName)}</div>
    </div>
    <div class="session-grid">
      <div class="session-cell">
        <span class="session-label">Cookie 名称</span>
        <span class="session-value">${escapeHtml(names)}</span>
      </div>
      <div class="session-cell">
        <span class="session-label">保存位置</span>
        <span class="session-value break">${escapeHtml(session.session_file || '--')}</span>
      </div>
      <div class="session-cell">
        <span class="session-label">同步来源</span>
        <span class="session-value">${escapeHtml(importSourceLabel(lastImport.source))}</span>
      </div>
      <div class="session-cell">
        <span class="session-label">最近校验</span>
        <span class="session-value">${escapeHtml(validation.checked_at || '--')}</span>
      </div>
      <div class="session-cell session-wide">
        <span class="session-label">Chrome Profile</span>
        <span class="session-value break">${escapeHtml(profilePath)}</span>
      </div>
      <div class="session-cell session-wide">
        <span class="session-label">来源页面</span>
        <span class="session-value break">${escapeHtml(lastImport.page_url || '--')}</span>
      </div>
    </div>
  `;
}

function extensionRequest(type, payload = {}, timeoutMs = 1800) {
  const requestId = `fanqie-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      window.removeEventListener('message', onMessage);
      reject(new Error('未检测到 Cookie 插件，请先安装插件并刷新本页面'));
    }, timeoutMs);

    function onMessage(event) {
      const data = event.data || {};
      if (data.source !== 'fanqie-cookie-exporter' || data.requestId !== requestId) return;
      clearTimeout(timer);
      window.removeEventListener('message', onMessage);
      if (!data.ok || !data.response?.ok) {
        const error = new Error(data.response?.error || '插件返回失败');
        error.code = data.response?.code || 'EXTENSION_ERROR';
        reject(error);
        return;
      }
      resolve(data.response);
    }

    window.addEventListener('message', onMessage);
    window.postMessage(
      {
        source: 'local-fanqie-console',
        requestId,
        type,
        payload,
      },
      '*',
    );
  });
}

async function probeExtension() {
  try {
    await extensionRequest('fanqie-bridge-ping', {}, 1000);
    state.extensionReady = true;
  } catch {
    state.extensionReady = false;
  }
}

function jobStatusLabel(status) {
  return JOB_STATUS_LABELS[status] || status || '--';
}

function renderDetail() {
  const detail = state.detail;
  if (!detail) {
    dom.detailPanel.className = 'detail-empty';
    dom.detailPanel.textContent = '输入书籍链接后点击“加载详情”';
    return;
  }

  const chapters = detail.chapters || [];
  const previewList = chapters.slice(0, 10).map((chapter) => `
    <li class="chapter-row">
      <span>${escapeHtml(chapter.title)}</span>
      <span class="chapter-chip ${chapter.locked || chapter.need_pay ? 'locked' : 'open'}">${chapter.locked || chapter.need_pay ? '受限' : '公开'}</span>
    </li>
  `).join('');

  dom.detailPanel.className = 'detail-card';
  dom.detailPanel.innerHTML = `
    <div class="detail-header">
      <div>
        <div class="detail-title">${escapeHtml(detail.book_name)}</div>
        <div class="detail-meta">${escapeHtml(detail.author)} / ${escapeHtml(detail.chapter_total)} 章 / ${escapeHtml(detail.status || '--')}</div>
      </div>
      ${detail.thumb_url ? `<img class="detail-cover" src="${escapeHtml(detail.thumb_url)}" alt="封面">` : ''}
    </div>
    <p class="detail-abstract">${escapeHtml(detail.abstract || '暂无简介')}</p>
    <div class="detail-subtitle">章节抽样</div>
    <ol class="chapter-list">${previewList}</ol>
  `;
}

function jobTone(job) {
  if (job.status === 'completed') return 'good';
  if (job.status === 'failed' || job.status === 'canceled') return 'bad';
  if (job.status === 'paused') return 'warn';
  return 'active';
}

function progressPercent(job) {
  if (!job.progress_total) return 0;
  return Math.max(0, Math.min(100, Math.round((job.progress_current / job.progress_total) * 100)));
}

function queueSummaryText() {
  const queued = state.jobs.filter((job) => job.status === 'queued').length;
  const running = state.jobs.filter((job) => job.status === 'running').length;
  const paused = state.jobs.filter((job) => job.status === 'paused').length;
  return `${state.jobs.length} 个任务 / 运行 ${running} / 排队 ${queued} / 暂停 ${paused}`;
}

function renderJobs() {
  dom.queueSummary.textContent = queueSummaryText();
  if (!state.jobs.length) {
    dom.jobsPanel.className = 'jobs-empty';
    dom.jobsPanel.textContent = '暂无任务';
    return;
  }

  dom.jobsPanel.className = 'jobs-list';
  dom.jobsPanel.innerHTML = state.jobs.map((job) => {
    const tone = jobTone(job);
    const percent = progressPercent(job);
    const actionButtons = [];
    if (job.can_pause) {
      actionButtons.push(`<button data-action="pause" data-job-id="${job.job_id}" class="ghost small">暂停</button>`);
    }
    if (job.can_resume) {
      actionButtons.push(`<button data-action="resume" data-job-id="${job.job_id}" class="ghost small">继续</button>`);
    }
    if (job.can_pin) {
      actionButtons.push(`<button data-action="pin" data-job-id="${job.job_id}" class="ghost small">${job.pinned ? '取消置顶' : '置顶'}</button>`);
    }
    if (job.can_delete) {
      actionButtons.push(`<button data-action="delete" data-job-id="${job.job_id}" class="ghost small danger">删除</button>`);
    }
    if (job.can_open_file) {
      actionButtons.push(`<button data-action="open-file" data-job-id="${job.job_id}" class="ghost small">打开文件</button>`);
    }
    if (job.can_open_folder) {
      actionButtons.push(`<button data-action="open-folder" data-job-id="${job.job_id}" class="ghost small">打开所在目录</button>`);
    }

    return `
      <article class="job-card ${tone}">
        <div class="job-card-top">
          <div>
            <div class="job-title-row">
              <span class="job-title">${escapeHtml(job.book_name || job.target)}</span>
              ${job.pinned ? '<span class="job-pin">置顶</span>' : ''}
            </div>
            <div class="job-meta">${escapeHtml(jobStatusLabel(job.status))} / ${escapeHtml(job.updated_at)}</div>
          </div>
          <div class="job-progress-label">${percent}%</div>
        </div>
        <div class="progress-track"><span class="progress-fill" style="width:${percent}%"></span></div>
        <div class="job-message">${escapeHtml(job.message || '--')}</div>
        ${job.result_path ? `<div class="job-path">${escapeHtml(job.result_path)}</div>` : ''}
        ${job.error ? `<div class="job-error">${escapeHtml(job.error)}</div>` : ''}
        ${(job.failures || []).length ? `<div class="job-error">失败章节：${escapeHtml((job.failures || []).length)}</div>` : ''}
        <div class="button-row compact actions">${actionButtons.join('')}</div>
      </article>
    `;
  }).join('');
}

async function loadConfig() {
  state.config = await api('/api/config');
  state.jobs = state.config.jobs || [];
  dom.outputDirInput.value = state.config.default_output_dir || '';
  renderSession();
  renderJobs();
}

async function refreshJobs() {
  state.jobs = await api('/api/jobs');
  renderJobs();
}

async function refreshSession(force = false) {
  if (force) {
    state.config = { ...(state.config || {}), session: await api('/api/session/status') };
  } else {
    const config = await api('/api/config');
    state.config = { ...(state.config || {}), session: config.session, default_output_dir: config.default_output_dir };
    if (!dom.outputDirInput.value.trim()) {
      dom.outputDirInput.value = config.default_output_dir || '';
    }
  }
  renderSession();
}

async function handlePreview() {
  const target = dom.targetInput.value.trim();
  if (!target) {
    alert('请输入书籍链接、章节链接或对应 ID');
    return;
  }
  dom.detailPanel.className = 'detail-loading';
  dom.detailPanel.textContent = '正在解析书籍信息…';
  state.detail = await api('/api/book', {
    method: 'POST',
    body: { target },
  });
  renderDetail();
  showNotice(`已加载《${state.detail.book_name || '书籍'}》详情`, 'success');
}

async function handleDownload() {
  const target = dom.targetInput.value.trim();
  if (!target) {
    alert('请输入书籍链接、章节链接或对应 ID');
    return;
  }
  const outputDir = dom.outputDirInput.value.trim();
  const job = await api('/api/download', {
    method: 'POST',
    body: {
      target,
      output_dir: outputDir,
    },
  });
  state.jobs = [job, ...state.jobs];
  renderJobs();
  showNotice('任务已加入下载队列', 'success');
}

async function handleSaveCookie() {
  const cookie = dom.cookieInput.value.trim();
  if (!cookie) {
    alert('请先粘贴 Cookie 或扩展导出的 JSON');
    return;
  }
  state.config = { ...(state.config || {}), session: await api('/api/session/save-cookie', { method: 'POST', body: { cookie } }) };
  dom.cookieInput.value = '';
  renderSession();
  showNotice('登录态已保存并完成校验', 'success');
}

async function handleClearCookie() {
  state.config = { ...(state.config || {}), session: await api('/api/session/clear', { method: 'POST', body: {} }) };
  renderSession();
  showNotice('已清空本地登录态', 'info');
}

async function handleOpenLogin() {
  const payload = await api('/api/session/open-login', { method: 'POST', body: {} });
  showNotice(payload.message, 'info');
}

async function handleSyncSession() {
  try {
    const extension = await extensionRequest('fanqie-export-payload');
    state.extensionReady = true;
    state.config = {
      ...(state.config || {}),
      session: await api('/api/session/save-cookie', {
        method: 'POST',
        body: { cookie: JSON.stringify(extension.payload) },
      }),
    };
    renderSession();
    showNotice('已从插件同步登录态', 'success');
  } catch (error) {
    try {
      state.config = {
        ...(state.config || {}),
        session: await api('/api/session/sync-chrome'),
      };
      renderSession();
      showNotice('插件桥接未响应，已直接从本机 Chrome 同步登录态', 'success');
      return;
    } catch (nativeError) {
      const code = error.code || '';
      if (code === 'NO_FANQIE_TAB') {
        showNotice('未找到已打开的番茄网页，请先点“打开番茄登录页”，登录后再同步', 'error');
        return;
      }
      if (code === 'NOT_LOGGED_IN') {
        showNotice('插件已连接，但当前番茄页面仍未登录。请先登录番茄，再点“同步登录态”', 'error');
        return;
      }
      showNotice(nativeError.message || error.message || '同步登录态失败', 'error');
    }
  }
}

async function handleJobAction(action, jobId) {
  const data = await api(`/api/jobs/${jobId}/${action}`, { method: 'POST', body: {} });
  if (action === 'delete' && data.deleted) {
    state.jobs = state.jobs.filter((job) => job.job_id !== jobId);
    showNotice('任务已删除', 'info');
  } else if (data.job_id) {
    state.jobs = state.jobs.map((job) => (job.job_id === jobId ? data : job));
  } else if (data.job) {
    state.jobs = state.jobs.map((job) => (job.job_id === jobId ? data.job : job));
  }
  renderJobs();
  if (action !== 'delete') {
    const labels = {
      pause: '任务已暂停',
      resume: '任务已继续',
      pin: '任务顺序已更新',
      'open-file': '已打开文件',
      'open-folder': '已在 Finder 中定位文件',
    };
    showNotice(labels[action] || '任务状态已更新', 'success');
  }
}

async function bootstrap() {
  await probeExtension();
  await loadConfig();
  setInterval(() => {
    refreshJobs().catch((error) => console.error(error));
  }, 2000);
  setInterval(() => {
    refreshSession(true).catch((error) => console.error(error));
  }, 30000);
}

dom.previewButton.addEventListener('click', () => {
  handlePreview().catch((error) => {
    showNotice(error.message, 'error');
    dom.detailPanel.className = 'detail-empty';
    dom.detailPanel.textContent = error.message;
  });
});
dom.downloadButton.addEventListener('click', () => {
  handleDownload().catch((error) => showNotice(error.message, 'error'));
});
dom.saveCookieButton.addEventListener('click', () => {
  handleSaveCookie().catch((error) => showNotice(error.message, 'error'));
});
dom.clearCookieButton.addEventListener('click', () => {
  handleClearCookie().catch((error) => showNotice(error.message, 'error'));
});
dom.openLoginButton.addEventListener('click', () => {
  handleOpenLogin().catch((error) => showNotice(error.message, 'error'));
});
dom.syncSessionButton.addEventListener('click', () => {
  handleSyncSession().catch((error) => showNotice(error.message, 'error'));
});
dom.refreshSessionButton.addEventListener('click', () => {
  refreshSession(true)
    .then(() => showNotice('已完成登录态检测', 'info'))
    .catch((error) => showNotice(error.message, 'error'));
});
dom.jobsPanel.addEventListener('click', (event) => {
  const button = event.target.closest('button[data-action]');
  if (!button) return;
  handleJobAction(button.dataset.action, button.dataset.jobId).catch((error) => showNotice(error.message, 'error'));
});

void bootstrap();
