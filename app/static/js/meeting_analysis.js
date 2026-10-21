/* Meeting page: on-demand AI review of how CloudFuze employees handled the client.
   Layout is manager-first: at a glance -> summary -> coaching focus -> scorecard ->
   per-employee detail -> evidence. Model output is inserted with textContent only. */
(function () {
    'use strict';

    var btn = document.getElementById('analyzeBtn');
    var card = document.getElementById('analysisCard');
    if (!btn || !card) { return; }

    var SKILLS = [
        ['clarity', 'Clarity'],
        ['listening', 'Listening & empathy'],
        ['problem_solving', 'Problem solving'],
        ['ownership', 'Ownership & follow-up'],
        ['professionalism', 'Professionalism'],
        ['meeting_control', 'Meeting control']
    ];
    var SCORE_WORD = { 5: 'Excellent', 4: 'Good', 3: 'Solid', 2: 'Needs work', 1: 'Concern' };
    var MOOD = {
        satisfied: ['Satisfied', 'an-good', 'bi-emoji-smile'],
        neutral: ['Neutral', 'an-mid', 'bi-emoji-neutral'],
        dissatisfied: ['Not satisfied', 'an-bad', 'bi-emoji-frown'],
        unclear: ['Unclear', 'an-muted', 'bi-question-circle']
    };
    var RESOLVED = { yes: 'Resolved', partly: 'Partly resolved', no: 'Not resolved' };

    // ── helpers ─────────────────────────────────────────────────────────
    function el(tag, cls, text) {
        var node = document.createElement(tag);
        if (cls) { node.className = cls; }
        if (text !== undefined && text !== null && text !== '') { node.textContent = text; }
        return node;
    }
    function icon(name) { return el('i', 'bi ' + name); }
    function add(parent) {
        for (var i = 1; i < arguments.length; i++) { if (arguments[i]) { parent.appendChild(arguments[i]); } }
        return parent;
    }
    function tone10(n) { return n >= 8 ? 'an-good' : n >= 5 ? 'an-mid' : 'an-bad'; }
    function tone5(n) { return n >= 4 ? 'an-good' : n === 3 ? 'an-ok' : 'an-bad'; }
    function label(s) { s = String(s || '').replace(/_/g, ' '); return s.charAt(0).toUpperCase() + s.slice(1); }

    function toSeconds(clock) {
        var parts = String(clock || '').trim().split(':').map(Number);
        if (!parts.length || parts.some(isNaN)) { return null; }
        return parts.reduce(function (acc, n) { return acc * 60 + n; }, 0);
    }
    // Jump to a moment: play the video there, or scroll to the transcript line
    function jump(clock) {
        var t = toSeconds(clock);
        if (t === null) { return; }
        var player = document.getElementById('player');
        if (player) {
            player.currentTime = t;
            player.play().catch(function () {});
            player.scrollIntoView({ behavior: 'smooth', block: 'center' });
            return;
        }
        var best = null;
        document.querySelectorAll('#txList .tx-line').forEach(function (line) {
            if ((parseFloat(line.dataset.t) || 0) <= t + 0.5) { best = line; }
        });
        if (best) {
            best.scrollIntoView({ behavior: 'smooth', block: 'center' });
            best.classList.add('active');
            setTimeout(function () { best.classList.remove('active'); }, 2500);
        }
    }
    function timeChip(clock) {
        if (!clock) { return null; }
        var chip = el('button', 'an-time', clock);
        chip.type = 'button';
        chip.title = 'Play from ' + clock;
        chip.addEventListener('click', function () { jump(clock); });
        return chip;
    }
    function section(title, iconName, extraCls) {
        var wrap = el('section', 'an-section' + (extraCls ? ' ' + extraCls : ''));
        var h = el('h3', 'an-heading');
        add(h, icon(iconName), document.createTextNode(title));
        wrap.appendChild(h);
        return wrap;
    }
    function timedList(items, textKey, cls) {
        var ul = el('ul', 'an-list ' + cls);
        items.forEach(function (item) {
            ul.appendChild(add(el('li'), timeChip(item.time), el('span', null, item[textKey])));
        });
        return ul;
    }

    // ── blocks ──────────────────────────────────────────────────────────
    function tile(title, value, sub, cls, iconName) {
        var t = el('div', 'an-tile ' + (cls || ''));
        add(t, el('div', 'an-tile-label', title));
        var v = el('div', 'an-tile-value');
        add(v, iconName ? icon(iconName) : null, document.createTextNode(value));
        add(t, v, sub ? el('div', 'an-tile-sub', sub) : null);
        return t;
    }

    function glance(a) {
        var row = el('div', 'an-tiles');
        var score = a.overall_score;
        add(row, tile('Handling', score ? score + '/10' : '—', 'How CloudFuze handled the client',
                      score ? tone10(score) : 'an-muted'));
        var mood = MOOD[(a.client_mood || {}).level] || MOOD.unclear;
        add(row, tile('Client mood', mood[0], null, mood[1], mood[2]));
        add(row, tile('Meeting type', label(a.meeting_type), null, 'an-neutral', 'bi-tag'));
        var esc = a.escalation || {};
        if (esc.is_escalation) {
            var r = esc.resolved;
            add(row, tile('Issue raised', RESOLVED[r] || 'Discussed', esc.issue || null,
                          r === 'yes' ? 'an-good' : r === 'partly' ? 'an-mid' : 'an-bad', 'bi-exclamation-triangle'));
        } else {
            add(row, tile('Issues', 'None raised', null, 'an-good', 'bi-shield-check'));
        }
        // Older analyses only have a yes/no flag
        var ns = a.next_steps || { status: a.next_steps_clear ? 'clear' : 'none', detail: '' };
        var NS = {
            clear: ['Clear', 'an-good', 'bi-check2-circle', 'Owners and timing agreed'],
            partial: ['Follow-up agreed', 'an-mid', 'bi-calendar-event', 'Exact time or owner still open'],
            none: ['Not agreed', 'an-bad', 'bi-x-circle', 'Call ended without next steps']
        }[ns.status] || ['Not agreed', 'an-bad', 'bi-x-circle', ''];
        add(row, tile('Next steps', NS[0], ns.detail || NS[3], NS[1], NS[2]));
        return row;
    }

    function summary(a) {
        var s = section('Summary for manager', 'bi-card-text');
        add(s, el('p', 'an-summary', a.summary));
        var mood = a.client_mood || {};
        if (mood.reason) {
            var m = MOOD[mood.level] || MOOD.unclear;
            var line = el('div', 'an-mood ' + m[1]);
            add(line, icon(m[2]), el('strong', null, 'Client mood: '), timeChip(mood.time), el('span', null, mood.reason));
            add(s, line);
        }
        var esc = a.escalation || {};
        if (esc.is_escalation && (esc.issue || esc.handling)) {
            var box = el('div', 'an-issue');
            add(box, el('div', 'an-issue-title', 'Issue: ' + esc.issue),
                esc.handling ? el('div', null, esc.handling) : null);
            add(s, box);
        }
        return s;
    }

    function coaching(a) {
        if (!(a.coaching_focus || []).length) { return null; }
        var s = section('Coaching focus', 'bi-bullseye', 'an-coaching');
        var ol = el('ol', 'an-coach-list');
        a.coaching_focus.forEach(function (f) { ol.appendChild(el('li', null, f)); });
        return add(s, ol);
    }

    function scorecard(a) {
        var people = a.employees || [];
        if (!people.length) { return null; }
        var s = section('Scorecard', 'bi-grid-3x3-gap');
        var wrap = el('div', 'table-responsive');
        var table = el('table', 'an-matrix');
        var head = el('tr');
        add(head, el('th', null, 'Employee'));
        SKILLS.forEach(function (k) { add(head, el('th', null, k[1])); });
        add(head, el('th', null, 'Talk'), el('th', null, 'Overall'));
        add(table, add(el('thead'), head));
        var body = el('tbody');
        people.forEach(function (p) {
            var tr = el('tr');
            var who = el('td', 'an-matrix-name');
            add(who, el('strong', null, p.name), p.role === 'support' ? el('span', 'an-role', 'support') : null);
            add(tr, who);
            SKILLS.forEach(function (k) {
                var v = (p.scores || {})[k[0]];
                var td = el('td');
                if (v) {
                    var chip = el('span', 'an-score ' + tone5(v), String(v));
                    chip.title = k[1] + ': ' + SCORE_WORD[v];
                    add(td, chip);
                } else {
                    add(td, el('span', 'an-score an-na', '—')).title = 'Not observed';
                }
                add(tr, td);
            });
            add(tr, el('td', 'an-matrix-num', p.metrics ? p.metrics.talk_share + '%' : '—'));
            var overall = el('td');
            add(overall, p.rating ? el('span', 'an-pill ' + tone10(p.rating), p.rating + '/10') : el('span', 'text-muted', '—'));
            add(tr, overall);
            add(body, tr);
        });
        add(table, body);
        add(s, add(wrap, table));
        var legend = el('div', 'an-legend');
        [5, 4, 3, 2, 1].forEach(function (n) {
            add(legend, add(el('span'), el('span', 'an-score an-score-sm ' + tone5(n), String(n)),
                            document.createTextNode(SCORE_WORD[n])));
        });
        return add(s, legend);
    }

    function employeeCards(a) {
        var people = a.employees || [];
        if (!people.length) { return null; }
        var s = section(people.length > 1 ? 'Employees' : 'Employee', 'bi-person-badge');
        people.forEach(function (p) {
            var c = el('div', 'an-person');
            var head = el('div', 'an-person-head');
            var name = el('div');
            add(name, el('strong', null, p.name), p.role === 'support' ? el('span', 'an-role', 'support') : null);
            add(head, name, p.rating ? el('span', 'an-pill ' + tone10(p.rating), p.rating + '/10') : null);
            add(c, head);
            if (p.metrics) {
                var m = el('div', 'an-metrics');
                add(m, el('span', null, 'Talk ' + p.metrics.talk_share + '%'),
                    el('span', null, p.metrics.questions + ' question' + (p.metrics.questions === 1 ? '' : 's')),
                    el('span', null, 'Longest turn ' + p.metrics.longest_turn));
                add(c, m);
            }
            var cols = el('div', 'an-cols');
            var good = add(el('div'), el('div', 'an-col-title an-col-good', 'Strengths'));
            add(good, (p.strengths || []).length ? timedList(p.strengths, 'point', 'an-list-good')
                                                 : el('p', 'text-muted small mb-0', 'Nothing specific noted.'));
            var grow = add(el('div'), el('div', 'an-col-title an-col-grow', 'To improve'));
            add(grow, (p.improvements || []).length ? timedList(p.improvements, 'point', 'an-list-grow')
                                                    : el('p', 'text-muted small mb-0', 'Nothing specific noted.'));
            add(c, add(cols, good, grow));
            add(s, c);
        });
        return s;
    }

    function moments(a) {
        if (!(a.key_moments || []).length) { return null; }
        var s = section('Key moments', 'bi-chat-left-dots');
        a.key_moments.forEach(function (m) {
            var good = m.verdict === 'good';
            var row = el('div', 'an-moment ' + (good ? 'an-moment-good' : 'an-moment-grow'));
            add(row,
                add(el('div', 'an-turn'), timeChip(m.client_time), el('span', 'an-who an-who-client', m.client_name || 'Client'), el('span', null, m.client_said)),
                add(el('div', 'an-turn'), timeChip(m.employee_time), el('span', 'an-who', m.employee_name || 'CloudFuze'), el('span', null, m.employee_reply)),
                add(el('div', 'an-why'), el('span', 'an-pill ' + (good ? 'an-good' : 'an-mid'), good ? 'Handled well' : 'Could be better'), el('span', null, m.why)));
            add(s, row);
        });
        return s;
    }

    function suggestions(a) {
        if (!(a.suggestions || []).length) { return null; }
        var s = section('Try saying', 'bi-lightbulb');
        a.suggestions.forEach(function (x) {
            var row = el('div', 'an-suggestion');
            add(row, add(el('div', 'an-turn'), timeChip(x.time),
                         x.employee_name ? el('span', 'an-who', x.employee_name) : null, el('span', null, x.situation)),
                x.better_response ? el('blockquote', 'an-quote', x.better_response) : null);
            add(s, row);
        });
        return s;
    }

    function details(a) {
        var hasC = (a.commitments || []).length, hasQ = (a.clarity_issues || []).length;
        if (!hasC && !hasQ) { return null; }
        var grid = el('div', 'an-cols');
        if (hasC) {
            var c = section('Commitments made', 'bi-check2-square');
            var ul = el('ul', 'an-list');
            a.commitments.forEach(function (x) {
                add(ul, add(el('li'), timeChip(x.time),
                            el('span', null, (x.employee_name ? x.employee_name + ': ' : '') + x.commitment + (x.due ? ' (' + x.due + ')' : ''))));
            });
            add(grid, add(c, ul));
        }
        if (hasQ) {
            var q = section('Clarity issues', 'bi-question-diamond');
            add(q, el('p', 'an-hint', 'Moments the client asked to repeat or seemed unsure.'));
            add(grid, add(q, timedList(a.clarity_issues, 'note', '')));
        }
        return grid;
    }

    // ── render ──────────────────────────────────────────────────────────
    function render(a) {
        card.textContent = '';
        card.classList.remove('d-none');
        var header = el('div', 'card-header d-flex justify-content-between align-items-center gap-2 flex-wrap');
        var title = el('span', 'card-title-text');
        add(title, icon('bi-graph-up-arrow text-primary'), document.createTextNode(' Conversation analysis'));
        add(header, title, a.meta ? el('span', 'text-muted small', 'Analyzed ' + a.meta.at) : null);
        add(card, header);

        var body = el('div', 'card-body an-body');
        if (!a.version) {
            add(body, el('div', 'alert alert-info mb-0',
                'This analysis uses an older format. Click Re-analyze for the full scorecard.'));
            if (a.summary) { add(body, add(section('Summary for manager', 'bi-card-text'), el('p', 'an-summary', a.summary))); }
        } else {
            add(body, glance(a), summary(a), coaching(a), scorecard(a), employeeCards(a),
                moments(a), suggestions(a), details(a));
        }
        add(body, el('p', 'an-footnote',
            'AI-generated from the transcript. Use it as coaching guidance, not a final judgement.'));
        add(card, body);
    }

    // ── actions ─────────────────────────────────────────────────────────
    function setBusy(busy, hasResult) {
        btn.disabled = busy;
        btn.textContent = '';
        if (busy) {
            add(btn, el('span', 'spinner-border spinner-border-sm'), el('span', null, 'Analyzing…'));
        } else {
            add(btn, icon('bi-graph-up-arrow'), el('span', null, hasResult ? 'Re-analyze' : 'Analyze'));
        }
    }
    function showError(message) {
        var alert = el('div', 'alert alert-warning d-flex gap-2 align-items-center mb-4 an-error');
        add(alert, icon('bi-exclamation-circle'), el('span', null, message));
        card.parentNode.insertBefore(alert, card);
        setTimeout(function () { alert.remove(); }, 8000);
    }

    btn.addEventListener('click', async function () {
        var refresh = !!card.querySelector('.an-body');
        if (refresh && !window.confirm('Run the analysis again? This replaces the current one.')) { return; }
        setBusy(true);
        try {
            var res = await fetch(btn.dataset.url, {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                body: JSON.stringify({ owner: btn.dataset.owner, event_id: btn.dataset.eventId, refresh: refresh })
            });
            var data = await res.json().catch(function () { return {}; });
            if (!res.ok || data.status !== 'ok') {
                showError(data.message || 'Analysis failed. Please try again.');
            } else {
                render(data.data);
                card.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
        } catch (err) {
            showError('Could not reach the server. Check your connection.');
        }
        setBusy(false, !!card.querySelector('.an-body'));
    });

    // A saved analysis shows straight away (no new AI call)
    try {
        var saved = JSON.parse(document.getElementById('analysisData').textContent || 'null');
        if (saved) { render(saved); }
    } catch (err) { /* no saved analysis */ }
})();
