(() => {
  'use strict';

  const main = document.getElementById('main');
  const SVG_NS = 'http://www.w3.org/2000/svg';
  const ID_KEY = 'edumind.learner';

  // ---------------------------------------------------------------- utilities
  const mem = {};
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch { return mem[k] ?? null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch { mem[k] = v; } },
    del(k) { try { localStorage.removeItem(k); } catch { delete mem[k]; } },
  };

  // Tiny accessible DOM builder. Text is inserted as text nodes, never as raw HTML.
  function h(tag, props, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (v === undefined || v === null || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k === 'text') el.textContent = v;
      else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? '' : v);
    }
    for (const kid of kids.flat()) {
      if (kid === null || kid === undefined || kid === false) continue;
      el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
    }
    return el;
  }

  // SVG helper
  function svgEl(tag, attrs = {}) {
    const el = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, String(v));
    return el;
  }

  async function request(path, opts = {}) {
    const init = { method: opts.method || 'GET', headers: { 'Content-Type': 'application/json' } };
    if (opts.body !== undefined) init.body = JSON.stringify(opts.body);
    let res;
    try {
      res = await fetch(path, init);
    } catch {
      throw Object.assign(new Error('We could not reach the server right now. Please check your connection and try again.'), { status: 0 });
    }
    let data = null;
    try { data = await res.json(); } catch { /* no body */ }
    if (!res.ok) {
      let msg = data && data.detail;
      if (Array.isArray(msg)) msg = 'Please check your answer and try again.';
      throw Object.assign(new Error(msg || `Something went wrong (${res.status}).`), { status: res.status });
    }
    return data;
  }

  let learnerId = store.get(ID_KEY);
  async function ensureLearner() {
    if (learnerId) return learnerId;
    const r = await request('/api/learners', { method: 'POST' });
    learnerId = r.learner_id;
    store.set(ID_KEY, learnerId);
    return learnerId;
  }

  async function withLearner(fn) {
    await ensureLearner();
    try {
      return await fn(learnerId);
    } catch (e) {
      if (e.status === 404 && /Unknown learner/.test(e.message)) {
        store.del(ID_KEY);
        learnerId = null;
        await ensureLearner();
        return fn(learnerId);
      }
      throw e;
    }
  }

  function view(...nodes) {
    main.replaceChildren(...nodes.filter(Boolean));
    main.focus({ preventScroll: true });
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  // Sticky header shadow handler
  const header = document.getElementById('site-header');
  if (header) {
    let ticking = false;
    window.addEventListener('scroll', () => {
      if (!ticking) {
        requestAnimationFrame(() => {
          header.classList.toggle('scrolled', window.scrollY > 8);
          ticking = false;
        });
        ticking = true;
      }
    }, { passive: true });
  }

  // ---------------------------------------------------- visual components
  function spinner(msg) {
    return h('div', { class: 'spinner-wrap', role: 'status', 'aria-live': 'polite' },
      h('div', { class: 'spinner' }),
      h('p', { class: 'spinner-text', text: msg || 'Loading…' }));
  }

  function failure(e, retry) {
    view(h('div', { class: 'card error', role: 'alert' },
      h('h2', { text: 'Something went wrong' }),
      h('p', { text: e.message || 'We ran into an unexpected issue.' }),
      h('div', { class: 'actions' },
        retry ? h('button', { class: 'btn', type: 'button', onclick: retry, text: 'Try again' }) : null,
        h('button', { class: 'btn secondary', type: 'button', onclick: showDashboard, text: 'Back to Dashboard' }))));
  }

  // TRACE Flow Progress Tracker: Try (Answer) -> Think -> Learn -> Retry
  function stepTracker(activeStep) {
    const steps = [
      { key: 'try',   label: 'Answer', num: '1' },
      { key: 'think', label: 'Think',  num: '2' },
      { key: 'learn', label: 'Learn',  num: '3' },
      { key: 'retry', label: 'Retry',  num: '4' },
    ];
    const activeIdx = steps.findIndex(s => s.key === activeStep);
    const nodes = [];
    steps.forEach((s, i) => {
      const cls = i < activeIdx ? 'step done' : i === activeIdx ? 'step active' : 'step';
      const icon = i < activeIdx ? '✓' : s.num;
      nodes.push(
        h('div', { class: cls },
          h('span', { class: 'step-icon', text: icon }),
          h('span', { class: 'step-label', text: s.label }))
      );
      if (i < steps.length - 1) {
        nodes.push(h('div', { class: 'step-line' + (i < activeIdx ? ' done' : '') }));
      }
    });
    return h('div', { class: 'step-tracker', role: 'navigation', 'aria-label': 'Learning progress through this question' }, nodes);
  }

  // ---------------------------------------------------- fraction visual aids
  function frac(n, d) {
    return h('span', { class: 'frac', role: 'img', 'aria-label': `${n} over ${d}` },
      h('span', { class: 'n', text: n }),
      h('span', { class: 'd', text: d }));
  }

  function barSvg(parts, shaded, label) {
    const W = 300, H = 28, w = W / parts;
    const svg = svgEl('svg', {
      viewBox: `0 0 ${W} ${H}`,
      class: 'bar',
      role: 'img',
      'aria-label': label || `A bar split into ${parts} equal parts with ${shaded} shaded`
    });
    for (let i = 0; i < parts; i++) {
      const r = svgEl('rect', {
        x: i * w,
        y: 1,
        width: w,
        height: H - 2,
        rx: 3,
        class: i < shaded ? 'seg on' : 'seg'
      });
      svg.append(r);
    }
    return svg;
  }

  function display(d) {
    if (d.type === 'bar') return barSvg(d.parts, d.shaded);
    const out = [];
    for (const p of d.parts) {
      if (p.f) out.push(frac(p.f[0], p.f[1]));
      else if (p.blank) out.push(h('span', { class: 'frac', role: 'img', 'aria-label': `blank over ${p.over}` },
        h('span', { class: 'n' }, h('span', { class: 'blank-box' })),
        h('span', { class: 'd', text: p.over })));
      else out.push(h('span', { class: 'op', 'aria-hidden': 'true', text: p.op }));
    }
    return out;
  }

  function barList(bars) {
    if (!bars || !bars.length) return null;
    return h('div', { class: 'bars', role: 'group', 'aria-label': 'Bar pictures of the fractions in the example' },
      bars.map(b => h('div', { class: 'barrow' },
        h('span', { class: 'lab', text: b.label }),
        barSvg(b.parts, b.shaded, `${b.label}: ${b.shaded} of ${b.parts} equal parts shaded`),
        b.caption ? h('span', { class: 'cap', text: b.caption }) : null)));
  }

  // ---------------------------------------------------- question workspace card
  function questionCard(q, o = {}) {
    const err = h('div', { class: 'field-error', role: 'alert', 'aria-live': 'assertive' });
    const submitBtn = h('button', { class: 'btn', type: 'submit', text: o.submitLabel || 'Check my answer' });
    let getValue;
    let inputArea;

    if (q.input_type === 'choice') {
      const name = 'choice-' + q.id;
      const wide = q.kind === 'compare';
      inputArea = h('fieldset', null,
        h('legend', { text: 'Choose one answer:' }),
        h('div', { class: 'opts' + (wide ? ' big' : '') },
          q.choices.map((c, i) => h('label', { class: 'opt' },
            h('input', { type: 'radio', name, value: c.value, id: `${name}-${i}` }),
            c.frac ? frac(c.frac[0], c.frac[1]) : h('span', { text: c.label || c.value })))));
      getValue = () => {
        const el = inputArea.querySelector('input:checked');
        return el ? el.value : '';
      };
    } else {
      const input = h('input', {
        type: 'text',
        id: 'answer',
        name: 'answer',
        autocomplete: 'off',
        autocapitalize: 'off',
        spellcheck: 'false',
        maxlength: '16',
        'aria-describedby': 'answer-hint',
        inputmode: q.input_type === 'integer' ? 'numeric' : 'text',
        placeholder: q.input_type === 'integer' ? 'e.g. 4' : 'e.g. 3/4',
      });
      inputArea = h('div', { class: 'field' },
        h('label', { for: 'answer', text: 'Your answer' }),
        input,
        h('span', { id: 'answer-hint', class: 'hintline',
          text: q.input_type === 'integer' ? 'Type a whole number.' : 'Type a fraction like 3/4, or a whole number.' }));
      getValue = () => input.value.trim();
    }

    const breadcrumb = h('nav', { class: 'breadcrumb', 'aria-label': 'Breadcrumb' },
      h('button', { type: 'button', onclick: showLearn, text: 'Topics' }),
      h('span', { text: '/' }),
      h('span', { text: q.skill_name || 'Practice' }));

    const reassurance = h('div', { class: 'reassurance-banner' },
      svgEl('svg', { width: '16', height: '16', viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '2' },
        svgEl('circle', { cx: '12', cy: '12', r: '10' }),
        svgEl('path', { d: 'M12 16v-4' }),
        svgEl('path', { d: 'M12 8h.01' })),
      h('span', { text: o.banner || "Let's figure this out together. Mistakes are how we learn." }));

    const form = h('form', { class: 'card', novalidate: true },
      breadcrumb,
      h('div', { class: 'q-head' },
        h('span', { class: 'chip primary', text: q.skill_name }),
        o.badge ? h('span', { class: 'chip teal', text: o.badge }) : null,
        !o.badge && q.skill_level && q.skill_level !== 'Not started'
          ? h('span', { class: 'chip ok', text: q.skill_level }) : null),
      reassurance,
      h('p', { class: 'prompt', text: q.prompt }),
      h('div', { class: 'visual', 'aria-hidden': 'true' }, display(q.display)),
      inputArea,
      err,
      h('div', { class: 'actions' },
        submitBtn,
        o.secondary || null));

    form.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const value = getValue();
      if (!value) {
        err.textContent = q.input_type === 'choice' ? 'Please choose an option to continue.' : 'Please enter an answer first.';
        return;
      }
      err.textContent = '';
      submitBtn.disabled = true;
      submitBtn.textContent = 'Checking answer…';
      try {
        await o.onSubmit(value);
      } catch (e) {
        submitBtn.disabled = false;
        submitBtn.textContent = o.submitLabel || 'Check my answer';
        if (e.status === 422 || e.status === 400) {
          err.textContent = e.message;
        } else {
          failure(e);
        }
      }
    });

    return form;
  }

  // ---------------------------------------------------- Skill Icons
  const SKILL_ICONS = {
    'add': '➕',
    'subtract': '➖',
    'compare': '⚖️',
    'equivalent': '🔄',
    'name': '🎨',
  };

  // ---------------------------------------------------- GAMIFICATION COMPONENTS
  function triggerConfetti() {
    if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      return;
    }
    const canvas = document.createElement('canvas');
    canvas.className = 'confetti-canvas';
    canvas.setAttribute('aria-hidden', 'true');
    document.body.appendChild(canvas);

    const ctx = canvas.getContext('2d');
    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight;

    const colors = ['#2864A8', '#0EA5E9', '#10B981', '#F59E0B', '#8B5CF6', '#EC4899'];
    const particles = [];
    const count = 48;

    for (let i = 0; i < count; i++) {
      particles.push({
        x: canvas.width * 0.5 + (Math.random() - 0.5) * 200,
        y: canvas.height * 0.35 + (Math.random() - 0.5) * 60,
        vx: (Math.random() - 0.5) * 8,
        vy: -Math.random() * 6 - 3,
        size: Math.random() * 6 + 4,
        color: colors[Math.floor(Math.random() * colors.length)],
        rotation: Math.random() * 360,
        vRot: (Math.random() - 0.5) * 10,
      });
    }

    let start = null;
    const duration = 1200;

    function frame(t) {
      if (!start) start = t;
      const elapsed = t - start;
      if (elapsed > duration) {
        canvas.remove();
        return;
      }
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      const alpha = Math.max(0, 1 - elapsed / duration);

      for (const p of particles) {
        p.x += p.vx;
        p.y += p.vy;
        p.vy += 0.22;
        p.rotation += p.vRot;

        ctx.save();
        ctx.globalAlpha = alpha;
        ctx.translate(p.x, p.y);
        ctx.rotate((p.rotation * Math.PI) / 180);
        ctx.fillStyle = p.color;
        ctx.fillRect(-p.size / 2, -p.size / 2, p.size, p.size * 0.7);
        ctx.restore();
      }
      requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
  }

  function showBadgeUnlockedModal(badges) {
    if (!badges || !badges.length) return;
    const modalBackdrop = h('div', {
      class: 'modal-backdrop',
      role: 'dialog',
      'aria-modal': 'true',
      'aria-labelledby': 'badge-modal-title',
    });

    const isSingle = badges.length === 1;
    const first = badges[0];

    const bodyContent = isSingle
      ? [
          h('div', { class: 'badge-modal-icon-big' }, first.icon || '🏆'),
          h('h2', { id: 'badge-modal-title', class: 'badge-modal-title', text: first.name }),
          h('p', { class: 'badge-modal-desc', text: first.description }),
        ]
      : [
          h('h2', { id: 'badge-modal-title', class: 'badge-modal-title', text: `${badges.length} Badges Unlocked! 🎉` }),
          h('div', { class: 'modal-badges-carousel' }, badges.map(b => h('div', { class: 'modal-badge-item' },
            h('span', { class: 'modal-badge-item-icon', text: b.icon || '🏆' }),
            h('div', { class: 'modal-badge-item-info' },
              h('strong', { text: b.name }),
              h('span', { text: b.description }))))),
        ];

    const modalBox = h('div', { class: 'badge-modal-box' },
      h('div', { class: 'badge-modal-stars', text: '✨ 🌟 ✨' }),
      ...bodyContent,
      h('div', { class: 'actions', style: 'justify-content: center;' },
        h('button', {
          class: 'btn btn-lg',
          type: 'button',
          onclick: () => modalBackdrop.remove(),
          text: 'Awesome! Keep learning',
        }))
    );

    modalBackdrop.append(modalBox);
    modalBackdrop.addEventListener('click', (e) => {
      if (e.target === modalBackdrop) modalBackdrop.remove();
    });
    document.body.append(modalBackdrop);
  }

  function streakIndicator(streak) {
    if (!streak) return null;

    const calendarNodes = (streak.weekly_calendar || []).map(day => {
      const dotCls = 'calendar-day-dot' + (day.active ? ' active' : '') + (day.is_today ? ' is-today' : '');
      const icon = day.active ? '✓' : '';
      return h('div', { class: 'calendar-day' },
        h('span', { class: 'calendar-day-name', text: day.day_name }),
        h('span', { class: dotCls, text: icon, title: `${day.date}: ${day.active ? 'Learned' : 'No activity'}` }));
    });

    return h('div', { class: 'streak-card', 'aria-label': 'Daily Learning Streak' },
      h('div', { class: 'streak-card-header' },
        h('div', { class: 'streak-badge-main' },
          h('span', { class: 'streak-flame-icon', 'aria-hidden': 'true', text: streak.current > 0 ? '🔥' : '🌱' }),
          h('div', { class: 'streak-title-wrap' },
            h('h3', { text: streak.current > 0 ? `${streak.current}-Day Streak` : 'Start your streak today' }),
            h('p', { text: streak.message }))),
        h('div', { class: 'streak-stats-pill' },
          h('span', { text: `🔥 Current: ${streak.current}` }),
          h('span', { text: `⚡ Best: ${streak.longest}` }))),
      h('div', { class: 'weekly-calendar', 'aria-label': 'Past 7 days activity' }, calendarNodes));
  }

  function dailyQuestsSection(quests) {
    if (!quests || !quests.length) return null;

    const questCards = quests.map(q => {
      const isCompleted = q.status === 'Completed';
      const pct = Math.min(100, Math.round((q.progress / q.target) * 100));

      const statusChipClass = isCompleted ? 'chip ok' : q.progress > 0 ? 'chip teal' : 'chip';
      const actionBtn = isCompleted
        ? h('button', { class: 'btn secondary', type: 'button', disabled: true, text: 'Completed ✓' })
        : h('button', {
            class: 'btn',
            type: 'button',
            onclick: () => startQuiz(null),
            text: q.progress > 0 ? 'Continue Quest →' : 'Start Quest →',
          });

      return h('div', { class: 'quest-card' + (isCompleted ? ' completed' : '') },
        h('div', null,
          h('div', { class: 'quest-card-top' },
            h('h3', { class: 'quest-title', text: q.title }),
            h('span', { class: statusChipClass, text: q.status })),
          h('p', { class: 'quest-desc', text: q.description }),
          q.reward_badge ? h('div', { class: 'quest-reward-pill' },
            h('span', { text: q.reward_badge.icon || '🏆' }),
            h('span', { text: `Reward: ${q.reward_badge.name}` })) : null),
        h('div', null,
          h('div', { class: 'quest-progress-wrap' },
            h('div', { class: 'quest-progress-bar-bg' },
              h('div', { class: 'quest-progress-bar-fill', style: `width: ${pct}%;` })),
            h('div', { class: 'quest-progress-text' },
              h('span', { text: q.objective }),
              h('span', { text: `${q.progress} / ${q.target}` }))),
          h('div', { class: 'quest-card-footer' }, actionBtn)));
    });

    return h('div', { class: 'quests-section' },
      h('div', { class: 'quests-section-header' },
        h('h2', null,
          h('span', { 'aria-hidden': 'true', text: '🎯' }),
          h('span', { text: 'Today’s Daily Quests' })),
        h('span', { class: 'chip primary', text: 'Resets daily' })),
      h('div', { class: 'quests-grid' }, questCards));
  }

  function badgeCollectionSection(badges, earnedCount, totalCount) {
    if (!badges || !badges.length) return null;

    const cards = badges.map(b => {
      const isEarned = b.earned;
      const cardCls = 'badge-card' + (isEarned ? ' earned' : ' locked');

      const datePill = isEarned && b.awarded_at
        ? h('span', { class: 'badge-date-pill', text: `Earned ${new Date(b.awarded_at).toLocaleDateString()}` })
        : h('span', { class: 'badge-lock-pill' },
            h('svg', { width: '12', height: '12', viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '2.5' },
              svgEl('rect', { x: '3', y: '11', width: '18', height: '11', rx: '2', ry: '2' }),
              svgEl('path', { d: 'M7 11V7a5 5 0 0110 0v4' })),
            h('span', { text: 'Locked' }));

      return h('div', { class: cardCls },
        h('div', { class: 'badge-icon-wrap' }, b.icon || '🏆'),
        h('h4', { class: 'badge-name', text: b.name }),
        h('p', { class: 'badge-condition', text: isEarned ? b.description : `Unlock: ${b.unlock_condition}` }),
        datePill);
    });

    return h('div', { class: 'badges-section' },
      h('div', { class: 'quests-section-header' },
        h('h2', null,
          h('span', { 'aria-hidden': 'true', text: '🏆' }),
          h('span', { text: 'Achievement Badges' })),
        h('span', { class: 'chip ok', text: `${earnedCount || 0} of ${totalCount || badges.length} unlocked` })),
      h('div', { class: 'badges-grid' }, cards));
  }

  // ---------------------------------------------------- VIEWS
  function setActiveNav(navId) {
    document.querySelectorAll('nav .nav-btn').forEach(btn => {
      btn.classList.toggle('active', btn.id === navId);
    });
  }

  // VIEW 1: STUDENT DASHBOARD (FR-02)
  async function showDashboard() {
    setActiveNav('nav-dashboard');
    view(spinner('Loading your learning dashboard…'));

    let skills = [];
    let progressData = null;
    let gamificationData = null;
    try {
      skills = await request('/api/skills');
      progressData = await withLearner(id => request(`/api/progress?learner_id=${id}`));
      gamificationData = await withLearner(id => request(`/api/gamification/summary?learner_id=${id}`));
    } catch (e) {
      return failure(e, showDashboard);
    }

    const attemptsCount = progressData?.totals?.attempts || 0;
    const correctCount = progressData?.totals?.correct || 0;
    const accuracy = attemptsCount > 0 ? Math.round((correctCount * 100) / attemptsCount) : 0;
    const isReturning = attemptsCount > 0;

    const heroCard = h('div', { class: 'hero-box' },
      h('div', { class: 'hero-badge', text: isReturning ? 'Welcome Back' : 'Get Started' }),
      h('h1', { text: isReturning ? 'Ready to continue learning?' : 'Fractions, understood.' }),
      h('p', { class: 'lead', text: isReturning
        ? "Pick up where you left off or start a fresh mixed practice session. When you make a mistake, we'll help you unpack the reasoning step by step."
        : "Practise with guided feedback that explains what happened and why, without judgement. Build confidence one concept at a time." }),
      h('div', { class: 'actions' },
        h('button', { class: 'btn btn-lg', type: 'button', onclick: () => startPractice(null), text: 'Start mixed practice' }),
        h('button', { class: 'btn btn-lg', type: 'button', style: 'background: #0284c7;', onclick: () => startQuiz(null), text: 'Take a quiz 🎯' }),
        h('button', { class: 'btn btn-lg secondary', type: 'button', onclick: showLearn, text: 'Browse topics' })));

    const streakWidget = gamificationData?.streak ? streakIndicator(gamificationData.streak) : null;
    const questsWidget = gamificationData?.quests ? dailyQuestsSection(gamificationData.quests) : null;

    let snapshotSection = null;
    if (isReturning) {
      snapshotSection = h('div', { class: 'statrow', 'aria-label': 'Learning statistics' },
        h('div', { class: 'stat' },
          h('b', { text: String(attemptsCount) }),
          h('span', { text: 'questions attempted' })),
        h('div', { class: 'stat' },
          h('b', { text: `${accuracy}%` }),
          h('span', { text: 'answered correctly' })),
        h('div', { class: 'stat' },
          h('b', { text: String(progressData.misconceptions?.length || 0) }),
          h('span', { text: 'concepts worked on' })));
    }

    const topicsOverview = h('div', { class: 'card' },
      h('div', { class: 'card-header' },
        h('h2', { text: 'Topics to explore' }),
        h('button', { class: 'link', type: 'button', onclick: showLearn, text: 'View all topics →' })),
      h('div', { class: 'grid' },
        skills.slice(0, 4).map(s => h('div', { class: 'topic-card', onclick: () => startPractice(s.id) },
          h('div', { class: 'topic-card-top' },
            h('span', { class: 'topic-icon', text: SKILL_ICONS[s.id] || '📐' })),
          h('strong', { text: s.name }),
          h('p', { text: s.blurb }),
          h('div', { class: 'topic-footer' },
            h('span', { class: 'link', text: 'Practise →' }))))));

    const badgesWidget = gamificationData?.badges ? badgeCollectionSection(gamificationData.badges, gamificationData.earned_count, gamificationData.total_badges) : null;

    view(heroCard, streakWidget, questsWidget, snapshotSection, topicsOverview, badgesWidget);
  }

  // VIEW 2: LEARN / TOPIC BROWSER (FR-03)
  async function showLearn() {
    setActiveNav('nav-learn');
    view(spinner('Loading topics…'));

    let skills = [];
    let progressData = null;
    try {
      skills = await request('/api/skills');
      progressData = await withLearner(id => request(`/api/progress?learner_id=${id}`));
    } catch (e) {
      return failure(e, showLearn);
    }

    const skillLevels = {};
    if (progressData?.skills) {
      progressData.skills.forEach(s => { skillLevels[s.id] = s.level; });
    }

    let filterText = '';
    const container = h('div');

    function renderGrid() {
      const filtered = skills.filter(s =>
        s.name.toLowerCase().includes(filterText.toLowerCase()) ||
        s.blurb.toLowerCase().includes(filterText.toLowerCase())
      );

      const gridNodes = filtered.map(s => {
        const level = skillLevels[s.id] || 'Not started';
        const isSecure = level === 'Secure';
        const isGettingThere = level === 'Getting there';
        const levelChipClass = isSecure ? 'chip ok' : isGettingThere ? 'chip teal' : 'chip';

        return h('div', { class: 'topic-card', onclick: () => startPractice(s.id) },
          h('div', { class: 'topic-card-top' },
            h('span', { class: 'topic-icon', text: SKILL_ICONS[s.id] || '📐' }),
            h('span', { class: levelChipClass, text: level })),
          h('strong', { text: s.name }),
          h('p', { text: s.blurb }),
          h('div', { class: 'topic-footer' },
            h('span', { class: 'btn secondary', text: 'Practise topic' })));
      });

      const listNode = filtered.length
        ? h('div', { class: 'grid' }, gridNodes)
        : h('div', { class: 'empty-state' },
            h('p', { text: 'No topics matched your search. Try another keyword.' }));

      container.replaceChildren(listNode);
    }

    const filterInput = h('input', {
      type: 'text',
      class: 'filter-input',
      placeholder: 'Search topics by name or description…',
      oninput: (e) => {
        filterText = e.target.value.trim();
        renderGrid();
      }
    });

    const headerBlock = h('div', null,
      h('h1', { text: 'Browse topics' }),
      h('p', { class: 'lead', text: 'Select any fraction skill to practice questions, review step-by-step methods, and clear up common misconceptions.' }),
      h('div', { class: 'filter-bar' }, filterInput));

    renderGrid();
    view(headerBlock, container);
  }

  // VIEW 3: PRACTICE RUNNER (FR-04)
  async function startPractice(skillId) {
    setActiveNav('nav-practice');
    view(stepTracker('try'), spinner('Finding your practice question…'));

    try {
      const q = await withLearner(id =>
        request(`/api/next-question?learner_id=${id}` + (skillId ? `&skill=${encodeURIComponent(skillId)}` : ''))
      );
      showQuestion(q, skillId);
    } catch (e) {
      failure(e, () => startPractice(skillId));
    }
  }

  function showQuestion(q, skillId) {
    view(
      stepTracker('try'),
      questionCard(q, {
        onSubmit: async (answer) => {
          const res = await withLearner(id =>
            request('/api/attempts', { method: 'POST', body: { learner_id: id, question_id: q.id, answer } })
          );
          afterAttempt(q, answer, res, skillId);
        },
        secondary: h('button', { class: 'btn secondary', type: 'button', onclick: showLearn, text: 'Back to topics' }),
      })
    );
  }

  function afterAttempt(q, answer, res, skillId) {
    if (res.correct) {
      triggerConfetti();
      view(h('div', { class: 'card success', role: 'status' },
        h('h2', { text: res.message || 'Correct — nice work!' }),
        res.note ? h('p', { text: res.note }) : null,
        res.skill_level ? h('p', { class: 'muted' }, 'Your current level in ', q.skill_name.toLowerCase(), ': ', h('strong', { text: res.skill_level }), '.') : null,
        h('div', { class: 'actions' },
          h('button', { class: 'btn', type: 'button', onclick: () => startPractice(skillId), text: 'Next question' }),
          h('button', { class: 'btn secondary', type: 'button', onclick: showDashboard, text: 'Back to Dashboard' }))));
      return;
    }

    if (res.resolved) {
      showRepair(res.session_id, res.resolved, q, answer, skillId);
      return;
    }

    showFollowup(res.session_id, res.followup, { q, answer, intro: res.message, skillId });
  }

  // VIEW 4: DIAGNOSTIC FOLLOW-UP (FR-05)
  function showFollowup(sessionId, followup, ctx) {
    const err = h('div', { class: 'field-error', role: 'alert' });
    const name = 'fu-' + followup.id;
    const submitBtn = h('button', { class: 'btn', type: 'submit', text: 'Continue' });

    const form = h('form', { class: 'card highlight', novalidate: true },
      h('div', { class: 'q-head' },
        h('span', { class: 'chip primary', text: 'Understanding your reasoning' })),
      h('p', { class: 'hintline', text: "A quick follow-up will help us understand your approach and give you more useful feedback." }),
      h('p', { class: 'muted' }, 'Question: ', ctx.q.prompt, ' — You answered: ', h('strong', { text: ctx.answer })),
      h('fieldset', null,
        h('legend', { text: followup.prompt }),
        h('div', { class: 'opts' }, followup.options.map((o, i) => h('label', { class: 'opt' },
          h('input', { type: 'radio', name, value: o.id, id: `${name}-${i}` }),
          h('span', { text: o.label }))))),
      err,
      h('div', { class: 'actions' }, submitBtn));

    form.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const picked = form.querySelector('input:checked');
      if (!picked) {
        err.textContent = 'Please choose the option that best fits your thinking.';
        return;
      }
      err.textContent = '';
      submitBtn.disabled = true;
      submitBtn.textContent = 'Checking reasoning…';
      try {
        const res = await withLearner(id => request(`/api/diagnostics/${sessionId}/responses`, {
          method: 'POST',
          body: { learner_id: id, followup_id: followup.id, option_id: picked.value }
        }));
        if (res.status === 'needs_followup') {
          showFollowup(sessionId, res.followup, ctx);
        } else {
          showRepair(sessionId, res, ctx.q, ctx.answer, ctx.skillId);
        }
      } catch (e) {
        submitBtn.disabled = false;
        submitBtn.textContent = 'Continue';
        failure(e);
      }
    });

    view(stepTracker('think'), form);
  }

  // VIEW 5: EXPLANATION & REMEDIATION (FR-06)
  function showRepair(sessionId, res, q, answer, skillId) {
    const iv = res.intervention;
    const targeted = res.outcome === 'targeted';

    const statusSection = h('div', { class: 'q-head' },
      h('span', { class: 'chip alt', text: targeted ? `Concept: ${res.pattern.name}` : 'Going over the method' }),
      h('span', { class: 'chip ' + (targeted ? 'ok' : 'primary'), text: res.confidence_indicator.label }));

    const whatSection = h('div', { class: 'remediation-section' },
      h('span', { class: 'remediation-heading', text: '1. What happened' }),
      h('p', { text: `For question “${q.prompt}”, you answered ${answer}.` }),
      h('p', { class: 'muted', text: targeted ? 'This pattern is very common when first working with fractions.' : 'Let’s walk through the concept step by step.' }));

    const whyHeadingRow = h('div', { class: 'remediation-heading-row' },
      h('span', { class: 'remediation-heading', text: '2. Why it may have happened' }),
      iv.gemini_refined ? h('span', { class: 'chip chip-gemini', text: '✨ Gemini refined' }) : null);

    const whySection = h('div', { class: 'remediation-section' },
      whyHeadingRow,
      h('p', { text: iv.why }));

    const howSection = h('div', { class: 'remediation-section' },
      h('span', { class: 'remediation-heading', text: '3. A clearer way to solve it' }),
      h('ol', { class: 'steps' }, iv.steps.map(s => h('li', { text: s }))),
      iv.example_lines && iv.example_lines.length
        ? h('div', { class: 'example' },
            h('p', { text: 'Worked Example:' }),
            iv.example_lines.map(l => h('p', { text: l })))
        : null,
      barList(iv.bars));

    const detailsAccordion = h('details', null,
      h('summary', { text: 'Why am I seeing this diagnosis?' }),
      h('ul', null, res.evidence.map(e => h('li', { text: e }))),
      h('p', { class: 'hintline', text: res.confidence_indicator.note }),
      res.ml_opinion ? h('p', { class: 'hintline' },
        h('strong', { text: 'Experimental model: ' }),
        `${res.ml_opinion.agrees ? 'agrees' : 'suggests another angle'} (${res.ml_opinion.name}, ${Math.round(res.ml_opinion.probability * 100)}%). `,
        res.ml_opinion.note) : null);

    const actions = h('div', { class: 'actions' },
      h('button', {
        class: 'btn btn-lg',
        type: 'button',
        text: 'Try a fresh question',
        onclick: () => showMastery(sessionId, res.mastery_question, null, q, skillId)
      }),
      h('button', {
        class: 'btn secondary',
        type: 'button',
        text: 'Back to topics',
        onclick: showLearn
      }));

    const card = h('div', { class: 'card' },
      statusSection,
      h('h2', { text: iv.title }),
      whatSection,
      whySection,
      howSection,
      detailsAccordion,
      actions);

    view(stepTracker('learn'), card);
  }

  // VIEW 6: MASTERY CHECK QUESTION (FR-07)
  function showMastery(sessionId, mq, hint, originalQ, skillId) {
    view(
      stepTracker('retry'),
      questionCard(mq, {
        badge: 'Fresh mastery check',
        banner: hint ? `Hint: ${hint}` : 'Here is a brand-new question to check whether the concept makes sense now.',
        submitLabel: 'Check my understanding',
        onSubmit: async (answer) => {
          const res = await withLearner(id => request('/api/mastery-checks', {
            method: 'POST',
            body: { learner_id: id, session_id: sessionId, question_id: mq.id, answer }
          }));
          afterMastery(sessionId, res, originalQ, skillId);
        },
        secondary: h('button', { class: 'btn secondary', type: 'button', onclick: showLearn, text: 'Back to topics' }),
      })
    );
  }

  function afterMastery(sessionId, res, originalQ, skillId) {
    if (res.outcome === 'mastered') {
      triggerConfetti();
      if (res.newly_unlocked_badges && res.newly_unlocked_badges.length) {
        showBadgeUnlockedModal(res.newly_unlocked_badges);
      }
      view(h('div', { class: 'card success', role: 'status' },
        h('h2', { text: 'Great progress!' }),
        h('p', { text: res.message || 'You solved the fresh question correctly!' }),
        res.note ? h('p', { text: res.note }) : null,
        h('div', { class: 'actions' },
          h('button', { class: 'btn', type: 'button', onclick: () => startPractice(skillId), text: 'Keep practising' }),
          h('button', { class: 'btn secondary', type: 'button', onclick: showProgress, text: 'See my progress' }))));
    } else if (res.outcome === 'retry') {
      showMastery(sessionId, res.mastery_question, res.hint, originalQ, skillId);
    } else {
      view(h('div', { class: 'card warning', role: 'status' },
        h('h2', { text: "Let's come back to this soon" }),
        h('p', { text: res.message || 'Learning fractions takes practice.' }),
        h('p', null, 'The correct answer to that question was ', h('strong', { text: res.answer }), '. ', res.hint || ''),
        h('div', { class: 'actions' },
          h('button', { class: 'btn', type: 'button', onclick: () => startPractice(null), text: 'Try another topic' }),
          h('button', { class: 'btn secondary', type: 'button', onclick: showProgress, text: 'See my progress' }))));
    }
  }

  // ---------------------------------------------------- ANALYTICS & CHARTS
  function svgText(attrs, text) {
    const el = svgEl('text', attrs);
    el.textContent = text;
    return el;
  }

  function createSummaryCardsRow(summary) {
    if (!summary) return null;

    const cards = [
      {
        icon: '📝',
        label: 'Problems Attempted',
        value: summary.total_attempts != null ? String(summary.total_attempts) : '0',
        subtext: summary.total_attempts === 1 ? '1 question' : `${summary.total_attempts} questions`,
        colorCls: 'metric-attempts',
      },
      {
        icon: '🎯',
        label: 'Average Accuracy',
        value: summary.average_accuracy != null ? `${Math.round(summary.average_accuracy)}%` : '—',
        subtext: summary.total_correct != null && summary.total_attempts > 0
          ? `${summary.total_correct} of ${summary.total_attempts} correct`
          : 'No attempts',
        colorCls: 'metric-accuracy',
      },
      {
        icon: '📅',
        label: 'Active Study Days',
        value: summary.active_days != null ? `${summary.active_days}` : '0',
        subtext: summary.active_days === 1 ? '1 day practiced' : `${summary.active_days} days practiced`,
        colorCls: 'metric-days',
      },
      {
        icon: '🔥',
        label: 'Current Streak',
        value: `${summary.current_streak || 0}d`,
        subtext: `Best: ${summary.longest_streak || 0} days`,
        colorCls: 'metric-streak',
      },
    ];

    const cardsNodes = cards.map(c => h('div', { class: `summary-metric-card ${c.colorCls}` },
      h('div', { class: 'summary-metric-top' },
        h('span', { class: 'summary-metric-icon', text: c.icon }),
        h('span', { class: 'summary-metric-label', text: c.label })),
      h('div', { class: 'summary-metric-val', text: c.value }),
      h('div', { class: 'summary-metric-sub', text: c.subtext })));

    const insightBanner = summary.insight_text
      ? h('div', { class: 'analytics-insight-banner' },
          h('span', { class: 'insight-icon', text: '💡' }),
          h('p', { text: summary.insight_text }))
      : null;

    return h('div', null,
      h('div', { class: 'summary-metrics-grid' }, cardsNodes),
      insightBanner);
  }

  function createProgressOverTimeChart(timeline, selectedDays) {
    const card = h('div', { class: 'chart-card' });

    const header = h('div', { class: 'chart-header-row' },
      h('div', { class: 'chart-title-area' },
        h('h3', { text: 'Learning Progress Over Time' }),
        h('p', { text: 'Track your daily practice and accuracy.' })),
      h('div', { class: 'chart-legend' },
        h('div', { class: 'legend-item' },
          h('span', { class: 'legend-swatch swatch-accuracy' }),
          h('span', { text: 'Daily Accuracy (%)' })),
        h('div', { class: 'legend-item' },
          h('span', { class: 'legend-swatch swatch-attempts' }),
          h('span', { text: 'Problems Attempted' }))));

    card.append(header);

    const hasAnyActivity = timeline && timeline.some(t => t.attempts > 0);
    if (!hasAnyActivity) {
      card.append(
        h('div', { class: 'chart-empty-state' },
          h('span', { class: 'chart-empty-icon', text: '📈' }),
          h('h4', { text: 'No practice data in this time range' }),
          h('p', { text: `You haven't attempted any questions in the last ${selectedDays} days. Once you solve questions or take quizzes, your daily accuracy and problem counts will be tracked here.` }),
          h('button', { class: 'btn', type: 'button', onclick: () => startPractice(null), text: 'Start Practice Now' }))
      );
      return card;
    }

    const tooltip = h('div', { class: 'chart-floating-tooltip' });
    const wrap = h('div', { class: 'chart-svg-wrap' });
    wrap.append(tooltip);

    const svgW = 800;
    const svgH = 320;
    const padL = 52;
    const padR = 48;
    const padT = 25;
    const padB = 45;
    const plotW = svgW - padL - padR;
    const plotH = svgH - padT - padB;

    const maxAttemptsRaw = Math.max(...timeline.map(t => t.attempts), 4);
    const maxAttempts = maxAttemptsRaw <= 5 ? 5 : Math.ceil(maxAttemptsRaw / 5) * 5;

    const svg = svgEl('svg', {
      viewBox: `0 0 ${svgW} ${svgH}`,
      width: '100%',
      height: 'auto',
      role: 'img',
      'aria-label': 'Learning Progress Over Time Line Chart',
    });

    // Defs: gradient for accuracy area fill
    const defs = svgEl('defs');
    const grad = svgEl('linearGradient', {
      id: 'accGradient',
      x1: '0',
      y1: '0',
      x2: '0',
      y2: '1',
    });
    const stop1 = svgEl('stop', { offset: '0%', 'stop-color': '#8b5cf6', 'stop-opacity': '0.32' });
    const stop2 = svgEl('stop', { offset: '100%', 'stop-color': '#8b5cf6', 'stop-opacity': '0.0' });
    grad.append(stop1, stop2);
    defs.append(grad);
    svg.append(defs);

    // Horizontal gridlines & Left/Right axis labels (0%, 25%, 50%, 75%, 100%)
    const gridSteps = [0, 0.25, 0.5, 0.75, 1];
    gridSteps.forEach(fraction => {
      const y = padT + (1 - fraction) * plotH;
      svg.append(svgEl('line', {
        x1: padL,
        y1: y,
        x2: padL + plotW,
        y2: y,
        stroke: '#f1f5f9',
        'stroke-width': '1',
        'stroke-dasharray': fraction === 0 ? 'none' : '4,4',
      }));

      // Left axis label (Accuracy %)
      svg.append(svgText({
        x: padL - 8,
        y: y + 4,
        'text-anchor': 'end',
        'font-size': '11',
        fill: '#8b5cf6',
        'font-weight': '600',
        'font-family': 'sans-serif',
      }, `${Math.round(fraction * 100)}%`));

      // Right axis label (Problems Attempted)
      const attVal = Math.round(fraction * maxAttempts);
      svg.append(svgText({
        x: padL + plotW + 8,
        y: y + 4,
        'text-anchor': 'start',
        'font-size': '11',
        fill: '#0ea5e9',
        'font-weight': '600',
        'font-family': 'sans-serif',
      }, String(attVal)));
    });

    const n = timeline.length;
    const colW = plotW / n;

    // Line 1: Problems Attempted Line (Sky Blue Line Graph)
    let attPathD = '';
    timeline.forEach((item, i) => {
      const cx = padL + (i + 0.5) * colW;
      const cy = padT + (1 - item.attempts / maxAttempts) * plotH;
      attPathD += (i === 0 ? `M ${cx} ${cy}` : ` L ${cx} ${cy}`);
    });

    if (timeline.length > 1) {
      svg.append(svgEl('path', {
        d: attPathD,
        fill: 'none',
        stroke: '#0ea5e9',
        'stroke-width': '2.5',
        'stroke-linecap': 'round',
        'stroke-linejoin': 'round',
        'stroke-dasharray': '5,4',
        pathLength: '1000',
        class: 'chart-animated-line-secondary',
      }));
    }

    // Points on Problems Attempted Line
    timeline.forEach((item, i) => {
      if (item.attempts > 0) {
        const cx = padL + (i + 0.5) * colW;
        const cy = padT + (1 - item.attempts / maxAttempts) * plotH;
        svg.append(svgEl('circle', {
          cx,
          cy,
          r: 3.5,
          fill: '#0ea5e9',
          stroke: '#ffffff',
          'stroke-width': '1.5',
          class: 'chart-animated-point',
          style: `animation-delay: ${0.3 + i * 0.02}s;`,
        }));
      }
    });

    // Line 2: Daily Accuracy (%) Line & Gradient Area (Purple Line Graph)
    const activePoints = [];
    timeline.forEach((item, i) => {
      if (item.has_activity && item.accuracy != null) {
        const cx = padL + (i + 0.5) * colW;
        const cy = padT + (1 - item.accuracy / 100) * plotH;
        activePoints.push({ x: cx, y: cy, item, index: i });
      }
    });

    if (activePoints.length > 1) {
      let pathD = `M ${activePoints[0].x} ${activePoints[0].y}`;
      for (let k = 1; k < activePoints.length; k++) {
        pathD += ` L ${activePoints[k].x} ${activePoints[k].y}`;
      }

      // Shaded area under accuracy line
      const firstPt = activePoints[0];
      const lastPt = activePoints[activePoints.length - 1];
      const areaD = `${pathD} L ${lastPt.x} ${padT + plotH} L ${firstPt.x} ${padT + plotH} Z`;
      svg.append(svgEl('path', {
        d: areaD,
        fill: 'url(#accGradient)',
        class: 'chart-animated-area',
      }));

      // Accuracy stroke line
      svg.append(svgEl('path', {
        d: pathD,
        fill: 'none',
        stroke: '#8b5cf6',
        'stroke-width': '3.5',
        'stroke-linecap': 'round',
        'stroke-linejoin': 'round',
        pathLength: '1000',
        class: 'chart-animated-line',
      }));
    }

    activePoints.forEach((pt, idx) => {
      svg.append(svgEl('circle', {
        cx: pt.x,
        cy: pt.y,
        r: 5,
        fill: '#8b5cf6',
        stroke: '#ffffff',
        'stroke-width': '2',
        class: 'chart-animated-point',
        style: `animation-delay: ${0.45 + idx * 0.03}s;`,
      }));

      if (activePoints.length === 1) {
        // Single point indicator pulse halo
        svg.append(svgEl('circle', {
          cx: pt.x,
          cy: pt.y,
          r: 9,
          fill: 'none',
          stroke: '#8b5cf6',
          'stroke-width': '1.5',
          'stroke-opacity': '0.4',
          class: 'chart-animated-point',
          style: `animation-delay: 0.5s;`,
        }));
      }
    });

    // X-Axis Date Labels
    const labelStep = n <= 10 ? 1 : (n <= 31 ? 3 : 7);
    timeline.forEach((item, i) => {
      if (i % labelStep === 0 || i === n - 1) {
        const cx = padL + (i + 0.5) * colW;
        svg.append(svgText({
          x: cx,
          y: padT + plotH + 18,
          'text-anchor': 'middle',
          'font-size': '11',
          fill: '#64748b',
          'font-family': 'sans-serif',
        }, item.label));
      }
    });

    // Interactive Hover Columns
    timeline.forEach((item, i) => {
      const colX = padL + i * colW;
      const hoverRect = svgEl('rect', {
        x: colX,
        y: padT,
        width: colW,
        height: plotH,
        fill: 'transparent',
        style: 'cursor: pointer;',
      });

      hoverRect.addEventListener('pointerenter', () => {
        hoverRect.setAttribute('fill', '#f8fafc');
        hoverRect.setAttribute('fill-opacity', '0.45');
        tooltip.style.display = 'block';
        tooltip.innerHTML = `<strong>${item.day_name}, ${item.label}</strong>` +
          `<div class="tt-row">🟦 Problems: <b>${item.attempts}</b> (${item.correct} correct)</div>` +
          `<div class="tt-row">🟣 Accuracy: <b>${item.accuracy != null ? item.accuracy + '%' : 'No activity'}</b></div>`;

        const tooltipX = colX + colW / 2;
        const activePt = activePoints.find(p => p.index === i);
        const tooltipY = activePt ? activePt.y : (padT + plotH / 2);

        tooltip.style.left = `${(tooltipX / svgW) * 100}%`;
        tooltip.style.top = `${(tooltipY / svgH) * 100}%`;
      });

      hoverRect.addEventListener('pointerleave', () => {
        hoverRect.setAttribute('fill', 'transparent');
        tooltip.style.display = 'none';
      });

      svg.append(hoverRect);
    });

    wrap.append(svg);
    card.append(wrap);
    return card;
  }

  function createStudyActivityChart(weekdays) {
    const card = h('div', { class: 'chart-card' });

    const header = h('div', { class: 'chart-header-row' },
      h('div', { class: 'chart-title-area' },
        h('h3', { text: 'Study Activity' }),
        h('p', { text: 'See which days you practice most.' })),
      h('div', { class: 'chart-legend' },
        h('div', { class: 'legend-item' },
          h('span', { class: 'legend-swatch swatch-active-day' }),
          h('span', { text: 'Top Practice Day' }))));

    card.append(header);

    const hasAnyActivity = weekdays && weekdays.some(w => w.attempts > 0);
    if (!hasAnyActivity) {
      card.append(
        h('div', { class: 'chart-empty-state' },
          h('span', { class: 'chart-empty-icon', text: '📅' }),
          h('h4', { text: 'No weekday patterns yet' }),
          h('p', { text: 'As you practice throughout the week, your attempt distribution across Monday to Sunday will appear here to help you identify your best study days.' }))
      );
      return card;
    }

    const tooltip = h('div', { class: 'chart-floating-tooltip' });
    const wrap = h('div', { class: 'chart-svg-wrap' });
    wrap.append(tooltip);

    const svgW = 650;
    const svgH = 250;
    const padL = 40;
    const padR = 25;
    const padT = 30;
    const padB = 40;
    const plotW = svgW - padL - padR;
    const plotH = svgH - padT - padB;

    const maxAttsRaw = Math.max(...weekdays.map(w => w.attempts), 4);
    const maxAtts = maxAttsRaw <= 4 ? 4 : Math.ceil(maxAttsRaw / 4) * 4;

    const svg = svgEl('svg', {
      viewBox: `0 0 ${svgW} ${svgH}`,
      width: '100%',
      height: 'auto',
      role: 'img',
      'aria-label': 'Study Activity by Weekday Bar Chart',
    });

    // Horizontal gridlines (0, 50%, 100%)
    [0, 0.5, 1].forEach(frac => {
      const y = padT + (1 - frac) * plotH;
      svg.append(svgEl('line', {
        x1: padL,
        y1: y,
        x2: padL + plotW,
        y2: y,
        stroke: '#f1f5f9',
        'stroke-width': '1',
      }));
      svg.append(svgText({
        x: padL - 8,
        y: y + 4,
        'text-anchor': 'end',
        'font-size': '11',
        fill: '#94a3b8',
        'font-family': 'sans-serif',
      }, String(Math.round(frac * maxAtts))));
    });

    const colW = plotW / 7;
    const barW = Math.min(42, colW * 0.58);

    weekdays.forEach((w, i) => {
      const cx = padL + (i + 0.5) * colW;
      const bH = w.attempts > 0 ? (w.attempts / maxAtts) * plotH : 2;
      const bY = padT + plotH - bH;
      const isTop = w.is_max;
      const fillColor = isTop ? '#2563eb' : (w.attempts > 0 ? '#0ea5e9' : '#e2e8f0');

      svg.append(svgEl('rect', {
        x: cx - barW / 2,
        y: bY,
        width: barW,
        height: bH,
        rx: 4,
        fill: fillColor,
        class: 'chart-animated-bar',
        style: `animation-delay: ${i * 0.08}s;`,
      }));

      if (w.attempts > 0) {
        svg.append(svgText({
          x: cx,
          y: bY - 6,
          'text-anchor': 'middle',
          'font-size': '11',
          'font-weight': isTop ? 'bold' : 'normal',
          fill: isTop ? '#1e40af' : '#475569',
          'font-family': 'sans-serif',
          class: 'chart-animated-label',
          style: `animation-delay: ${0.25 + i * 0.08}s;`,
        }, isTop ? `${w.attempts} ⭐` : String(w.attempts)));
      }

      svg.append(svgText({
        x: cx,
        y: padT + plotH + 18,
        'text-anchor': 'middle',
        'font-size': '12',
        'font-weight': isTop ? '700' : '500',
        fill: isTop ? '#1e293b' : '#64748b',
        'font-family': 'sans-serif',
      }, w.short_name));

      const hoverRect = svgEl('rect', {
        x: padL + i * colW,
        y: padT,
        width: colW,
        height: plotH,
        fill: 'transparent',
        style: 'cursor: pointer;',
      });

      hoverRect.addEventListener('pointerenter', () => {
        hoverRect.setAttribute('fill', '#f8fafc');
        hoverRect.setAttribute('fill-opacity', '0.45');
        tooltip.style.display = 'block';
        tooltip.innerHTML = `<strong>${w.day_name}</strong>` +
          `<div class="tt-row">Questions: <b>${w.attempts}</b> (${w.correct} correct)</div>` +
          `<div class="tt-row">Accuracy: <b>${w.accuracy != null ? w.accuracy + '%' : 'No activity'}</b></div>` +
          (isTop ? `<div style="color: #60a5fa; margin-top: 0.2rem;">⭐ Most active study day!</div>` : '');

        tooltip.style.left = `${(cx / svgW) * 100}%`;
        tooltip.style.top = `${((bY - 10) / svgH) * 100}%`;
      });

      hoverRect.addEventListener('pointerleave', () => {
        hoverRect.setAttribute('fill', 'transparent');
        tooltip.style.display = 'none';
      });

      svg.append(hoverRect);
    });

    wrap.append(svg);
    card.append(wrap);
    return card;
  }

  function createAnalyticsDashboard(initialAnalytics, learnerId) {
    let currentDays = initialAnalytics.days || 7;
    let analyticsData = initialAnalytics;

    const container = h('div', { class: 'analytics-container' });

    const pills = [
      { label: 'Last 7 Days', days: 7 },
      { label: 'Last 30 Days', days: 30 },
      { label: 'Last 90 Days', days: 90 },
    ];

    const pillsContainer = h('div', { class: 'time-range-pills', role: 'group', 'aria-label': 'Select chart time range' });
    const contentArea = h('div');

    function renderContent() {
      pillsContainer.replaceChildren(
        ...pills.map(p => h('button', {
          type: 'button',
          class: 'time-pill-btn' + (p.days === currentDays ? ' active' : ''),
          text: p.label,
          onclick: async () => {
            if (p.days === currentDays) return;
            currentDays = p.days;
            contentArea.replaceChildren(spinner('Updating progress analytics…'));
            renderContent();
            try {
              analyticsData = await withLearner(id => request(`/api/progress/analytics?learner_id=${id}&days=${currentDays}`));
              renderContent();
            } catch (err) {
              contentArea.replaceChildren(
                h('div', { class: 'card error' },
                  h('p', { text: err.message || 'Could not load analytics for this range.' }),
                  h('button', { class: 'btn', type: 'button', onclick: renderContent, text: 'Retry' }))
              );
            }
          }
        }))
      );

      const summaryRow = createSummaryCardsRow(analyticsData.summary);
      const progressChart = createProgressOverTimeChart(analyticsData.timeline, currentDays);
      const studyActivityChart = createStudyActivityChart(analyticsData.weekdays);

      const chartsGrid = h('div', { class: 'charts-grid' }, progressChart, studyActivityChart);
      contentArea.replaceChildren(summaryRow, chartsGrid);
    }

    const topBar = h('div', { class: 'analytics-top-bar' },
      h('div', null,
        h('h2', { style: 'margin: 0;', text: 'Individual Learning Analytics' }),
        h('p', { class: 'lead', style: 'margin: 0.2rem 0 0 0; font-size: 0.95rem;', text: 'Real-time performance trends based on your verified practice and quiz history.' })),
      pillsContainer);

    container.append(topBar, contentArea);
    renderContent();
    return container;
  }

  // VIEW 7: PROGRESS & HISTORY (FR-08)
  async function showProgress() {
    setActiveNav('nav-progress');
    view(spinner('Loading your learning progress…'));

    let p;
    let analyticsData = null;
    let gamificationData = null;
    try {
      p = await withLearner(id => request(`/api/progress?learner_id=${id}`));
      analyticsData = await withLearner(id => request(`/api/progress/analytics?learner_id=${id}&days=7`));
      gamificationData = await withLearner(id => request(`/api/gamification/summary?learner_id=${id}`));
    } catch (e) {
      return failure(e, showProgress);
    }

    const attemptsCount = p.totals.attempts || 0;
    const correctCount = p.totals.correct || 0;
    const pct = attemptsCount ? Math.round((100 * correctCount) / attemptsCount) : null;

    const statsHeader = h('div', null,
      h('h1', { text: 'My learning progress' }),
      h('p', { class: 'lead', text: 'Track how your understanding is developing across different fraction concepts.' }));

    const analyticsDashboard = analyticsData
      ? createAnalyticsDashboard(analyticsData, learnerId)
      : null;

    const streakWidget = gamificationData?.streak ? streakIndicator(gamificationData.streak) : null;

    const topicsSection = h('div', { class: 'card' },
      h('h2', { text: 'Topic mastery' }),
      p.skills.length
        ? p.skills.map(s => h('div', { class: 'skillrow' },
            h('div', null,
              h('strong', { text: s.name }),
              h('div', { class: 'muted', text: `${s.correct} of ${s.attempts} correct` })),
            h('div', null,
              h('span', { class: 'chip' + (s.level === 'Secure' ? ' ok' : s.level === 'Getting there' ? ' teal' : ''), text: s.level }),
              ' ',
              h('span', { class: 'dots', role: 'img', 'aria-label': s.recent.length ? `Recent ${s.recent.length} attempts` : 'No attempts' },
                s.recent.map(r => h('span', { class: 'dot ' + (r ? 'good' : 'miss') }))))))
        : h('p', { class: 'muted', text: 'No topics attempted yet.' }));

    const ideasSection = h('div', { class: 'card' },
      h('h2', { text: 'Concepts we’ve been working on' }),
      p.misconceptions.length
        ? h('ul', null, p.misconceptions.map(m => h('li', null,
            h('strong', { text: m.name }),
            ` — identified ${m.times_seen} time${m.times_seen === 1 ? '' : 's'}, resolved in fresh questions ${m.resolved} time${m.resolved === 1 ? '' : 's'}.`)))
        : h('p', { class: 'muted', text: 'No recurring misconceptions recorded yet. Mistakes will show up here to help guide your practice.' }));

    const reviewSection = p.review && p.review.length
      ? h('div', { class: 'card warning' },
          h('h2', { text: 'Scheduled for review' }),
          h('ul', null, p.review.map(r => h('li', null,
            h('strong', { text: r.name }),
            r.due ? ' — ready to practise now!' : ' — scheduled for tomorrow.'))))
      : null;

    const badgesWidget = gamificationData?.badges ? badgeCollectionSection(gamificationData.badges, gamificationData.earned_count, gamificationData.total_badges) : null;

    const bottomActions = h('div', { class: 'actions' },
      h('button', { class: 'btn', type: 'button', onclick: () => startPractice(null), text: 'Continue practice' }),
      h('button', { class: 'btn', type: 'button', style: 'background: #0284c7;', onclick: () => startQuiz(null), text: 'Take a quiz 🎯' }),
      h('button', { class: 'btn secondary', type: 'button', onclick: showLearn, text: 'Explore topics' }));

    view(statsHeader, analyticsDashboard, streakWidget, topicsSection, ideasSection, reviewSection, badgesWidget, bottomActions);
  }

  // VIEW 8: QUIZ RUNNER (Gamified Quiz Mode)
  async function startQuiz(skillId) {
    setActiveNav('nav-quiz');
    view(spinner('Loading your quiz questions…'));

    let questions = [];
    try {
      const url = '/api/quiz?n=5' + (skillId ? `&skill=${encodeURIComponent(skillId)}` : '');
      const res = await request(url);
      questions = res.questions || [];
    } catch (e) {
      return failure(e, () => startQuiz(skillId));
    }

    if (!questions.length) {
      return view(h('div', { class: 'card' },
        h('h2', { text: 'No questions available' }),
        h('p', { text: 'Could not load quiz questions at this time.' }),
        h('div', { class: 'actions' },
          h('button', { class: 'btn', type: 'button', onclick: showDashboard, text: 'Back to Dashboard' }))));
    }

    let currentIndex = 0;
    const userAnswers = [];

    function renderQuestion() {
      const q = questions[currentIndex];
      const isLast = currentIndex === questions.length - 1;

      view(
        h('div', { class: 'quiz-header-card' },
          h('div', null,
            h('span', { class: 'chip primary', text: `Quiz — Question ${currentIndex + 1} of ${questions.length}` }),
            h('h2', { style: 'margin: 0.35rem 0 0 0;', text: q.skill_name || 'Fractions Challenge' })),
          h('span', { class: 'quiz-score-pill', text: `Question ${currentIndex + 1} / ${questions.length}` })),
        questionCard(q, {
          submitLabel: isLast ? 'Finish & submit quiz' : 'Next question →',
          onSubmit: async (ans) => {
            userAnswers.push({ question_id: q.id, answer: ans });
            if (!isLast) {
              currentIndex++;
              renderQuestion();
            } else {
              await submitQuiz();
            }
          },
          secondary: h('button', {
            class: 'btn secondary',
            type: 'button',
            onclick: () => {
              if (window.confirm('Quit this quiz and return to Dashboard? Your answers will not be recorded.')) {
                showDashboard();
              }
            },
            text: 'Quit quiz',
          }),
        })
      );
    }

    async function submitQuiz() {
      view(spinner('Grading your quiz answers…'));
      try {
        const idempotencyKey = 'quiz-' + Date.now() + '-' + Math.random().toString(36).slice(2, 9);
        const gradeRes = await withLearner(lid => request('/api/quiz/grade', {
          method: 'POST',
          body: {
            learner_id: lid,
            answers: userAnswers,
            idempotency_key: idempotencyKey,
          }
        }));

        if (gradeRes.score > 0) {
          triggerConfetti();
        }

        const newBadges = gradeRes.gamification?.newly_unlocked_badges || [];
        if (newBadges.length > 0) {
          showBadgeUnlockedModal(newBadges);
        }

        showQuizResults(gradeRes);
      } catch (e) {
        failure(e, () => submitQuiz());
      }
    }

    function showQuizResults(res) {
      const pct = Math.round((res.score / res.total) * 100);
      const isPerfect = res.score === res.total && res.total >= 3;
      const isPassing = pct >= 80;

      const title = isPerfect
        ? '🌟 Perfect Score! Outstanding!'
        : isPassing
        ? '🎉 Excellent work!'
        : 'Good effort! Keep practising!';

      const resultsList = res.results.map((r, i) => {
        const q = questions[i];
        return h('div', { class: 'card', style: 'margin-bottom: 0.75rem; border-left: 4px solid ' + (r.correct ? '#10b981' : '#ef4444') },
          h('div', { style: 'display: flex; justify-content: space-between; align-items: center;' },
            h('strong', { text: `Question ${i + 1}: ${q.prompt}` }),
            h('span', { class: 'chip ' + (r.correct ? 'ok' : 'danger'), text: r.correct ? 'Correct ✓' : 'Incorrect ✗' })),
          !r.correct ? h('p', { class: 'muted', style: 'margin: 0.4rem 0 0 0;' },
            'Correct answer: ', h('strong', { text: r.answer }),
            r.message ? ` — ${r.message}` : '') : null);
      });

      view(
        h('div', { class: 'card highlight', role: 'status' },
          h('h1', { text: title }),
          h('p', { class: 'lead', text: `You scored ${res.score} out of ${res.total} (${pct}%).` }),
          isPerfect ? h('p', { text: 'You answered every question correctly and earned the Perfect Score badge!' }) : null,
          h('div', { class: 'actions' },
            h('button', { class: 'btn btn-lg', type: 'button', onclick: () => startQuiz(skillId), text: 'Try another quiz' }),
            h('button', { class: 'btn btn-lg secondary', type: 'button', onclick: showDashboard, text: 'Back to Dashboard' }))),
        h('h2', { text: 'Question Review' }),
        ...resultsList
      );
    }

    renderQuestion();
  }

  // VIEW 8: SESSION & PROFILE MODAL (FR-01)
  function showProfileModal() {
    const modalBackdrop = h('div', { class: 'modal-backdrop', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'session-modal-title' });

    const modalBox = h('div', { class: 'modal-box' },
      h('h2', { id: 'session-modal-title', text: 'Learner Session & Privacy' }),
      h('p', { text: 'EduMind operates on a strict privacy-first model. No email, password, or real name is ever collected.' }),
      h('div', { class: 'field' },
        h('label', { text: 'Anonymous Learner ID' }),
        h('input', { type: 'text', readonly: true, value: learnerId || 'Session not initialized' })),
      h('p', { class: 'hintline', text: 'Your progress is stored in your browser and synced anonymously with the local database.' }),
      h('div', { class: 'actions' },
        h('button', { class: 'btn secondary danger', type: 'button', onclick: deleteData, text: 'Delete all my data' }),
        h('button', { class: 'btn', type: 'button', onclick: () => modalBackdrop.remove(), text: 'Done' })));

    modalBackdrop.append(modalBox);
    modalBackdrop.addEventListener('click', (e) => {
      if (e.target === modalBackdrop) modalBackdrop.remove();
    });
    document.body.append(modalBackdrop);
  }

  // DELETE DATA (Privacy Guarantee)
  async function deleteData() {
    if (!learnerId) {
      showDashboard();
      return;
    }
    if (!window.confirm('Are you sure you want to delete all of your practice progress and answers? This action cannot be undone.')) {
      return;
    }
    try {
      await request(`/api/learners/${learnerId}`, { method: 'DELETE' });
    } catch (e) {
      if (e.status !== 404) return failure(e);
    }
    store.del(ID_KEY);
    learnerId = null;

    // Remove any open modals
    document.querySelectorAll('.modal-backdrop').forEach(m => m.remove());

    view(h('div', { class: 'card success', role: 'status' },
      h('h2', { text: 'Your data has been erased' }),
      h('p', { text: 'All your answers, progress records, and anonymous session IDs have been permanently erased.' }),
      h('div', { class: 'actions' },
        h('button', { class: 'btn', type: 'button', onclick: showDashboard, text: 'Start a fresh session' }))));
  }

  // ---------------------------------------------------- AI TUTOR CHATBOT WIDGET
  function initAITutorWidget() {
    let isOpen = false;
    let difficulty = 'Intermediate';
    let mode = 'chat';
    let isLoading = false;
    let errorMessage = null;
    let lastFailedText = null;

    const initialGreeting = "Hello! I'm your EduMind AI Tutor. Ask me any question to explore new subjects, understand unfamiliar concepts, or discover topics beyond your regular syllabus. What would you like to explore today?";
    const messages = [
      { role: 'bot', text: initialGreeting }
    ];

    const launcherWrap = h('div', { class: 'tutor-launcher-wrap' });
    const inviteBubble = h('button', {
      type: 'button',
      class: 'tutor-launcher-invite',
      text: 'Want to learn something new? Come here!',
      onclick: () => toggleTutor(true),
    });

    const launcherBtn = h('button', {
      type: 'button',
      class: 'tutor-launcher-btn',
      'aria-label': 'Open EduMind AI Tutor',
      title: 'EduMind AI Tutor',
      onclick: () => toggleTutor(),
    });
    launcherBtn.innerHTML = '<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path><path d="M8 10h.01M12 10h.01M16 10h.01"></path></svg>';

    launcherWrap.append(inviteBubble, launcherBtn);

    // Panel Elements
    const panel = h('div', { class: 'tutor-panel hidden', 'aria-label': 'EduMind AI Tutor Chat' });

    // Header
    const brand = h('div', { class: 'tutor-brand' },
      h('div', { class: 'tutor-title' },
        h('span', { text: 'EduMind AI Tutor' }),
        h('span', { class: 'tutor-sub', text: '• Gemini Powered' })));

    const diffBeginner = h('button', { type: 'button', class: 'tutor-diff-btn', text: '🌱 Beginner', onclick: () => setDifficulty('Beginner') });
    const diffInter = h('button', { type: 'button', class: 'tutor-diff-btn active', text: '📐 Intermediate', onclick: () => setDifficulty('Intermediate') });
    const diffAdv = h('button', { type: 'button', class: 'tutor-diff-btn', text: '🚀 Advanced', onclick: () => setDifficulty('Advanced') });
    const diffGroup = h('div', { class: 'tutor-diff-group' }, diffBeginner, diffInter, diffAdv);

    const resetBtn = h('button', {
      type: 'button',
      class: 'tutor-icon-btn',
      title: 'Start a fresh conversation',
      'aria-label': 'Reset Chat',
      onclick: resetChat,
    });
    resetBtn.innerHTML = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"></path><path d="M3 3v5h5"></path></svg>';

    const closeBtn = h('button', {
      type: 'button',
      class: 'tutor-icon-btn',
      title: 'Close Tutor',
      'aria-label': 'Close Tutor',
      onclick: () => toggleTutor(false),
    });
    closeBtn.innerHTML = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>';

    const headerControls = h('div', { class: 'tutor-header-controls' }, diffGroup, resetBtn, closeBtn);
    const header = h('div', { class: 'tutor-header' }, brand, headerControls);

    // Messages Area
    const messagesArea = h('div', { class: 'tutor-messages', role: 'log', 'aria-live': 'polite' });

    // Mode chips
    const modeConfigs = [
      { id: 'chat', label: '💬 Chat' },
      { id: 'step-by-step', label: '🔢 Step-by-Step' },
      { id: 'analogy', label: '🍕 Real-World Analogy' },
      { id: 'flashcards', label: '🗂️ Flashcards' },
      { id: 'hints', label: '📚 3-Tier Hints' },
    ];
    const chipBtns = modeConfigs.map(mc => h('button', {
      type: 'button',
      class: 'tutor-chip' + (mc.id === mode ? ' active' : ''),
      text: mc.label,
      onclick: () => setMode(mc.id),
    }));
    const chipsBar = h('div', { class: 'tutor-chips-bar' }, ...chipBtns);

    // Input Area
    const input = h('input', {
      type: 'text',
      class: 'tutor-input',
      placeholder: 'Ask anything (e.g. How do black holes work? What is quantum computing? Explain relational algebra)...',
      onkeydown: (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          sendMessage();
        }
      }
    });

    const sendBtn = h('button', {
      type: 'button',
      class: 'tutor-send-btn',
      text: 'Send 🚀',
      onclick: () => sendMessage(),
    });

    const inputRow = h('div', { class: 'tutor-input-row' }, input, sendBtn);

    panel.append(header, messagesArea, chipsBar, inputRow);
    document.body.append(launcherWrap, panel);

    function setDifficulty(lvl) {
      difficulty = lvl;
      diffBeginner.classList.toggle('active', lvl === 'Beginner');
      diffInter.classList.toggle('active', lvl === 'Intermediate');
      diffAdv.classList.toggle('active', lvl === 'Advanced');
    }

    function setMode(m) {
      mode = m;
      chipBtns.forEach((btn, idx) => {
        btn.classList.toggle('active', modeConfigs[idx].id === m);
      });
    }

    function toggleTutor(forceState) {
      isOpen = forceState !== undefined ? forceState : !isOpen;
      panel.classList.toggle('hidden', !isOpen);
      if (isOpen) {
        scrollToBottom();
        setTimeout(() => input.focus(), 150);
      }
    }

    function resetChat() {
      messages.length = 0;
      messages.push({ role: 'bot', text: initialGreeting });
      errorMessage = null;
      renderMessages();
    }

    function scrollToBottom() {
      messagesArea.scrollTop = messagesArea.scrollHeight;
    }

    function renderMessages() {
      messagesArea.replaceChildren();

      messages.forEach(msg => {
        const isBot = msg.role === 'bot';
        const row = h('div', { class: 'tutor-msg-row ' + (isBot ? 'bot' : 'user') });

        const avatar = h('div', { class: 'tutor-avatar ' + (isBot ? 'bot' : 'user') });
        if (isBot) {
          avatar.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M12 2l2.5 5.5L20 10l-4 4 1 5.5L12 17l-5 2.5 1-5.5-4-4 5.5-2.5z"/></svg>';
        } else {
          avatar.innerHTML = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>';
        }

        const bubble = h('div', { class: 'tutor-bubble ' + (isBot ? 'bot' : 'user') });
        const paragraphs = (msg.text || '').split(/\n\n+/);
        paragraphs.forEach(p => {
          if (p.trim()) {
            bubble.append(h('p', { text: p.trim() }));
          }
        });

        if (isBot) {
          row.append(avatar, bubble);
        } else {
          row.append(bubble, avatar);
        }
        messagesArea.append(row);
      });

      // Quick starter prompts if chat is newly opened
      if (messages.length === 1 && !isLoading) {
        const starterPrompts = [
          "Teach me something new.",
          "What is relational algebra?",
          "How do black holes bend space and time?",
          "Show me a real-world analogy.",
          "Explore a topic beyond my syllabus.",
        ];
        const startersRow = h('div', { class: 'tutor-chips-bar', style: 'padding: 4px 0 8px; border: none; background: transparent;' },
          starterPrompts.map(sp => h('button', {
            type: 'button',
            class: 'tutor-chip',
            text: sp,
            onclick: () => {
              input.value = sp;
              sendMessage();
            }
          }))
        );
        messagesArea.append(startersRow);
      }

      // Loading typing indicator
      if (isLoading) {
        const botAvatar = h('div', { class: 'tutor-avatar bot' });
        botAvatar.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M12 2l2.5 5.5L20 10l-4 4 1 5.5L12 17l-5 2.5 1-5.5-4-4 5.5-2.5z"/></svg>';

        const typingBubble = h('div', { class: 'tutor-bubble bot' },
          h('div', { class: 'tutor-typing', 'aria-label': 'EduMind AI Tutor is typing' },
            h('span'), h('span'), h('span')));

        const typingRow = h('div', { class: 'tutor-msg-row bot' }, botAvatar, typingBubble);
        messagesArea.append(typingRow);
      }

      // Error banner
      if (errorMessage) {
        const errBanner = h('div', { class: 'tutor-error', role: 'alert' },
          h('span', { text: errorMessage }),
          lastFailedText ? h('button', {
            type: 'button',
            class: 'tutor-retry-btn',
            text: 'Retry',
            onclick: () => {
              errorMessage = null;
              sendMessage(lastFailedText);
            }
          }) : null
        );
        messagesArea.append(errBanner);
      }

      scrollToBottom();
    }

    async function sendMessage(overrideText) {
      const text = (overrideText || input.value || '').trim();
      if (!text || isLoading) return;

      input.value = '';
      errorMessage = null;
      lastFailedText = null;

      // Add user message to UI
      messages.push({ role: 'user', text });
      isLoading = true;
      sendBtn.disabled = true;
      renderMessages();

      try {
        // Build history turns for API context (max 10 recent messages)
        const historyForApi = messages.slice(0, -1).map(m => ({
          role: m.role === 'bot' ? 'assistant' : 'user',
          content: m.text,
        }));

        const res = await request('/api/tutor/chat', {
          method: 'POST',
          body: {
            message: text,
            difficulty,
            mode,
            history: historyForApi,
          }
        });

        messages.push({ role: 'bot', text: res.reply });
      } catch (err) {
        errorMessage = err.message || 'Could not connect to EduMind AI Tutor. Please try again.';
        lastFailedText = text;
      } finally {
        isLoading = false;
        sendBtn.disabled = false;
        renderMessages();
      }
    }

    renderMessages();
  }

  // ---------------------------------------------------- INIT & LISTENERS
  document.getElementById('nav-dashboard')?.addEventListener('click', showDashboard);
  document.getElementById('nav-learn')?.addEventListener('click', showLearn);
  document.getElementById('nav-practice')?.addEventListener('click', () => startPractice(null));
  document.getElementById('nav-quiz')?.addEventListener('click', () => startQuiz(null));
  document.getElementById('nav-progress')?.addEventListener('click', showProgress);
  document.getElementById('nav-profile')?.addEventListener('click', showProfileModal);
  document.getElementById('footer-session-btn')?.addEventListener('click', showProfileModal);
  document.getElementById('delete-data')?.addEventListener('click', deleteData);

  // Logo click returns to Dashboard
  document.querySelector('.brand')?.addEventListener('click', showDashboard);

  // Initialize AI Tutor floating assistant
  initAITutorWidget();

  // Initial load
  showDashboard();
})();
