/* V31.108 Interactive Lesson Builder - front end.

   One classic script, loaded by both dashboards:
     * student.html  -> BMTLessonBlocks.openStudentLesson(id): renders a lesson's
                        blocks in order (mobile first) and lets the student check
                        Practice/Quiz answers and submit an Assignment.
     * teacher.html  -> mounts the builder into #lessonBuilderMount.

   Safety rules this file keeps (the server enforces the same rules; this is the
   second layer):
     - Nothing from the server is ever built into an HTML string. Every value goes in via
       textContent or a validated attribute, so stored content cannot execute.
     - A link is only used if it parses as https. An iframe is only created for a
       host in IFRAME_HOSTS (kept identical to lesson_blocks.IFRAME_HOSTS by a test)
       and is sandboxed. No inline-document frames, no inline handlers, no dynamic code.
     - Answer keys are never requested by the student view: the server does not
       send them. */
(function () {
  'use strict';

  var IFRAME_HOSTS = ['www.youtube-nocookie.com', 'player.vimeo.com', 'drive.google.com',
    'www.geogebra.org', 'phet.colorado.edu', 'www.canva.com'];

  /* ---------------- small helpers ---------------- */
  function el(tag, props, kids) {
    var n = document.createElement(tag);
    Object.keys(props || {}).forEach(function (k) {
      var v = props[k];
      if (v === undefined || v === null || v === false) return;
      if (k === 'class') n.className = v;
      else if (k === 'text') n.textContent = v;
      else if (k === 'onclick' || k === 'onchange' || k === 'oninput') n[k] = v;
      else n.setAttribute(k, v === true ? '' : String(v));
    });
    (kids || []).forEach(function (c) { if (c) n.appendChild(typeof c === 'string' ? document.createTextNode(c) : c); });
    return n;
  }

  function httpsUrl(u) {
    try { var x = new URL(String(u)); return x.protocol === 'https:' ? x.href : ''; } catch (e) { return ''; }
  }

  function embedUrl(u) {
    var s = httpsUrl(u);
    if (!s) return '';
    return IFRAME_HOSTS.indexOf(new URL(s).hostname) >= 0 ? s : '';
  }

  function getToken() {
    var a = window.auth;
    if (!a || !a.currentUser) return Promise.reject(new Error('Please sign in again.'));
    return a.currentUser.getIdToken();
  }

  function api(method, path, body) {
    return getToken().then(function (t) {
      var opts = { method: method, headers: { Authorization: 'Bearer ' + t } };
      if (body !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
      return fetch(path, opts);
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok) { var e = new Error(d.error || 'Something went wrong. Please try again.'); e.data = d; throw e; }
        return d;
      });
    });
  }

  function say(box, msg, kind) {
    if (!box) return;
    box.textContent = msg || '';
    box.className = 'bmt-lb-msg' + (msg ? ' bmt-lb-msg-' + (kind || 'info') : '');
  }

  function renderMath(node) { if (typeof window.BMTRenderMath === 'function') window.BMTRenderMath(node); }

  function frame(src, title, cls) {
    var safe = embedUrl(src);
    if (!safe) return null;
    return el('iframe', {
      src: safe, title: title || 'Embedded content', loading: 'lazy', allowfullscreen: true,
      referrerpolicy: 'strict-origin-when-cross-origin', class: cls || 'bmt-lb-frame',
      sandbox: 'allow-scripts allow-same-origin allow-popups allow-presentation'
    });
  }

  function outLink(url, label, cls) {
    var safe = httpsUrl(url);
    if (!safe) return null;
    return el('a', { href: safe, target: '_blank', rel: 'noopener noreferrer', class: cls || 'btn btn-outline', text: label });
  }

  /* ---------------- student block renderers ---------------- */
  var renderers = {};

  function caption(text) { return text ? el('p', { class: 'bmt-lb-caption', text: text }) : null; }

  renderers.text = function (r) {
    var wrap = el('div', { class: 'bmt-lb-text' });
    String(r.text || '').split(/\n{2,}/).forEach(function (para) {
      if (para.trim()) wrap.appendChild(el('p', { text: para, style: 'white-space:pre-line' }));
    });
    return wrap;
  };

  renderers.formula = function (r) {
    var open = r.display === false ? '\\(' : '\\[', close = r.display === false ? '\\)' : '\\]';
    return el('div', { class: 'bmt-lb-formula' }, [el('div', { text: open + ' ' + r.latex + ' ' + close }), caption(r.caption)]);
  };

  renderers.image = function (r) {
    if (r.provider === 'canva') {
      var f = frame(r.embedUrl, r.alt || 'Canva design', 'bmt-lb-frame bmt-lb-frame-wide');
      return el('figure', { class: 'bmt-lb-figure' }, [f, caption(r.caption)]);
    }
    var src = httpsUrl(r.src);
    if (!src) return null;
    return el('figure', { class: 'bmt-lb-figure' }, [
      el('img', { src: src, alt: r.alt || '', loading: 'lazy', referrerpolicy: 'no-referrer', class: 'bmt-lb-img' }), caption(r.caption)]);
  };

  renderers.video = function (r) {
    var media;
    if (r.provider === 'direct') {
      var src = httpsUrl(r.src);
      if (!src) return null;
      media = el('video', { src: src, controls: true, preload: 'none', playsinline: true, class: 'bmt-lb-video' });
    } else {
      media = frame(r.embedUrl, r.title || 'Video', 'bmt-lb-frame bmt-lb-frame-wide');
    }
    if (!media) return null;
    return el('figure', { class: 'bmt-lb-figure' }, [r.title ? el('h4', { text: r.title }) : null, media, caption(r.caption)]);
  };

  renderers.pdf = function (r) {
    var link = outLink(r.url, r.kind === 'pdf' ? 'Open PDF' : 'Open reading', 'btn btn-primary');
    if (!link) return null;
    return el('div', { class: 'bmt-lb-card' }, [
      el('div', { class: 'bmt-lb-card-title', text: (r.kind === 'pdf' ? '📄 ' : '📖 ') + (r.title || 'Reading') }),
      r.notes ? el('p', { class: 'subtitle', style: 'white-space:pre-line', text: r.notes }) : null, link]);
  };

  renderers.geogebra = function (r) {
    var f = frame(r.embedUrl, r.title || 'GeoGebra', 'bmt-lb-frame');
    if (!f) return null;
    f.style.height = Math.max(200, Math.min(900, Number(r.height) || 480)) + 'px';
    return el('div', { class: 'bmt-lb-figure' }, [r.title ? el('h4', { text: r.title }) : null, f,
      outLink(r.openUrl, 'Open in GeoGebra', 'btn btn-outline bmt-lb-small')]);
  };

  renderers.simulation = function (r) {
    var head = [el('div', { class: 'bmt-lb-card-title', text: '🧪 ' + (r.title || 'Simulation') }),
      r.description ? el('p', { class: 'subtitle', style: 'white-space:pre-line', text: r.description }) : null];
    if (r.embeddable) {
      var f = frame(r.url, r.title || 'Simulation', 'bmt-lb-frame bmt-lb-frame-tall');
      if (f) return el('div', { class: 'bmt-lb-card' }, head.concat([f, outLink(r.url, 'Open in a new tab', 'btn btn-outline bmt-lb-small')]));
    }
    var link = outLink(r.url, 'Open simulation', 'btn btn-primary');
    if (!link) return null;
    return el('div', { class: 'bmt-lb-card' }, head.concat([el('p', { class: 'subtitle', text: 'Opens on an external website.' }), link]));
  };

  renderers.assignment = function (r, ctx, block) {
    if (!r.available) return el('div', { class: 'bmt-lb-card' }, [el('div', { class: 'bmt-lb-card-title', text: '📝 Assignment' }),
      el('p', { class: 'subtitle', text: 'This assignment is not available to you right now.' })]);
    var msg = el('div', { class: 'bmt-lb-msg' });
    var card = el('div', { class: 'bmt-lb-card' }, [
      el('div', { class: 'bmt-lb-card-title', text: '📝 ' + r.title }),
      r.note ? el('p', { class: 'subtitle', text: r.note }) : null,
      r.description ? el('p', { style: 'white-space:pre-line', text: r.description }) : null,
      r.dueAt ? el('p', { class: 'subtitle', text: 'Due: ' + r.dueAt }) : null,
      el('p', { class: 'subtitle', text: 'Status: ' + String(r.submissionStatus || 'not_submitted').replace(/_/g, ' ') })]);
    if (ctx.readOnly) return card;
    var text = el('textarea', { rows: 4, maxlength: 10000, placeholder: 'Write your answer…', 'aria-label': 'Your answer' });
    var link = el('input', { type: 'url', maxlength: 1000, placeholder: 'Or paste a link to your work (https://…)', 'aria-label': 'Submission link' });
    var btn = el('button', { type: 'button', class: 'btn btn-success', text: 'Submit' });
    btn.onclick = function () {
      var body = { text: text.value.trim() }, l = link.value.trim();
      if (l) { if (!httpsUrl(l)) { say(msg, 'The link must start with https://', 'error'); return; } body.link = l; }
      btn.disabled = true;
      api('POST', '/api/student/assignments/' + encodeURIComponent(r.assignmentId) + '/submit', body)
        .then(function () { say(msg, 'Submitted. Well done!', 'ok'); })
        .catch(function (e) { say(msg, e.message, 'error'); })
        .then(function () { btn.disabled = false; });
    };
    [text, link, btn, msg].forEach(function (n) { card.appendChild(n); });
    return card;
  };

  function questionBlock(r, ctx, block) {
    var box = el('div', { class: 'bmt-lb-card' });
    box.appendChild(el('div', { class: 'bmt-lb-card-title', text: (r.graded ? '🧮 ' : '✏️ ') + (r.title || (r.graded ? 'Quiz' : 'Practice')) }));
    var info = el('p', { class: 'subtitle' });
    function refreshInfo(res) {
      var parts = [];
      if (r.graded) parts.push('Attempts allowed: ' + r.maxAttempts);
      var mine = res || r.myResult;
      if (mine && mine.attempts) parts.push('Best: ' + (mine.bestPercentage != null ? mine.bestPercentage + '%' : '—'));
      info.textContent = parts.join(' • ') || 'Check your answers as often as you like.';
    }
    refreshInfo(); box.appendChild(info);
    var chosen = {}, rows = {};
    (r.questions || []).forEach(function (q, i) {
      var group = el('fieldset', { class: 'bmt-lb-q' }, [el('legend', { text: (i + 1) + '. ' + q.prompt })]);
      Object.keys(q.options).forEach(function (letter) {
        var id = 'q_' + block.id + '_' + q.id + '_' + letter;
        var input = el('input', { type: 'radio', name: 'q_' + block.id + '_' + q.id, id: id, value: letter });
        input.onchange = function () { chosen[q.id] = letter; };
        group.appendChild(el('label', { class: 'bmt-lb-opt', for: id }, [input, el('span', { text: letter + '. ' + q.options[letter] })]));
      });
      var fb = el('div', { class: 'bmt-lb-feedback' });
      group.appendChild(fb); rows[q.id] = { fb: fb, group: group };
      box.appendChild(group);
    });
    var msg = el('div', { class: 'bmt-lb-msg' });
    var btn = el('button', { type: 'button', class: 'btn btn-primary', text: r.graded ? 'Submit quiz' : 'Check answers' });
    if (ctx.readOnly) { btn.disabled = true; btn.title = 'Preview only'; }
    btn.onclick = function () {
      btn.disabled = true; say(msg, 'Checking…', 'info');
      api('POST', '/api/student/lessons/' + encodeURIComponent(ctx.lessonId) + '/blocks/' + encodeURIComponent(block.id) + '/answers', { answers: chosen })
        .then(function (res) {
          res.results.forEach(function (row) {
            var t = rows[row.questionId]; if (!t) return;
            var text = row.correct ? '✅ Correct' : '❌ Not correct';
            if (row.correctAnswer && !row.correct) text += ' — answer: ' + row.correctAnswer;
            t.fb.textContent = text + (row.explanation ? ' — ' + row.explanation : '');
            t.fb.className = 'bmt-lb-feedback ' + (row.correct ? 'bmt-lb-good' : 'bmt-lb-bad');
          });
          say(msg, 'Score: ' + res.score + '/' + res.totalPoints + ' (' + res.percentage + '%)' +
            (res.attemptsLeft === null ? '' : ' • Attempts left: ' + res.attemptsLeft), 'ok');
          refreshInfo({ attempts: res.attempts, bestPercentage: res.bestPercentage });
          if (res.attemptsLeft !== 0) btn.disabled = false;
          renderMath(box);
        })
        .catch(function (e) { say(msg, e.message, 'error'); btn.disabled = !!(e.data && /attempts/i.test(e.message)); });
    };
    box.appendChild(btn); box.appendChild(msg);
    return box;
  }
  renderers.practice = questionBlock;
  renderers.quiz = questionBlock;

  /* Extension point: a new block type only needs a server BlockType plus
     BMTLessonBlocks.registerRenderer('type', function (render, ctx, block) {...}). */
  function registerRenderer(type, fn) { if (/^[a-z][a-z0-9_]{1,30}$/.test(type) && typeof fn === 'function') renderers[type] = fn; }

  function renderBlocks(container, blocks, ctx) {
    ctx = ctx || {};
    container.textContent = '';
    (blocks || []).forEach(function (b) {
      var fn = renderers[b.type], node = null;
      if (!fn) return;
      try { node = fn(b.render || {}, ctx, b); } catch (e) { node = null; }
      if (node) container.appendChild(el('section', { class: 'bmt-lb-block bmt-lb-' + b.type, 'data-block-id': b.id }, [node]));
    });
    if (!container.firstChild) container.appendChild(el('p', { class: 'subtitle', text: 'This lesson has no content yet.' }));
    renderMath(container);
  }

  /* ---------------- student viewer ---------------- */
  var savedCourseViewerDisplay = null;

  function closeStudentLesson() {
    var box = document.getElementById('blockLessonViewer');
    if (box) { box.style.display = 'none'; box.textContent = ''; }
    var cv = document.getElementById('distanceCourseViewer');
    if (cv && savedCourseViewerDisplay !== null) cv.style.display = savedCourseViewerDisplay;
    savedCourseViewerDisplay = null;
  }

  function openStudentLesson(lessonId) {
    var box = document.getElementById('blockLessonViewer');
    if (!box) return;
    var cv = document.getElementById('distanceCourseViewer');
    if (cv && savedCourseViewerDisplay === null) { savedCourseViewerDisplay = cv.style.display; cv.style.display = 'none'; }
    box.style.display = 'block'; box.textContent = 'Loading…';
    api('GET', '/api/student/lessons/' + encodeURIComponent(lessonId)).then(function (d) {
      box.textContent = '';
      var back = el('button', { type: 'button', class: 'btn btn-outline', text: '← Back to course' });
      back.onclick = closeStudentLesson;
      box.appendChild(el('div', { class: 'bmt-lb-head' }, [back, el('div', {}, [
        el('h3', { class: 'bmt-lb-title', text: d.lesson.title || 'Lesson' }),
        el('div', { class: 'subtitle', text: [d.course.title, d.course.subject].filter(Boolean).join(' • ') })])]));
      if (d.lesson.description) box.appendChild(el('p', { class: 'subtitle', style: 'white-space:pre-line', text: d.lesson.description }));
      var body = el('div', { class: 'bmt-lb-body' });
      box.appendChild(body);
      renderBlocks(body, d.blocks, { lessonId: lessonId });
      if (d.legacyResource) {
        var l = outLink(d.legacyResource.url, 'Open original resource', 'btn btn-outline');
        if (l) body.appendChild(el('div', { class: 'bmt-lb-card' }, [l]));
      }
      if (!d.lesson.completed && typeof window.completeDistanceLesson === 'function') {
        var done = el('button', { type: 'button', class: 'btn btn-success', text: 'Mark lesson complete' });
        done.onclick = function () { closeStudentLesson(); window.completeDistanceLesson(lessonId); };
        box.appendChild(el('div', { class: 'bmt-lb-foot' }, [done]));
      }
      box.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }).catch(function (e) {
      box.textContent = '';
      var back = el('button', { type: 'button', class: 'btn btn-outline', text: '← Back to course' });
      back.onclick = closeStudentLesson;
      box.appendChild(back); box.appendChild(el('p', { class: 'bmt-lb-msg bmt-lb-msg-error', text: e.message }));
    });
  }

  /* ---------------- teacher builder ---------------- */
  var LETTERS = ['A', 'B', 'C', 'D', 'E', 'F'];

  function defaults(type) {
    if (type === 'quiz') return { title: '', maxAttempts: 3, questions: [blankQuestion()] };
    if (type === 'practice') return { title: '', questions: [blankQuestion()] };
    if (type === 'formula') return { latex: '', caption: '', display: true };
    if (type === 'geogebra') return { materialId: '', title: '', height: 480 };
    return {};
  }
  function blankQuestion() { return { prompt: '', type: 'mcq', options: { A: '', B: '', C: '', D: '' }, answer: 'A', points: 1, explanation: '' }; }

  function mountBuilder(root) {
    var S = { catalog: [], courses: [], course: null, lessons: [], lesson: null, blocks: [], assignments: [] };
    var status = el('div', { class: 'bmt-lb-msg', role: 'status' });
    var courseSel = el('select', { 'aria-label': 'Course' });
    var lessonSel = el('select', { 'aria-label': 'Lesson' });
    var newTitle = el('input', { placeholder: 'New lesson title', maxlength: 160, 'aria-label': 'New lesson title' });
    var newBtn = el('button', { type: 'button', class: 'btn btn-outline', text: 'Create block lesson' });
    var editor = el('div', { class: 'bmt-lb-editor' });
    var preview = el('div', { class: 'bmt-lb-preview' });

    root.appendChild(el('p', { class: 'subtitle', text: 'Build a lesson from reusable blocks. New lessons start as drafts; students only see published lessons in your course.' }));
    [courseSel, lessonSel, el('div', { class: 'bmt-lb-row' }, [newTitle, newBtn]), status, editor, preview].forEach(function (n) { root.appendChild(n); });

    function option(sel, value, label) { sel.appendChild(el('option', { value: value, text: label })); }

    function loadCourses() {
      return api('GET', '/api/teacher/courses').then(function (d) {
        S.courses = d.courses || [];
        courseSel.textContent = ''; option(courseSel, '', 'Choose a course…');
        S.courses.forEach(function (c) { option(courseSel, c.id, (c.title || 'Course') + (c.className ? ' — Grade ' + c.className : '')); });
      }).catch(function (e) { say(status, e.message, 'error'); });
    }

    function loadLessons(selectId) {
      lessonSel.textContent = ''; option(lessonSel, '', S.course ? 'Choose a lesson…' : 'Choose a course first');
      S.lesson = null; S.blocks = []; draw();
      if (!S.course) return Promise.resolve();
      return api('GET', '/api/teacher/courses/' + encodeURIComponent(S.course.id) + '/lessons').then(function (d) {
        S.lessons = d.lessons || [];
        S.lessons.forEach(function (l) {
          option(lessonSel, l.id, (l.title || 'Lesson') + ' [' + (l.status || '') + ']' + (l.blocks && l.blocks.length ? ' • ' + l.blocks.length + ' blocks' : ''));
        });
        if (selectId) { lessonSel.value = selectId; return openLesson(selectId); }
      }).catch(function (e) { say(status, e.message, 'error'); });
    }

    function openLesson(id) {
      S.lesson = null; S.blocks = []; draw();
      if (!id) return Promise.resolve();
      return api('GET', '/api/teacher/lessons/' + encodeURIComponent(id) + '/blocks').then(function (d) {
        S.lesson = d.lesson; S.blocks = d.blocks || []; draw(); renderBlocks(preview, d.preview, { readOnly: true, lessonId: id });
        preview.insertBefore(el('h4', { text: 'Student preview (saved version)' }), preview.firstChild);
      }).catch(function (e) { say(status, e.message, 'error'); });
    }

    function typeMeta(type) { return S.catalog.filter(function (t) { return t.type === type; })[0] || { label: type, fields: [] }; }

    function draw() {
      editor.textContent = ''; preview.textContent = '';
      if (!S.lesson) return;
      var pub = S.lesson.status === 'published';
      var bar = el('div', { class: 'bmt-lb-row' });
      var typeSel = el('select', { 'aria-label': 'Block type' });
      S.catalog.forEach(function (t) { option(typeSel, t.type, t.label); });
      var add = el('button', { type: 'button', class: 'btn btn-outline', text: '+ Add block' });
      add.onclick = function () { S.blocks.push({ type: typeSel.value, content: defaults(typeSel.value) }); draw(); };
      var save = el('button', { type: 'button', class: 'btn btn-primary', text: 'Save & preview' });
      save.onclick = saveBlocks;
      var toggle = el('button', { type: 'button', class: 'btn ' + (pub ? 'btn-outline' : 'btn-success'), text: pub ? 'Unpublish' : 'Publish' });
      toggle.onclick = function () {
        api('PATCH', '/api/teacher/lessons/' + encodeURIComponent(S.lesson.id), { status: pub ? 'draft' : 'published' })
          .then(function () { S.lesson.status = pub ? 'draft' : 'published'; say(status, pub ? 'Lesson is now a draft.' : 'Lesson published.', 'ok'); draw(); })
          .catch(function (e) { say(status, e.message, 'error'); });
      };
      [typeSel, add, save, toggle].forEach(function (n) { bar.appendChild(n); });
      editor.appendChild(el('div', { class: 'subtitle', text: 'Status: ' + (S.lesson.status || 'draft') + (S.lesson.contentType !== 'blocks' && S.lesson.url ? ' • this lesson also has an original link, shown to students below the blocks' : '') }));
      editor.appendChild(bar);
      S.blocks.forEach(function (b, i) { editor.appendChild(blockEditor(b, i)); });
      if (!S.blocks.length) editor.appendChild(el('p', { class: 'subtitle', text: 'No blocks yet. Add your first block above.' }));
    }

    function saveBlocks() {
      say(status, 'Saving…', 'info');
      api('PUT', '/api/teacher/lessons/' + encodeURIComponent(S.lesson.id) + '/blocks', { blocks: S.blocks }).then(function (d) {
        S.blocks = d.blocks; return openLesson(S.lesson.id).then(function () { say(status, 'Saved ' + d.blockCount + ' block(s).', 'ok'); });
      }).catch(function (e) {
        var where = e.data && typeof e.data.blockIndex === 'number' ? 'Block ' + (e.data.blockIndex + 1) + ': ' : '';
        say(status, where + e.message, 'error');
      });
    }

    function move(i, d) { var j = i + d; if (j < 0 || j >= S.blocks.length) return; var t = S.blocks[i]; S.blocks[i] = S.blocks[j]; S.blocks[j] = t; draw(); }

    function blockEditor(b, i) {
      var meta = typeMeta(b.type);
      var head = el('div', { class: 'bmt-lb-row bmt-lb-blockhead' }, [el('strong', { text: (i + 1) + '. ' + meta.label })]);
      var up = el('button', { type: 'button', class: 'btn btn-outline bmt-lb-small', text: '↑', 'aria-label': 'Move up' }); up.onclick = function () { move(i, -1); };
      var dn = el('button', { type: 'button', class: 'btn btn-outline bmt-lb-small', text: '↓', 'aria-label': 'Move down' }); dn.onclick = function () { move(i, 1); };
      var rm = el('button', { type: 'button', class: 'btn btn-outline bmt-lb-small', text: '✕', 'aria-label': 'Remove block' });
      rm.onclick = function () { S.blocks.splice(i, 1); draw(); };
      [up, dn, rm].forEach(function (n) { head.appendChild(n); });
      var card = el('div', { class: 'bmt-lb-blockcard' }, [head]);
      b.content = b.content || {};
      (meta.fields || []).forEach(function (f) { card.appendChild(fieldEditor(b, f)); });
      return card;
    }

    function fieldEditor(b, f) {
      var c = b.content, label = el('label', { class: 'bmt-lb-field' }, [el('span', { text: f.label + (f.required ? ' *' : '') })]);
      var input;
      if (f.kind === 'textarea') {
        input = el('textarea', { rows: 4 }); input.value = c[f.name] || ''; input.oninput = function () { c[f.name] = input.value; };
      } else if (f.kind === 'boolean') {
        input = el('input', { type: 'checkbox' }); input.checked = c[f.name] !== false; input.onchange = function () { c[f.name] = input.checked; };
      } else if (f.kind === 'number') {
        input = el('input', { type: 'number', inputmode: 'numeric' }); input.value = c[f.name] === undefined ? '' : c[f.name];
        input.oninput = function () { c[f.name] = input.value === '' ? undefined : Number(input.value); };
      } else if (f.kind === 'assignment') {
        input = el('select', {}); option(input, '', 'Choose an assignment…');
        var grade = S.course && S.course.className ? String(S.course.className) : '';
        S.assignments.filter(function (a) { return String(a.className || '') === grade; })
          .forEach(function (a) { option(input, a.id, a.title || a.id); });
        input.value = c[f.name] || ''; input.onchange = function () { c[f.name] = input.value; };
      } else if (f.kind === 'questions') {
        return questionsEditor(b);
      } else {
        input = el('input', { type: f.kind === 'url' ? 'url' : 'text', maxlength: 2000 }); input.value = c[f.name] || '';
        input.oninput = function () { c[f.name] = input.value; };
      }
      label.appendChild(input);
      return label;
    }

    function questionsEditor(b) {
      var qs = b.content.questions = b.content.questions || [blankQuestion()];
      var wrap = el('div', { class: 'bmt-lb-questions' });
      var graded = b.type === 'quiz';
      qs.forEach(function (q, qi) {
        var qc = el('div', { class: 'bmt-lb-qcard' });
        var prompt = el('textarea', { rows: 2, placeholder: 'Question ' + (qi + 1), 'aria-label': 'Question ' + (qi + 1) });
        prompt.value = q.prompt || ''; prompt.oninput = function () { q.prompt = prompt.value; };
        var type = el('select', { 'aria-label': 'Question type' }); option(type, 'mcq', 'Multiple choice'); option(type, 'true_false', 'True / False');
        type.value = q.type || 'mcq'; type.onchange = function () { q.type = type.value; q.answer = 'A'; draw(); };
        qc.appendChild(prompt); qc.appendChild(type);
        var letters = q.type === 'true_false' ? ['A', 'B'] : LETTERS.filter(function (l) { return q.options && q.options[l] !== undefined; });
        if (q.type === 'true_false') { q.options = { A: 'True', B: 'False' }; }
        letters.forEach(function (l) {
          if (q.type === 'true_false') { qc.appendChild(el('div', { class: 'subtitle', text: l + '. ' + q.options[l] })); return; }
          var o = el('input', { placeholder: 'Option ' + l, maxlength: 300, 'aria-label': 'Option ' + l }); o.value = q.options[l] || ''; o.oninput = function () { q.options[l] = o.value; };
          qc.appendChild(o);
        });
        if (q.type !== 'true_false' && letters.length < LETTERS.length) {
          var addOpt = el('button', { type: 'button', class: 'btn btn-outline bmt-lb-small', text: '+ option' });
          addOpt.onclick = function () { q.options[LETTERS[letters.length]] = ''; draw(); }; qc.appendChild(addOpt);
        }
        var ans = el('select', { 'aria-label': 'Correct answer' }); letters.forEach(function (l) { option(ans, l, 'Correct: ' + l); });
        ans.value = q.answer || 'A'; ans.onchange = function () { q.answer = ans.value; }; qc.appendChild(ans);
        if (graded) {
          var pts = el('input', { type: 'number', min: 1, max: 100, placeholder: 'Points', 'aria-label': 'Points' }); pts.value = q.points || 1;
          pts.oninput = function () { q.points = Number(pts.value) || 1; }; qc.appendChild(pts);
        }
        var ex = el('input', { placeholder: 'Explanation (shown after answering)', maxlength: 500, 'aria-label': 'Explanation' });
        ex.value = q.explanation || ''; ex.oninput = function () { q.explanation = ex.value; }; qc.appendChild(ex);
        var del = el('button', { type: 'button', class: 'btn btn-outline bmt-lb-small', text: 'Remove question' });
        del.onclick = function () { qs.splice(qi, 1); draw(); }; qc.appendChild(del);
        wrap.appendChild(qc);
      });
      var addQ = el('button', { type: 'button', class: 'btn btn-outline', text: '+ Add question' });
      addQ.onclick = function () { qs.push(blankQuestion()); draw(); };
      wrap.appendChild(addQ);
      return wrap;
    }

    courseSel.onchange = function () {
      S.course = S.courses.filter(function (c) { return c.id === courseSel.value; })[0] || null;
      say(status, '', ''); loadLessons();
    };
    lessonSel.onchange = function () { say(status, '', ''); openLesson(lessonSel.value); };
    newBtn.onclick = function () {
      if (!S.course) { say(status, 'Choose a course first.', 'error'); return; }
      if (!newTitle.value.trim()) { say(status, 'Enter a lesson title.', 'error'); return; }
      api('POST', '/api/teacher/courses/' + encodeURIComponent(S.course.id) + '/block-lessons', { title: newTitle.value.trim(), status: 'draft', blocks: [] })
        .then(function (d) { newTitle.value = ''; say(status, 'Lesson created as a draft.', 'ok'); return loadLessons(d.lessonId); })
        .catch(function (e) { say(status, e.message, 'error'); });
    };

    api('GET', '/api/teacher/lesson-blocks/types').then(function (d) { S.catalog = d.types || []; })
      .then(function () { return api('GET', '/api/teacher/assignments').then(function (d) { S.assignments = d.assignments || []; }).catch(function () {}); })
      .then(loadCourses)
      .catch(function (e) { say(status, e.message, 'error'); });
  }

  function autoMount() {
    var root = document.getElementById('lessonBuilderMount');
    if (!root || root.dataset.mounted) return;
    root.dataset.mounted = '1';
    // Wait until the dashboard has signed the teacher in (window.auth is set by teacher.js).
    var tries = 0;
    (function wait() {
      if (window.auth && window.auth.currentUser) return mountBuilder(root);
      if (++tries < 120) setTimeout(wait, 500); else say(root.appendChild(el('div')), 'Sign in to use the lesson builder.', 'error');
    })();
  }

  window.BMTLessonBlocks = {
    renderBlocks: renderBlocks, registerRenderer: registerRenderer,
    openStudentLesson: openStudentLesson, closeStudentLesson: closeStudentLesson, mountBuilder: mountBuilder
  };
  window.BMTOpenBlockLesson = openStudentLesson;

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', autoMount); else autoMount();
})();
