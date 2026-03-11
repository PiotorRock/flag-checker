const fileInput = document.getElementById('fileInput');
const scanBtn = document.getElementById('scanBtn');
const registryMeta = document.getElementById('registryMeta');
const summaryCard = document.getElementById('summaryCard');
const previewCard = document.getElementById('previewCard');
const statsBox = document.getElementById('stats');
const matchesBox = document.getElementById('matches');
const preview = document.getElementById('preview');

let registry = null;

function escapeHtml(value) {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function normalizeChar(ch) {
  return ch.toLowerCase().replace('ё', 'е');
}

function buildNormalizedMap(text) {
  let normalized = '';
  const map = [];
  for (let i = 0; i < text.length; i += 1) {
    normalized += normalizeChar(text[i]);
    map.push(i);
  }
  return { normalized, map };
}

function normalizePlain(text) {
  return [...text].map(normalizeChar).join('');
}

function isWordChar(ch) {
  return /[0-9a-zа-я]/i.test(ch);
}

function isBoundary(text, start, end) {
  const prev = start > 0 ? text[start - 1] : '';
  const next = end < text.length ? text[end] : '';
  return !isWordChar(prev) && !isWordChar(next);
}

function dedupeStrings(items) {
  const seen = new Set();
  const out = [];
  for (const item of items) {
    const clean = (item || '').trim();
    if (!clean) continue;
    const key = clean.toLowerCase();
    if (!seen.has(key)) {
      seen.add(key);
      out.push(clean);
    }
  }
  return out;
}

async function loadRegistry() {
  registryMeta.textContent = 'Загрузка реестров…';
  const res = await fetch(`./data/registries.json?v=${Date.now()}`);
  if (!res.ok) {
    throw new Error(`Не удалось загрузить data/registries.json: ${res.status}`);
  }
  registry = await res.json();
  const counts = registry.counts || {};
  const total = counts.total_entities || (registry.entities ? registry.entities.length : 0);
  registryMeta.innerHTML = `
    Обновлено: <strong>${escapeHtml(registry.generated_at || 'неизвестно')}</strong><br>
    Записей: <strong>${total}</strong>
    <small class="mono">foreign_agents=${counts.foreign_agents || 0},
    terrorists_extremists=${counts.terrorists_extremists || 0},
    undesirable_orgs=${counts.undesirable_orgs || 0},
    banned_orgs=${counts.banned_orgs || 0}</small>
  `;
}

function findMatches(originalText, entities) {
  const { normalized: normText, map } = buildNormalizedMap(originalText);
  const rawMatches = [];

  for (const entity of entities) {
    const variants = dedupeStrings([entity.name, ...(entity.variants || [])]);
    for (const variant of variants) {
      const needle = normalizePlain(variant);
      if (!needle || needle.length < 3) continue;

      let fromIndex = 0;
      while (fromIndex < normText.length) {
        const hitIndex = normText.indexOf(needle, fromIndex);
        if (hitIndex === -1) break;

        const hitEnd = hitIndex + needle.length;
        if (isBoundary(normText, hitIndex, hitEnd)) {
          rawMatches.push({
            start: map[hitIndex],
            end: map[hitEnd - 1] + 1,
            category: entity.category,
            entityName: entity.name,
            variant
          });
        }
        fromIndex = hitIndex + needle.length;
      }
    }
  }

  rawMatches.sort((a, b) => {
    if (a.start !== b.start) return a.start - b.start;
    return (b.end - b.start) - (a.end - a.start);
  });

  const filtered = [];
  let currentEnd = -1;
  for (const match of rawMatches) {
    if (match.start >= currentEnd) {
      filtered.push(match);
      currentEnd = match.end;
    }
  }

  return filtered;
}

function renderPreview(text, matches) {
  if (!matches.length) {
    preview.innerHTML = escapeHtml(text);
    return;
  }

  let html = '';
  let cursor = 0;

  for (const match of matches) {
    html += escapeHtml(text.slice(cursor, match.start));
    html += `<mark class="hit">${escapeHtml(text.slice(match.start, match.end))}</mark>`;
    cursor = match.end;
  }

  html += escapeHtml(text.slice(cursor));
  preview.innerHTML = html;
}

function humanCategory(category) {
  const map = {
    foreign_agents: 'Иноагенты',
    terrorists_extremists: 'Террористы / экстремисты',
    undesirable_orgs: 'Нежелательные организации',
    banned_orgs: 'Запрещенные / ликвидированные организации'
  };
  return map[category] || category;
}

function renderSummary(matches) {
  summaryCard.classList.remove('hidden');
  previewCard.classList.remove('hidden');

  const uniqueEntities = new Map();
  for (const match of matches) {
    const key = `${match.category}||${match.entityName}`;
    if (!uniqueEntities.has(key)) {
      uniqueEntities.set(key, {
        category: match.category,
        entityName: match.entityName,
        count: 0,
        variants: new Set()
      });
    }
    const item = uniqueEntities.get(key);
    item.count += 1;
    item.variants.add(match.variant);
  }

  const byCategory = {};
  for (const item of uniqueEntities.values()) {
    byCategory[item.category] = (byCategory[item.category] || 0) + 1;
  }

  statsBox.innerHTML = `
    <div class="stat">Совпадений: <strong>${matches.length}</strong></div>
    <div class="stat">Уникальных сущностей: <strong>${uniqueEntities.size}</strong></div>
    <div class="stat">Иноагенты: <strong>${byCategory.foreign_agents || 0}</strong></div>
    <div class="stat">Террористы / экстремисты: <strong>${byCategory.terrorists_extremists || 0}</strong></div>
    <div class="stat">Нежелательные организации: <strong>${byCategory.undesirable_orgs || 0}</strong></div>
    <div class="stat">Запрещенные организации: <strong>${byCategory.banned_orgs || 0}</strong></div>
  `;

  const items = [...uniqueEntities.values()].sort((a, b) => a.entityName.localeCompare(b.entityName, 'ru'));
  if (!items.length) {
    matchesBox.innerHTML = '<p>Ничего не найдено.</p>';
    return;
  }

  matchesBox.innerHTML = items.map((item) => `
    <div class="match-group">
      <h3>${escapeHtml(item.entityName)} <span class="badge">${escapeHtml(humanCategory(item.category))}</span></h3>
      <div>Упоминаний: <strong>${item.count}</strong></div>
      <div>Сработавшие варианты: ${escapeHtml([...item.variants].join(', '))}</div>
    </div>
  `).join('');
}

async function extractTextFromDocx(file) {
  const arrayBuffer = await file.arrayBuffer();
  const result = await mammoth.extractRawText({ arrayBuffer });
  return result.value || '';
}

scanBtn.addEventListener('click', async () => {
  try {
    if (!registry) {
      await loadRegistry();
    }
    const file = fileInput.files[0];
    if (!file) {
      alert('Сначала выберите .docx файл.');
      return;
    }

    scanBtn.disabled = true;
    scanBtn.textContent = 'Проверяем…';

    const text = await extractTextFromDocx(file);
    const matches = findMatches(text, registry.entities || []);

    renderSummary(matches);
    renderPreview(text, matches);
  } catch (error) {
    summaryCard.classList.remove('hidden');
    summaryCard.innerHTML = `<div class="error">Ошибка: ${escapeHtml(error.message)}</div>`;
  } finally {
    scanBtn.disabled = false;
    scanBtn.textContent = 'Проверить';
  }
});

loadRegistry().catch((error) => {
  registryMeta.innerHTML = `<span class="error">Ошибка загрузки реестров: ${escapeHtml(error.message)}</span>`;
});
