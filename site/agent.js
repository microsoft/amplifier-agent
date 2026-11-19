/* Progressive enhancement only. Every code sample is on the page without JavaScript. */

for (const tabs of document.querySelectorAll('[data-tabs]')) {
  const buttons = [...tabs.querySelectorAll('[role="tab"]')];
  const select = chosen => {
    for (const button of buttons) {
      const selected = button === chosen;
      button.setAttribute('aria-selected', String(selected));
      button.tabIndex = selected ? 0 : -1;
      document.getElementById(button.getAttribute('aria-controls')).hidden = !selected;
    }
  };
  for (const [index, button] of buttons.entries()) {
    button.addEventListener('click', () => select(button));
    button.addEventListener('keydown', event => {
      const step = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
      if (!step) return;
      const next = buttons[(index + step + buttons.length) % buttons.length];
      select(next);
      next.focus();
    });
  }
  // An address that ends in a tab's id opens the page on that tab.
  select(buttons.find(button => `#${button.id}` === location.hash) ?? buttons[0]);
}

for (const button of document.querySelectorAll('[data-copy-sample]')) {
  let reset;
  button.addEventListener('click', async () => {
    const code = document.getElementById(button.dataset.copySample);
    const status = button.closest('.copy-region').querySelector('[role="status"]');
    try {
      await navigator.clipboard.writeText(code.textContent);
      status.textContent = 'Copied.';
      clearTimeout(reset);
      reset = setTimeout(() => { status.textContent = ''; }, 1600);
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

/* Context Intelligence: a drawing plays when it comes into view, and again each time its tab opens. */
if ('IntersectionObserver' in window && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
  const watcher = new IntersectionObserver(entries => {
    for (const entry of entries) {
      if (entry.isIntersecting) entry.target.classList.add('play');
      // A drawing in a closed tab has no box. It starts over when its tab opens again.
      else if (!entry.target.offsetParent) entry.target.classList.remove('play');
    }
  }, { threshold: 0.2 });
  for (const figure of document.querySelectorAll('[data-recall]')) {
    figure.classList.add('armed');
    watcher.observe(figure);
  }
}

// The questions are a column, so the up and down arrows also move between them.
for (const list of document.querySelectorAll('.recall-tabs [role="tablist"]')) {
  list.addEventListener('keydown', event => {
    const step = { ArrowDown: 1, ArrowUp: -1 }[event.key];
    if (!step) return;
    event.preventDefault();
    const tabs = [...list.querySelectorAll('[role="tab"]')];
    const next = tabs[(tabs.indexOf(document.activeElement) + step + tabs.length) % tabs.length];
    next.click();
    next.focus();
  });
}

/* Long running work and delegation. One note shows at a time. A pin or a button chooses it. */
for (const record of document.querySelectorAll('[data-moments]')) {
  const notes = [...record.querySelectorAll('.long-moments > li')];
  const marks = record.querySelectorAll('.long-pin, .long-guides span');
  let shown = 0;
  const show = index => {
    shown = (index + notes.length) % notes.length;
    for (const [place, note] of notes.entries()) note.classList.toggle('away', place !== shown);
    for (const mark of marks) mark.classList.toggle('lit', mark.dataset.moment === notes[shown].dataset.moment);
  };
  for (const pin of record.querySelectorAll('.long-pin')) {
    pin.addEventListener('click', event => {
      event.preventDefault();
      show(Number(pin.dataset.moment) - 1);
    });
  }
  for (const button of record.querySelectorAll('[data-step]')) {
    button.addEventListener('click', () => show(shown + Number(button.dataset.step)));
  }
  // An address that ends in a note's id opens the page on that note.
  show(Math.max(0, notes.findIndex(note => `#${note.id}` === location.hash)));
}

/* Built with Amplifier Agent: the row of projects scrolls and stops at each slide without this script.
   This shows the buttons and the marks, and moves the row one slide for each arrow key. */
for (const carousel of document.querySelectorAll('[data-carousel]')) {
  const track = carousel.querySelector('.carousel-track');
  const slides = [...track.children];
  const controls = carousel.querySelector('.carousel-controls');
  const [back, forward] = controls.querySelectorAll('.carousel-step');
  const marks = slides.map(() => controls.querySelector('.carousel-marks').appendChild(document.createElement('span')));
  // The first slide that starts at or after the left edge of the view.
  const first = () => {
    const index = slides.findIndex(slide => slide.offsetLeft >= track.scrollLeft - 1);
    return index < 0 ? slides.length - 1 : index;
  };
  const inView = slide => slide.offsetLeft >= track.scrollLeft - 1
    && slide.offsetLeft + slide.offsetWidth <= track.scrollLeft + track.clientWidth + 1;
  const show = index => {
    track.scrollTo({ left: slides[Math.max(0, Math.min(index, slides.length - 1))].offsetLeft });
  };
  const update = () => {
    for (const [index, slide] of slides.entries()) marks[index].classList.toggle('shown', inView(slide));
    back.setAttribute('aria-disabled', String(track.scrollLeft <= 1));
    forward.setAttribute('aria-disabled', String(track.scrollLeft + track.clientWidth >= track.scrollWidth - 1));
  };
  // A button moves the row by the slides that fit in the view.
  const page = way => {
    show(first() + way * Math.max(1, slides.filter(inView).length));
  };
  back.addEventListener('click', () => page(-1));
  forward.addEventListener('click', () => page(1));
  track.addEventListener('keydown', event => {
    if (event.target !== track) return;
    const index = { ArrowRight: first() + 1, ArrowLeft: first() - 1, Home: 0, End: slides.length - 1 }[event.key];
    if (index === undefined) return;
    event.preventDefault();
    show(index);
  });
  track.addEventListener('scroll', update, { passive: true });
  window.addEventListener('resize', update);
  controls.hidden = false;
  update();
}

/* And the parts you expect: without this script every slide shows. This shows one slide at a time and the buttons.
   The slides change every 5 s while the drawing is in view. A mouse over the drawing, or the keyboard focus in it,
   holds the slide. A tap does not hold it, so a phone keeps changing the slides after the finger lifts.
   With reduced motion the slides change only by the buttons. */
for (const spokes of document.querySelectorAll('[data-spokes]')) {
  const holder = spokes.querySelector('.spoke-slides');
  const slides = [...holder.children];
  const controls = spokes.querySelector('.spoke-controls');
  const marks = [...controls.querySelectorAll('.spoke-mark')];
  const toggle = controls.querySelector('[data-spoke-play]');
  const [back, forward] = controls.querySelectorAll('.spoke-step:not([data-spoke-play])');
  let shown = 0;
  let timer;
  let stopped = matchMedia('(prefers-reduced-motion: reduce)').matches;
  let held = false;
  let seen = false;
  const show = index => {
    shown = (index + slides.length) % slides.length;
    for (const [place, slide] of slides.entries()) {
      const current = place === shown;
      slide.hidden = !current;
      slide.classList.remove('enter');
      if (current) {
        // Reading the width starts the animation of the slide again.
        void slide.offsetWidth;
        slide.classList.add('enter');
      }
    }
    for (const [place, mark] of marks.entries()) mark.setAttribute('aria-current', String(place === shown));
  };
  const schedule = () => {
    clearInterval(timer);
    const turning = !stopped && !held && seen;
    // A screen reader reads a slide that changes only when a person changes it.
    holder.setAttribute('aria-live', turning ? 'off' : 'polite');
    if (turning) timer = setInterval(() => show(shown + 1), 5000);
  };
  const setStopped = value => {
    stopped = value;
    spokes.classList.toggle('stopped', stopped);
    toggle.setAttribute('aria-label', stopped ? 'Play slides' : 'Pause slides');
    schedule();
  };
  // A button restarts the 5 s, so a slide that a person opens stays for the full time.
  const step = index => {
    show(index);
    schedule();
  };
  back.addEventListener('click', () => step(shown - 1));
  forward.addEventListener('click', () => step(shown + 1));
  for (const [place, mark] of marks.entries()) mark.addEventListener('click', () => step(place));
  toggle.addEventListener('click', () => setStopped(!stopped));
  // Only a change restarts the 5 s, so taps and scrolls elsewhere on the page do not keep putting off the next slide.
  const hold = value => {
    if (held === value) return;
    held = value;
    schedule();
  };
  // Only a mouse hovers. A touch also sends enter and leave, and a phone may never send the leave.
  let over = false;
  const typing = () => spokes.contains(document.activeElement) && document.activeElement.matches(':focus-visible');
  spokes.addEventListener('pointerenter', event => {
    over = event.pointerType === 'mouse';
    hold(over || typing());
  });
  spokes.addEventListener('pointerleave', () => {
    over = false;
    hold(typing());
  });
  // A tap or a click focuses a button without a visible ring. That focus does not hold the slide.
  spokes.addEventListener('focusin', () => hold(over || typing()));
  // Focus that moves inside the drawing is judged when it arrives.
  spokes.addEventListener('focusout', event => {
    if (!spokes.contains(event.relatedTarget)) hold(over);
  });
  // A tap or a click elsewhere on the page lets the slides go on.
  document.addEventListener('pointerdown', event => {
    if (spokes.contains(event.target)) return;
    over = false;
    hold(false);
  });
  if ('IntersectionObserver' in window) {
    new IntersectionObserver(entries => {
      seen = entries.at(-1).isIntersecting;
      schedule();
    }, { threshold: 0.3 }).observe(spokes);
  } else {
    seen = true;
  }
  controls.hidden = false;
  show(0);
  setStopped(stopped);
}

/* The videos: without this script every video shows under its title. This shows one video at a time, with a tab
   for each. A video plays muted while the carousel is in view, and when it ends the next one plays, and after the
   last the first. With reduced motion a video plays only when a person plays it. Only the video that shows loads,
   and the next one loads its first data shortly before it starts. The next video keeps the sound, speed, and
   captions of the one before. */
for (const reel of document.querySelectorAll('[data-reel]')) {
  const figures = [...reel.querySelectorAll('.reel-video')];
  const videos = figures.map(figure => figure.querySelector('video'));
  const tabs = [...reel.querySelectorAll('[role="tab"]')];
  const bars = tabs.map(tab => tab.querySelector('.reel-progress span'));
  const still = matchMedia('(prefers-reduced-motion: reduce)').matches;
  let shown = 0;
  let seen = false;
  // A video that a person paused stays paused when the carousel comes back into view.
  let resume = !still;
  const show = (index, play) => {
    const old = videos[shown];
    old.pause();
    old.currentTime = 0;
    shown = (index + videos.length) % videos.length;
    const video = videos[shown];
    video.muted = old.muted;
    video.volume = old.volume;
    video.defaultPlaybackRate = video.playbackRate = old.playbackRate;
    video.textTracks[0].mode = old.textTracks[0].mode;
    for (const [number, figure] of figures.entries()) {
      figure.hidden = number !== shown;
      tabs[number].setAttribute('aria-selected', String(number === shown));
      tabs[number].tabIndex = number === shown ? 0 : -1;
    }
    video.preload = 'auto';
    if (play) video.play().catch(() => {});
  };
  for (const [index, video] of videos.entries()) {
    video.addEventListener('timeupdate', () => {
      bars[index].style.setProperty('--done', video.duration ? video.currentTime / video.duration : 0);
      const next = videos[(index + 1) % videos.length];
      if (video.duration - video.currentTime < 8 && next.preload === 'none') next.preload = 'metadata';
    });
    video.addEventListener('ended', () => show(index + 1, !still));
  }
  for (const [index, tab] of tabs.entries()) {
    tab.addEventListener('click', () => show(index, true));
    tab.addEventListener('keydown', event => {
      const to = { ArrowRight: index + 1, ArrowLeft: index - 1, Home: 0, End: tabs.length - 1 }[event.key];
      if (to === undefined) return;
      event.preventDefault();
      show(to, true);
      tabs[shown].focus();
    });
  }
  // The carousel is in view when at least half of it shows.
  new IntersectionObserver(entries => {
    const now = entries.at(-1).intersectionRatio >= 0.5;
    if (now === seen) return;
    seen = now;
    if (!seen) {
      resume = !videos[shown].paused;
      videos[shown].pause();
    } else if (resume) videos[shown].play().catch(() => {});
  }, { threshold: [0, 0.25, 0.5, 0.75, 1] }).observe(reel);
  reel.querySelector('.reel-tabs').hidden = false;
  show(0, false);
}
