// LinkedIn Helper - Main Application Logic

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

let currentSessionId = null;
let allResults = [];

// --- Search Form ---
$('#search-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const query = $('#query-input').value.trim();
  if (!query) return;

  resetUI();
  $('#search-btn').disabled = true;
  $('#progress-panel').classList.remove('hidden');

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
    showError(`Connection error: ${err.message}`);
  }
});

// --- SSE Connection ---
function connectSSE(sessionId, query) {
  // Pass query as param so it works on serverless (no shared memory between requests)
  const evtSource = new EventSource(`/api/search/${sessionId}/stream?query=${encodeURIComponent(query)}`);

  evtSource.addEventListener('status', (e) => {
    updateProgress(e.data);
  });

  evtSource.addEventListener('criteria', (e) => {
    renderCriteria(JSON.parse(e.data));
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
    // Check if it's an SSE error event with data
    if (e.data) {
      showError(e.data);
    }
    evtSource.close();
    $('#search-btn').disabled = false;
    $('#spinner').classList.add('hidden');
  });
}

// --- UI Helpers ---
function resetUI() {
  allResults = [];
  $('#criteria-panel').classList.add('hidden');
  $('#progress-panel').classList.add('hidden');
  $('#error-panel').classList.add('hidden');
  $('#results-panel').classList.add('hidden');
  $('#results-body').innerHTML = '';
  $('#progress-log').innerHTML = '';
  $('#spinner').classList.remove('hidden');
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

function appendResult(result) {
  const panel = $('#results-panel');
  panel.classList.remove('hidden');

  const profile = result.profile;
  const evaluation = result.evaluation;
  const score = evaluation.match_score;

  let scoreClass = 'score-low';
  if (score >= 70) scoreClass = 'score-high';
  else if (score >= 50) scoreClass = 'score-medium';

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
    <td class="px-4 py-3">
      <span class="inline-block px-2 py-1 rounded text-xs font-semibold ${scoreClass}">${score}</span>
    </td>
    <td class="px-4 py-3 text-gray-600 text-xs">${escapeHtml(evaluation.summary || '')}</td>
  `;

  // Click to expand details
  row.addEventListener('click', () => toggleDetails(result, row));
  $('#results-body').appendChild(row);

  // Update count
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

  const detailRow = document.createElement('tr');
  detailRow.className = 'result-details expanded';
  detailRow.innerHTML = `
    <td colspan="6" class="px-6 py-4">
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
