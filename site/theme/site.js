/* Progressive enhancement only. All content and links work without JavaScript. */
document.documentElement.classList.add('js');

for (const button of document.querySelectorAll('[data-copy]')) {
  let reset;
  button.addEventListener('click', async () => {
    const code = document.getElementById(button.dataset.copy);
    const status = button.closest('.copy-region').querySelector('[role="status"]');
    try {
      await navigator.clipboard.writeText(code.textContent);
      status.textContent = 'Copied. Paste it into your agent.';
      // A button with no room for a status line shows the result on itself.
      button.toggleAttribute('data-copied', true);
      clearTimeout(reset);
      reset = setTimeout(() => button.toggleAttribute('data-copied', false), 1600);
    } catch {
      const range = document.createRange();
      range.selectNodeContents(code);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      status.textContent = 'Text selected. Use your usual copy command.';
    }
  });
}

for (const link of document.querySelectorAll('[data-copy-markdown]')) {
  let reset;
  link.addEventListener('click', async event => {
    event.preventDefault();
    const text = fetch(link.href).then(response => {
      if (!response.ok) throw new Error(response.statusText);
      return response.text();
    });
    try {
      // A clipboard item that holds the pending text keeps the write inside the click, which Safari requires.
      if (window.ClipboardItem) {
        const blob = text.then(value => new Blob([value], { type: 'text/plain' }));
        await navigator.clipboard.write([new ClipboardItem({ 'text/plain': blob })]);
      } else {
        await navigator.clipboard.writeText(await text);
      }
      // The button shows the result to the eye. The status line says it to a screen reader.
      const status = link.closest('.copy-region').querySelector('[role="status"]');
      status.textContent = 'Copied as Markdown.';
      link.toggleAttribute('data-copied', true);
      clearTimeout(reset);
      reset = setTimeout(() => {
        link.toggleAttribute('data-copied', false);
        status.textContent = '';
      }, 1600);
    } catch {
      window.location.assign(link.href);
    }
  });
}

// The page outline stays open beside a wide layout and folds away above a narrow one.
const outline = document.querySelector('.reader-toc');
if (outline && window.matchMedia('(max-width: 1100px)').matches) outline.open = false;

const menu = document.getElementById('menu');
const menuToggle = document.querySelector('[data-menu-toggle]');
if (menu && menuToggle) {
  const menuClose = menu.querySelector('[data-menu-close]');
  function setMenu(open) {
    if (open) {
      // The field grows from wherever the control sits in this layout.
      const box = menuToggle.getBoundingClientRect();
      menu.style.setProperty('--menu-x', `${box.left + box.width / 2}px`);
      menu.style.setProperty('--menu-y', `${box.top + box.height / 2}px`);
    }
    menu.toggleAttribute('data-open', open);
    menuToggle.setAttribute('aria-expanded', String(open));
    (open ? menuClose : menuToggle).focus({ preventScroll: true });
  }
  menuToggle.addEventListener('click', () => setMenu(true));
  menuClose.addEventListener('click', () => setMenu(false));
  menu.addEventListener('keydown', event => {
    if (event.key === 'Escape') setMenu(false);
  });
  // A link to a section of this page must reveal that section.
  menu.addEventListener('click', event => {
    if (event.target.closest('a')) setMenu(false);
  });
}

const search = document.getElementById('tool-search');
if (search) {
  const searchField = document.getElementById('store-search');
  const chipGroup = document.getElementById('category-chips');
  const chips = chipGroup ? [...chipGroup.querySelectorAll('[data-category]')] : [];
  const rows = [...document.querySelectorAll('.app-row[data-tool]')];
  const count = document.getElementById('result-count');
  const featured = document.getElementById('featured');
  const empty = document.getElementById('empty-results');
  let category = '';
  function filter() {
    const words = search.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
    let visible = 0;
    for (const row of rows) {
      const match = words.every(word => row.dataset.search.includes(word)) &&
        (!category || row.dataset.category === category);
      row.hidden = !match;
      if (match) visible += 1;
    }
    count.textContent = `${visible} ${visible === 1 ? 'tool' : 'tools'}`;
    for (const chip of chips) {
      chip.setAttribute('aria-pressed', String(chip.dataset.category === category));
    }
    // Recommendations are a starting point, so they step aside once a reader narrows the list.
    if (featured) featured.hidden = Boolean(category || words.length);
    empty.hidden = visible !== 0;
  }
  search.addEventListener('input', filter);
  for (const chip of chips) {
    chip.addEventListener('click', () => {
      category = chip.dataset.category;
      filter();
    });
  }
  empty.querySelector('[data-clear]').addEventListener('click', () => {
    search.value = '';
    category = '';
    filter();
    search.focus();
  });
  searchField.hidden = false;
  if (chipGroup) chipGroup.hidden = false;
  filter();
}

const demoVideos = [...document.querySelectorAll('[data-demo-video]')];
if (demoVideos.length) {
  const preference = window.matchMedia('(prefers-reduced-motion: reduce)');
  const toggles = [...document.querySelectorAll('[data-motion-toggle]')];
  let paused = preference.matches;
  function setMotion() {
    for (const video of demoVideos) {
      if (paused) video.pause();
      else video.play().catch(() => { /* Native controls remain available. */ });
    }
    for (const button of toggles) {
      button.hidden = false;
      button.textContent = paused ? 'Play motion' : 'Pause motion';
      button.setAttribute('aria-pressed', String(paused));
    }
  }
  for (const button of toggles) {
    button.addEventListener('click', () => { paused = !paused; setMotion(); });
  }
  preference.addEventListener('change', event => { paused = event.matches; setMotion(); });
  setMotion();
}
