(async function () {
  const app = document.getElementById('app');
  const params = new URLSearchParams(window.location.search);
  const src = params.get('src');

  if (!src) {
    app.innerHTML = '<div class="error">Missing ?src=... parameter.</div>';
    return;
  }

  let payload;
  try {
    const response = await fetch(src, { cache: 'no-store' });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    payload = await response.json();
  } catch (error) {
    app.innerHTML = `<div class="error">Failed to load ${src}: ${error.message}</div>`;
    return;
  }

  renderApp(app, payload);
})();

function renderApp(root, payload) {
  const modeConfig = getModeConfig(payload.mode);
  const dates = payload.dates || [];
  const timeSlots = payload.time_slots || [];
  const facilities = payload.facilities || [];
  const cells = payload.cells || {};

  const state = {
    view: 'summary',
    allExpanded: true,
    activeTimes: new Set(timeSlots.map((slot) => slot.key)),
    monthFilter: 'all',
    dayFilter: 'all',
    openOnly: false,
    activeStatuses: new Set(),
  };

  root.innerHTML = `
    <div class="sticky-nav">
      <header class="site-header">
        <a href="index.html" style="display:inline-block;font-size:.75em;color:#64748b;text-decoration:none;margin-bottom:8px;">← ホームへ</a>
        <div class="site-title">${escapeHtml(payload.page?.title || 'Tennis Schedule')}</div>
        <div class="site-meta">${buildMeta(payload)}</div>
      </header>
      <div class="toolbar">
        <div class="tabs">
          <button class="tab on" data-view="summary">サマリー</button>
          <button class="tab" data-view="facility">場別</button>
          <button class="tab" data-view="date">日付別</button>
        </div>
        <div class="filters">
          <div class="fg" id="status-filters"></div>
          <div class="fg" id="open-only-filter"></div>
          <div class="fg" id="month-filters"></div>
          <div class="fg" id="day-filters"></div>
          <div class="fg" id="time-filters"><span class="flabel">時間</span></div>
          <div class="fg" id="expand-fg" style="display:none">
            <button class="btn expand-btn on" id="expand-btn">全折畳</button>
          </div>
          <button class="btn reset" id="reset-btn">リセット</button>
        </div>
      </div>
    </div>
    <div class="main">
      <div class="view on" id="view-summary"></div>
      <div class="view" id="view-facility"></div>
      <div class="view" id="view-date"></div>
    </div>
  `;

  renderFilterControls(root, payload, modeConfig, state, timeSlots);
  renderViews(root, payload, modeConfig, state, dates, timeSlots, facilities, cells);
  attachHandlers(root, payload, modeConfig, state, dates, timeSlots, facilities, cells);
  applyFilters(root, payload, modeConfig, state, dates, timeSlots, facilities, cells);
}

function getModeConfig(mode) {
  const configs = {
    reservation: {
      summaryLegend: '12面の中で最も空きがある枠を表示',
      statusPriority: ['open', 'general', 'cancel', 'full', 'rain', 'closed', 'unavailable', 'other'],
      extraStatusButtons: [],
      useOpenOnly: true,
    },
    lottery: {
      summaryLegend: '12面の中で一番申請が少ないコートの数値を表示',
      statusPriority: ['hot', 'easy', 'medium', 'hard', 'unavailable', 'other'],
      extraStatusButtons: ['hot', 'easy', 'medium', 'hard'],
      useOpenOnly: false,
    },
  };
  return configs[mode] || configs.reservation;
}

function buildMeta(payload) {
  const parts = [];
  if (payload.generated_at) parts.push(`更新: ${escapeHtml(formatGeneratedAt(payload.generated_at, payload.timezone))}`);
  if (payload.page?.facility_group) parts.push(escapeHtml(payload.page.facility_group));
  if (payload.range?.start && payload.range?.end) {
    parts.push(`対象期間: ${escapeHtml(payload.range.start)} ～ ${escapeHtml(payload.range.end)}`);
  }
  return parts.join(' &nbsp;·&nbsp; ');
}

function formatGeneratedAt(value, timezone) {
  const text = String(value ?? '').replace('T', ' ');
  if (timezone === 'Asia/Tokyo' && text.endsWith('+09:00')) {
    return `${text.slice(0, -6)} JST`;
  }
  return text;
}

function renderFilterControls(root, payload, modeConfig, state, timeSlots) {
  const statusWrap = root.querySelector('#status-filters');
  if (modeConfig.extraStatusButtons.length > 0) {
    statusWrap.innerHTML = '<span class="flabel">難易度</span>';
    modeConfig.extraStatusButtons.forEach((status) => {
      statusWrap.insertAdjacentHTML(
        'beforeend',
        `<button class="btn lv-${status}" data-status="${status}">${escapeHtml(statusButtonLabel(status))}</button>`
      );
    });
  }

  const openWrap = root.querySelector('#open-only-filter');
  if (payload.filters?.open_only && modeConfig.useOpenOnly) {
    openWrap.innerHTML = '<span class="flabel">絞込み</span><button class="btn toggle" id="open-only-btn">○ 空き</button>';
  }

  const monthWrap = root.querySelector('#month-filters');
  if (payload.filters?.month) {
    monthWrap.innerHTML = `
      <span class="flabel">月</span>
      <button class="btn month on" data-month="all">全部</button>
      <button class="btn month" data-month="current">本月</button>
      <button class="btn month" data-month="next">下一月</button>
    `;
  }

  const dayWrap = root.querySelector('#day-filters');
  if (payload.filters?.day_type) {
    dayWrap.innerHTML = `
      <span class="flabel">曜日</span>
      <button class="btn day on" data-day="all">全部</button>
      <button class="btn day" data-day="weekday">平日</button>
      <button class="btn day" data-day="special">周末+休日</button>
    `;
  }

  const timeWrap = root.querySelector('#time-filters');
  if (payload.filters?.time) {
    timeSlots.forEach((slot) => {
      timeWrap.insertAdjacentHTML(
        'beforeend',
        `<button class="btn time-btn on" data-time="${escapeHtml(slot.key)}">${escapeHtml(slot.label)}</button>`
      );
    });
  }
}

function renderViews(root, payload, modeConfig, state, dates, timeSlots, facilities, cells) {
  const summary = buildSummaryGrid(modeConfig, dates, timeSlots, facilities, cells);
  root.querySelector('#view-summary').innerHTML = `
    <div class="sum-legend">${escapeHtml(payload.page?.legend || modeConfig.summaryLegend)}</div>
    <div class="facility">${buildTable(summary, dates)}</div>
  `;

  root.querySelector('#view-facility').innerHTML = facilities.map((facility) => `
    <div class="facility" data-facility="${escapeHtml(facility.key)}">
      <div class="fac-hd" data-toggle>${escapeHtml(facility.name)}<span class="arrow">▼</span></div>
      <div class="fac-bd open">${buildTable(buildFacilityGrid(facility, dates, timeSlots, cells), dates)}</div>
    </div>
  `).join('');

  root.querySelector('#view-date').innerHTML = dates.map((dateItem) => `
    <div class="facility ${dateItem.day_group === 'special' ? 'special-date' : ''}" data-date-key="${escapeHtml(dateItem.key)}" data-month-group="${escapeHtml(dateItem.month_group)}" data-day-group="${escapeHtml(dateItem.day_group)}">
      <div class="fac-hd" data-toggle>${escapeHtml(dateItem.label)}<span class="arrow">▼</span></div>
      <div class="fac-bd open">${buildDateTable(dateItem, facilities, timeSlots, cells)}</div>
    </div>
  `).join('');
}

function buildSummaryGrid(modeConfig, dates, timeSlots, facilities, cells) {
  return timeSlots.map((slot) => {
    const row = { time: slot };
    dates.forEach((dateItem) => {
      const all = facilities
        .map((facility) => cells[facility.key]?.[dateItem.key]?.[slot.key])
        .filter(Boolean);
      row[dateItem.key] = pickBestCell(all, modeConfig.statusPriority);
    });
    return row;
  });
}

function buildFacilityGrid(facility, dates, timeSlots, cells) {
  return timeSlots.map((slot) => {
    const row = { time: slot };
    dates.forEach((dateItem) => {
      row[dateItem.key] = cells[facility.key]?.[dateItem.key]?.[slot.key] || fallbackCell();
    });
    return row;
  });
}

function buildTable(rows, dates) {
  return `
    <div class="tbl-wrap">
      <table>
        <thead>
          <tr>
            <th class="col-time">時間</th>
            ${dates.map((dateItem) => `
              <th class="${dateItem.day_group === 'special' ? 'col-special' : 'col-wd'}" data-date-key="${escapeHtml(dateItem.key)}" data-month-group="${escapeHtml(dateItem.month_group)}" data-day-group="${escapeHtml(dateItem.day_group)}">${escapeHtml(dateItem.label)}</th>
            `).join('')}
          </tr>
        </thead>
        <tbody>
          ${rows.map((row) => `
            <tr class="row-time" data-time="${escapeHtml(row.time.key)}">
              <td class="col-time">${escapeHtml(row.time.label)}</td>
              ${dates.map((dateItem) => buildCell(row[dateItem.key], dateItem)).join('')}
            </tr>
          `).join('')}
        </tbody>
      </table>
    </div>
  `;
}

function buildDateTable(dateItem, facilities, timeSlots, cells) {
  return `
    <div class="tbl-wrap">
      <table>
        <thead>
          <tr>
            <th class="col-time">時間</th>
            ${facilities.map((facility) => `<th class="col-fac">${escapeHtml(facility.short_name || facility.name)}</th>`).join('')}
          </tr>
        </thead>
        <tbody>
          ${timeSlots.map((slot) => `
            <tr class="row-time" data-time="${escapeHtml(slot.key)}">
              <td class="col-time">${escapeHtml(slot.label)}</td>
              ${facilities.map((facility) => {
                const cell = cells[facility.key]?.[dateItem.key]?.[slot.key] || fallbackCell();
                return `<td class="cell-${escapeHtml(cell.status)}" data-level="${escapeHtml(cell.status)}" data-text="${escapeHtml(cell.text)}">${escapeHtml(cell.text)}</td>`;
              }).join('')}
            </tr>
          `).join('')}
        </tbody>
      </table>
    </div>
  `;
}

function buildCell(cell, dateItem) {
  const item = cell || fallbackCell();
  return `
    <td class="${dateItem.day_group === 'special' ? 'col-special ' : ''}cell-${escapeHtml(item.status)}"
        data-level="${escapeHtml(item.status)}"
        data-text="${escapeHtml(item.text)}"
        data-date-key="${escapeHtml(dateItem.key)}"
        data-month-group="${escapeHtml(dateItem.month_group)}"
        data-day-group="${escapeHtml(dateItem.day_group)}">
      ${escapeHtml(item.text)}
    </td>
  `;
}

function pickBestCell(cells, priority) {
  if (cells.length === 0) return fallbackCell();
  const order = new Map(priority.map((status, index) => [status, index]));
  return [...cells].sort((left, right) => {
    return (order.get(left.status) ?? 999) - (order.get(right.status) ?? 999);
  })[0];
}

function fallbackCell() {
  return { status: 'unavailable', text: '—', raw: '-' };
}

function attachHandlers(root, payload, modeConfig, state, dates, timeSlots, facilities, cells) {
  root.querySelectorAll('.tab').forEach((button) => {
    button.addEventListener('click', () => {
      state.view = button.dataset.view;
      root.querySelectorAll('.tab').forEach((item) => item.classList.remove('on'));
      button.classList.add('on');
      root.querySelectorAll('.view').forEach((view) => view.classList.remove('on'));
      root.querySelector(`#view-${state.view}`).classList.add('on');
      root.querySelector('#expand-fg').style.display = state.view === 'summary' ? 'none' : '';
    });
  });

  root.querySelectorAll('[data-toggle]').forEach((header) => {
    header.addEventListener('click', () => {
      header.classList.toggle('open');
      header.nextElementSibling.classList.toggle('open');
    });
  });

  root.querySelector('#expand-btn')?.addEventListener('click', () => {
    state.allExpanded = !state.allExpanded;
    const button = root.querySelector('#expand-btn');
    button.textContent = state.allExpanded ? '全折畳' : '全展開';
    button.classList.toggle('on', state.allExpanded);
    const selector = state.view === 'date' ? '#view-date [data-toggle]' : '#view-facility [data-toggle]';
    root.querySelectorAll(selector).forEach((header) => {
      header.classList.toggle('open', state.allExpanded);
      header.nextElementSibling.classList.toggle('open', state.allExpanded);
    });
  });

  root.querySelectorAll('.time-btn').forEach((button) => {
    button.addEventListener('click', () => {
      const key = button.dataset.time;
      if (state.activeTimes.has(key)) state.activeTimes.delete(key);
      else state.activeTimes.add(key);
      button.classList.toggle('on');
      applyFilters(root, payload, modeConfig, state, dates, timeSlots, facilities, cells);
    });
  });

  root.querySelectorAll('.month').forEach((button) => {
    button.addEventListener('click', () => {
      state.monthFilter = button.dataset.month;
      root.querySelectorAll('.month').forEach((item) => item.classList.remove('on'));
      button.classList.add('on');
      applyFilters(root, payload, modeConfig, state, dates, timeSlots, facilities, cells);
    });
  });

  root.querySelectorAll('.day').forEach((button) => {
    button.addEventListener('click', () => {
      state.dayFilter = button.dataset.day;
      root.querySelectorAll('.day').forEach((item) => item.classList.remove('on'));
      button.classList.add('on');
      applyFilters(root, payload, modeConfig, state, dates, timeSlots, facilities, cells);
    });
  });

  root.querySelector('#open-only-btn')?.addEventListener('click', (event) => {
    state.openOnly = !state.openOnly;
    event.currentTarget.classList.toggle('on', state.openOnly);
    applyFilters(root, payload, modeConfig, state, dates, timeSlots, facilities, cells);
  });

  root.querySelectorAll('[data-status]').forEach((button) => {
    button.addEventListener('click', () => {
      const status = button.dataset.status;
      if (state.activeStatuses.has(status)) state.activeStatuses.delete(status);
      else state.activeStatuses.add(status);
      button.classList.toggle('on');
      applyFilters(root, payload, modeConfig, state, dates, timeSlots, facilities, cells);
    });
  });

  root.querySelector('#reset-btn').addEventListener('click', () => {
    state.activeTimes = new Set(timeSlots.map((slot) => slot.key));
    state.monthFilter = 'all';
    state.dayFilter = 'all';
    state.openOnly = false;
    state.activeStatuses.clear();
    root.querySelectorAll('.time-btn').forEach((button) => button.classList.add('on'));
    root.querySelectorAll('.month, .day, [data-status]').forEach((button) => button.classList.remove('on'));
    root.querySelector('.month[data-month="all"]')?.classList.add('on');
    root.querySelector('.day[data-day="all"]')?.classList.add('on');
    root.querySelector('#open-only-btn')?.classList.remove('on');
    applyFilters(root, payload, modeConfig, state, dates, timeSlots, facilities, cells);
  });
}

function applyFilters(root, payload, modeConfig, state, dates, timeSlots, facilities, cells) {
  filterStandardTables(root.querySelector('#view-summary table'), state);
  root.querySelectorAll('#view-facility .facility').forEach((card) => {
    const visible = filterStandardTables(card.querySelector('table'), state);
    card.style.display = visible ? '' : 'none';
  });
  root.querySelectorAll('#view-date .facility').forEach((card) => {
    const visible = filterDateTable(card.querySelector('table'), card, state);
    card.style.display = visible ? '' : 'none';
  });
}

function filterStandardTables(table, state) {
  if (!table) return false;
  const headers = Array.from(table.querySelectorAll('thead th[data-date-key]'));
  const rows = Array.from(table.querySelectorAll('tbody tr.row-time'));
  const visibleCols = {};

  headers.forEach((header) => {
    const monthOk = state.monthFilter === 'all' || header.dataset.monthGroup === state.monthFilter;
    const dayOk = state.dayFilter === 'all' || header.dataset.dayGroup === state.dayFilter;
    visibleCols[header.dataset.dateKey] = monthOk && dayOk;
    header.style.display = visibleCols[header.dataset.dateKey] ? '' : 'none';
  });

  let tableVisible = false;
  rows.forEach((row) => {
    const timeOk = state.activeTimes.has(row.dataset.time);
    let rowVisible = false;
    row.querySelectorAll('td[data-date-key]').forEach((cell) => {
      const columnVisible = visibleCols[cell.dataset.dateKey];
      const openOnlyOk = !state.openOnly || cell.dataset.level === 'open';
      const statusOk = state.activeStatuses.size === 0 || state.activeStatuses.has(cell.dataset.level);
      const visible = timeOk && columnVisible;
      cell.style.display = visible ? '' : 'none';
      cell.textContent = visible && openOnlyOk && statusOk ? cell.dataset.text : '';
      if (visible && openOnlyOk && statusOk && cell.dataset.text !== '—') rowVisible = true;
    });
    row.style.display = rowVisible ? '' : 'none';
    if (rowVisible) tableVisible = true;
  });

  return tableVisible;
}

function filterDateTable(table, card, state) {
  if (!table) return false;
  const monthOk = state.monthFilter === 'all' || card.dataset.monthGroup === state.monthFilter;
  const dayOk = state.dayFilter === 'all' || card.dataset.dayGroup === state.dayFilter;
  if (!monthOk || !dayOk) return false;

  const headers = Array.from(table.querySelectorAll('thead th.col-fac'));
  const rows = Array.from(table.querySelectorAll('tbody tr.row-time'));
  const visibleCols = headers.map((_, index) => {
    return rows.some((row) => {
      if (!state.activeTimes.has(row.dataset.time)) return false;
      const cell = row.querySelectorAll('td[data-level]')[index];
      if (!cell) return false;
      const openOnlyOk = !state.openOnly || cell.dataset.level === 'open';
      const statusOk = state.activeStatuses.size === 0 || state.activeStatuses.has(cell.dataset.level);
      return openOnlyOk && statusOk && cell.dataset.text !== '—';
    });
  });

  headers.forEach((header, index) => {
    header.style.display = visibleCols[index] ? '' : 'none';
  });

  let tableVisible = false;
  rows.forEach((row) => {
    const timeOk = state.activeTimes.has(row.dataset.time);
    let rowVisible = false;
    row.querySelectorAll('td[data-level]').forEach((cell, index) => {
      const columnVisible = visibleCols[index];
      const openOnlyOk = !state.openOnly || cell.dataset.level === 'open';
      const statusOk = state.activeStatuses.size === 0 || state.activeStatuses.has(cell.dataset.level);
      const showText = timeOk && columnVisible && openOnlyOk && statusOk;
      cell.style.display = columnVisible ? '' : 'none';
      cell.textContent = showText ? cell.dataset.text : '';
      if (showText && cell.dataset.text !== '—') rowVisible = true;
    });
    row.style.display = rowVisible ? '' : 'none';
    if (rowVisible) tableVisible = true;
  });
  return tableVisible;
}

function statusButtonLabel(status) {
  const labels = {
    hot: '★ 必中(0人)',
    easy: '● 容易(1-3)',
    medium: '△ 一般(4-10)',
    hard: '× 激戦(11+)',
  };
  return labels[status] || status;
}

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
