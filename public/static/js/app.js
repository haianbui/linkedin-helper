// LinkedIn Helper - Main Application Logic

const $ = (sel) => document.querySelector(sel);
let currentSessionId = null;
let allResults = [];
let currentDimensions = [];

// --- Init ---
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => loadHistory());
} else {
  loadHistory();
}

// --- Search Form ---
$('#search-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const query = $('#query-input').value.trim();
  if (!query) return;

  resetUI();
  $('#search-btn').disabled = true;
  $('#progress-panel').classList.remove('hidden');
  $('#history-panel').classList.add('hidden');

  // Try SSE first (works on local dev), fall back to sync endpoint (Vercel)
  try {
    const res = await fetch('/api/search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query }),
    });

    if (!res.ok) {
      const err = await res.json();
      showError(err.detail || 'Failed to start search');
      return;
    }

    const { session_id } = await res.json();
    currentSessionId = session_id;
    connectSSE(session_id, query);
  } catch (err) {
    runSyncSearch(query);
  }
});

// --- SSE Connection (local dev - real-time streaming) ---
function connectSSE(sessionId, query) {
  const evtSource = new EventSource(`/api/search/${sessionId}/stream?query=${encodeURIComponent(query)}`);
  let gotData = false;
  let sseTimeout = null;

  sseTimeout = setTimeout(() => {
    if (!gotData) {
      evtSource.close();
      runSyncSearch(query);
    }
  }, 10000);

  evtSource.addEventListener('status', (e) => {
    gotData = true;
    if (sseTimeout) { clearTimeout(sseTimeout); sseTimeout = null; }
    updateProgress(e.data);
  });

  evtSource.addEventListener('criteria', (e) => {
    gotData = true;
    renderCriteria(JSON.parse(e.data));
  });

  evtSource.addEventListener('dimensions', (e) => {
    gotData = true;
    currentDimensions = JSON.parse(e.data);
    updateDimensionHeaders(currentDimensions);
  });

  evtSource.addEventListener('result', (e) => {
    const result = JSON.parse(e.data);
    allResults.push(result);
    appendResult(result);
  });

  evtSource.addEventListener('complete', (e) => {
    const summary = JSON.parse(e.data);
    onSearchComplete(summary);
    evtSource.close();
  });

  evtSource.addEventListener('error', (e) => {
    if (sseTimeout) { clearTimeout(sseTimeout); sseTimeout = null; }

    if (e.data) {
      showError(e.data);
      evtSource.close();
      return;
    }

    evtSource.close();
    if (!gotData || allResults.length === 0) {
      runSyncSearch(query);
    } else {
      $('#search-btn').disabled = false;
      $('#spinner').classList.add('hidden');
    }
  });
}

// --- Sync Search (Vercel - single request/response) ---
async function runSyncSearch(query) {
  resetUI();
  $('#search-btn').disabled = true;
  $('#progress-panel').classList.remove('hidden');
  $('#history-panel').classList.add('hidden');
  updateProgress('Running search (this may take up to a minute)...');

  try {
    const res = await fetch('/api/search/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
      showError(err.detail || err.error || 'Search failed');
      return;
    }

    const data = await res.json();

    if (data.error) {
      showError(data.error);
      return;
    }

    currentSessionId = data.session_id;

    if (data.criteria) {
      renderCriteria(data.criteria);
    }

    if (data.dimensions) {
      currentDimensions = data.dimensions;
      updateDimensionHeaders(currentDimensions);
    }

    for (const result of data.results || []) {
      allResults.push(result);
      appendResult(result);
    }

    onSearchComplete({ total: data.total || allResults.length });
  } catch (err) {
    showError(`Search failed: ${err.message}`);
  }
}

// --- UI Helpers ---
function resetUI() {
  allResults = [];
  currentDimensions = [];
  $('#criteria-panel').classList.add('hidden');
  $('#progress-panel').classList.add('hidden');
  $('#error-panel').classList.add('hidden');
  $('#results-panel').classList.add('hidden');
  $('#results-body').innerHTML = '';
  $('#progress-log').innerHTML = '';
  $('#spinner').classList.remove('hidden');
  updateDimensionHeaders([]);
}

function updateDimensionHeaders(dims) {
  $('#dim-1-header').textContent = dims[0] || '';
  $('#dim-2-header').textContent = dims[1] || '';
  $('#dim-3-header').textContent = dims[2] || '';
}

function updateProgress(message) {
  $('#progress-text').textContent = message;
  const log = $('#progress-log');
  const line = document.createElement('div');
  line.textContent = `${new Date().toLocaleTimeString()} - ${message}`;
  log.appendChild(line);
  log.scrollTop = log.scrollHeight;
}

function showError(message) {
  $('#error-panel').classList.remove('hidden');
  $('#error-text').textContent = message;
  $('#search-btn').disabled = false;
  $('#spinner').classList.add('hidden');
}

function renderCriteria(criteria) {
  const panel = $('#criteria-panel');
  const content = $('#criteria-content');
  panel.classList.remove('hidden');

  const parts = [];
  if (criteria.job_titles?.length) {
    parts.push(`<strong>Titles:</strong> ${criteria.job_titles.join(', ')}`);
  }
  if (criteria.locations?.length) {
    parts.push(`<strong>Locations:</strong> ${criteria.locations.join(', ')}`);
  }
  if (criteria.keywords?.length) {
    parts.push(`<strong>Keywords:</strong> ${criteria.keywords.join(', ')}`);
  }
  if (criteria.experience_requirements?.length) {
    parts.push(`<strong>Experience:</strong> ${criteria.experience_requirements.join('; ')}`);
  }
  if (criteria.semantic_intent) {
    parts.push(`<strong>Looking for:</strong> ${criteria.semantic_intent}`);
  }

  content.innerHTML = parts.map(p => `<div>${p}</div>`).join('');
}

function scoreClass(score) {
  if (score >= 70) return 'score-high';
  if (score >= 50) return 'score-medium';
  return 'score-low';
}

function scoreBadge(score) {
  if (typeof score !== 'number') return '<span class="text-gray-300">-</span>';
  return `<span class="inline-block px-2 py-1 rounded text-xs font-semibold ${scoreClass(score)}">${score}</span>`;
}

function appendResult(result) {
  const panel = $('#results-panel');
  panel.classList.remove('hidden');

  const profile = result.profile;
  const evaluation = result.evaluation;
  const score = evaluation.match_score;
  const subScores = evaluation.sub_scores || [];

  const subScoreCells = [0, 1, 2].map(i => {
    const s = subScores[i];
    return `<td class="px-4 py-3">${scoreBadge(s ? s.score : null)}</td>`;
  }).join('');

  const row = document.createElement('tr');
  row.className = 'result-row';
  row.innerHTML = `
    <td class="px-4 py-3 text-gray-500">${result.rank}</td>
    <td class="px-4 py-3">
      <div class="font-medium text-gray-900">
        ${profile.linkedin_url
          ? `<a href="${profile.linkedin_url}" target="_blank" class="text-blue-600 hover:underline">${escapeHtml(profile.full_name)}</a>`
          : escapeHtml(profile.full_name)
        }
      </div>
    </td>
    <td class="px-4 py-3 text-gray-600">
      <div>${escapeHtml(profile.headline || profile.current_title || '')}</div>
      ${profile.current_company ? `<div class="text-xs text-gray-400">${escapeHtml(profile.current_company)}</div>` : ''}
    </td>
    <td class="px-4 py-3 text-gray-600">${escapeHtml(profile.location || '')}</td>
    <td class="px-4 py-3">${scoreBadge(score)}</td>
    ${subScoreCells}
    <td class="px-4 py-3 text-gray-600 text-xs">${escapeHtml(evaluation.summary || '')}</td>
  `;

  row.addEventListener('click', () => toggleDetails(result, row));
  $('#results-body').appendChild(row);
  $('#results-count').textContent = `${allResults.length} results`;
}

function toggleDetails(result, row) {
  const existingDetails = row.nextElementSibling;
  if (existingDetails?.classList.contains('result-details')) {
    existingDetails.remove();
    return;
  }

  const evaluation = result.evaluation;
  const profile = result.profile;
  const hitCount = profile.hit_count || result.hit_count;

  const detailRow = document.createElement('tr');
  detailRow.className = 'result-details expanded';
  detailRow.innerHTML = `
    <td colspan="9" class="px-6 py-4">
      <div class="grid grid-cols-2 gap-4 text-xs">
        <div>
          <h4 class="font-semibold text-gray-700 mb-1">Match Reasons</h4>
          <ul class="list-disc list-inside text-gray-600 space-y-0.5">
            ${(evaluation.match_reasons || []).map(r => `<li>${escapeHtml(r)}</li>`).join('')}
          </ul>
          ${evaluation.concerns?.length ? `
            <h4 class="font-semibold text-gray-700 mt-2 mb-1">Concerns</h4>
            <ul class="list-disc list-inside text-orange-600 space-y-0.5">
              ${evaluation.concerns.map(c => `<li>${escapeHtml(c)}</li>`).join('')}
            </ul>
          ` : ''}
          ${hitCount > 1 ? `<div class="mt-2"><span class="text-xs bg-purple-100 text-purple-700 px-2 py-0.5 rounded">Seen in ${hitCount} searches</span></div>` : ''}
        </div>
        <div>
          ${profile.summary ? `
            <h4 class="font-semibold text-gray-700 mb-1">Profile Summary</h4>
            <p class="text-gray-600">${escapeHtml(profile.summary.slice(0, 300))}</p>
          ` : ''}
          ${profile.experience?.length ? `
            <h4 class="font-semibold text-gray-700 mt-2 mb-1">Experience</h4>
            <ul class="text-gray-600 space-y-0.5">
              ${profile.experience.slice(0, 3).map(e =>
                `<li>${escapeHtml(e.title)} at ${escapeHtml(e.company)}</li>`
              ).join('')}
            </ul>
          ` : ''}
          ${profile.skills?.length ? `
            <h4 class="font-semibold text-gray-700 mt-2 mb-1">Skills</h4>
            <p class="text-gray-600">${profile.skills.slice(0, 10).map(s => escapeHtml(s)).join(', ')}</p>
          ` : ''}
        </div>
      </div>
    </td>
  `;

  row.after(detailRow);
}

function onSearchComplete(summary) {
  $('#spinner').classList.add('hidden');
  $('#progress-text').textContent = `Search complete! Found ${summary.total} results.`;
  $('#search-btn').disabled = false;
  $('#results-count').textContent = `${summary.total} results`;
  loadHistory(); // refresh history list
}

// --- Sorting ---
document.querySelectorAll('.sortable').forEach(th => {
  th.addEventListener('click', () => {
    sortResults(th.dataset.sort);
  });
});

function sortResults(key) {
  allResults.sort((a, b) => {
    let aVal, bVal;
    if (key === 'overall') {
      aVal = a.evaluation.match_score;
      bVal = b.evaluation.match_score;
    } else {
      const idx = parseInt(key.replace('dim', '')) - 1;
      aVal = (a.evaluation.sub_scores || [])[idx]?.score || 0;
      bVal = (b.evaluation.sub_scores || [])[idx]?.score || 0;
    }
    return bVal - aVal;
  });

  // Re-render
  $('#results-body').innerHTML = '';
  allResults.forEach((r, i) => {
    r.rank = i + 1;
    appendResult(r);
  });
}

// --- Search History ---
async function loadHistory() {
  try {
    const res = await fetch('/api/sessions', { cache: 'no-store' });
    if (!res.ok) return;
    const sessions = await res.json();
    renderHistory(sessions);
  } catch (e) {
    console.error('loadHistory failed:', e);
    const list = document.querySelector('#history-list');
    if (list) list.innerHTML = '<p class="text-sm text-gray-400">No previous searches</p>';
  }
}

function renderHistory(sessions) {
  const panel = $('#history-panel');
  const list = $('#history-list');

  if (!sessions.length) {
    list.innerHTML = '<p class="text-sm text-gray-400">No previous searches</p>';
    return;
  }

  list.innerHTML = sessions.map(s => `
    <div class="flex justify-between items-center p-3 rounded-lg hover:bg-gray-50 cursor-pointer border border-gray-100 transition-colors"
         data-query-id="${s.id}">
      <div>
        <div class="text-sm font-medium text-gray-800">${escapeHtml(s.query || s.natural_query || '')}</div>
        <div class="text-xs text-gray-400">${new Date(s.created_at).toLocaleDateString()} - ${s.result_count || 0} results</div>
      </div>
      <span class="text-xs px-2 py-1 rounded ${s.status === 'completed' ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500'}">${s.status}</span>
    </div>
  `).join('');

  list.querySelectorAll('[data-query-id]').forEach(el => {
    el.addEventListener('click', () => loadSavedResults(el.dataset.queryId));
  });
}

async function loadSavedResults(queryId) {
  resetUI();
  $('#progress-panel').classList.remove('hidden');
  $('#history-panel').classList.add('hidden');
  updateProgress('Loading saved results...');

  try {
    const res = await fetch(`/api/sessions/${queryId}/results`);
    if (!res.ok) throw new Error('Failed to load results');
    const data = await res.json();

    const q = data.query;
    currentSessionId = queryId;

    // Set dimensions from saved query
    const dims = [q.dimension_1_name, q.dimension_2_name, q.dimension_3_name].filter(Boolean);
    if (dims.length === 3) {
      currentDimensions = dims;
      updateDimensionHeaders(currentDimensions);
    }

    // Render criteria if available
    if (q.criteria_json) {
      try { renderCriteria(JSON.parse(q.criteria_json)); } catch(e) {}
    }

    // Render results
    for (const result of data.results || []) {
      allResults.push(result);
      appendResult(result);
    }
    onSearchComplete({ total: data.results?.length || 0 });
  } catch (e) {
    showError(e.message);
  }
}

// --- CSV Export ---
$('#export-btn')?.addEventListener('click', () => {
  if (currentSessionId) {
    window.location.href = `/api/export/${currentSessionId}/csv`;
  }
});

// --- Utilities ---
function escapeHtml(text) {
  if (!text) return '';
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}
