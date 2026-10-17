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
    try {
      skills = await request('/api/skills');
      progressData = await withLearner(id => request(`/api/progress?learner_id=${id}`));
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
        h('button', { class: 'btn btn-lg secondary', type: 'button', onclick: showLearn, text: 'Browse topics' })));

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

    view(heroCard, snapshotSection, topicsOverview);
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

  // VIEW 7: PROGRESS & HISTORY (FR-08)
  async function showProgress() {
    setActiveNav('nav-progress');
    view(spinner('Loading your learning progress…'));

    let p;
    try {
      p = await withLearner(id => request(`/api/progress?learner_id=${id}`));
    } catch (e) {
      return failure(e, showProgress);
    }

    const attemptsCount = p.totals.attempts || 0;
    const correctCount = p.totals.correct || 0;
    const pct = attemptsCount ? Math.round((100 * correctCount) / attemptsCount) : null;

    const statsHeader = h('div', null,
      h('h1', { text: 'My learning progress' }),
      h('p', { class: 'lead', text: 'Track how your understanding is developing across different fraction concepts.' }),
      h('div', { class: 'statrow' },
        h('div', { class: 'stat' },
          h('b', { text: String(attemptsCount) }),
          h('span', { text: 'questions answered' })),
        h('div', { class: 'stat' },
          h('b', { text: pct === null ? '—' : `${pct}%` }),
          h('span', { text: 'answered correctly' }))));

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

    const bottomActions = h('div', { class: 'actions' },
      h('button', { class: 'btn', type: 'button', onclick: () => startPractice(null), text: 'Continue practice' }),
      h('button', { class: 'btn secondary', type: 'button', onclick: showLearn, text: 'Explore topics' }));

    view(statsHeader, topicsSection, ideasSection, reviewSection, bottomActions);
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

    const initialGreeting = "Hello! I'm your personalized math tutor. Ask me any question about fractions, request a step-by-step breakdown, or ask for a real-world analogy. How can I help you today?";
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
      placeholder: 'Ask a math question (e.g. Why do we invert and multiply when dividing fractions?)...',
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
          "Why do we invert & multiply when dividing fractions?",
          "What is the value of tan 45?",
          "Show me a real-world example.",
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
