/* Teams meetings page: fill in Transcript / Video status after the list renders. */
(function () {
    'use strict';

    var table = document.getElementById('meetingsTable');
    if (!table) { return; }
    var url = table.dataset.statusUrl;
    var BATCH = 4;
    var IN_FLIGHT = 3;

    var PILL = {
        transcript: {
            yes: '<span class="status-pill status-ok">Available</span>',
            no: '<span class="status-pill status-manual">Manual</span>'
        },
        video: {
            yes: '<span class="status-pill status-sent">Available</span>',
            no: '<span class="text-muted small">&mdash;</span>'
        },
        error: '<span class="text-muted small" title="Could not check right now">?</span>'
    };

    function updateCounts() {
        var rows = table.querySelectorAll('tbody tr[data-event-id]');
        var tx = 0, video = 0;
        rows.forEach(function (row) {
            if (row.dataset.status === 'transcript') { tx += 1; }
            if (row.dataset.video === 'yes') { video += 1; }
        });
        var t = document.querySelector('[data-count="transcript"]');
        var v = document.querySelector('[data-count="video"]');
        if (t) { t.textContent = tx; }
        if (v) { v.textContent = video; }
    }

    function apply(row, info) {
        row.removeAttribute('data-pending');
        if (!info) {
            row.querySelector('[data-cell="transcript"]').innerHTML = PILL.error;
            row.querySelector('[data-cell="video"]').innerHTML = PILL.error;
            return;
        }
        row.dataset.status = info.transcript ? 'transcript' : 'manual';
        row.dataset.video = info.video ? 'yes' : 'no';
        row.querySelector('[data-cell="transcript"]').innerHTML = PILL.transcript[info.transcript ? 'yes' : 'no'];
        row.querySelector('[data-cell="video"]').innerHTML = PILL.video[info.video ? 'yes' : 'no'];
        var open = row.querySelector('[data-open]');
        if (open && (info.transcript || info.video)) {
            open.classList.remove('btn-outline-primary');
            open.classList.add('btn-primary');
        }
    }

    async function load(rows) {
        var ids = rows.map(function (r) { return r.dataset.eventId; });
        try {
            var res = await fetch(url, {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                body: JSON.stringify({ ids: ids })
            });
            var body = await res.json().catch(function () { return {}; });
            var data = (res.ok && body.status === 'ok') ? body.data : {};
            rows.forEach(function (row) { apply(row, data[row.dataset.eventId]); });
        } catch (err) {
            rows.forEach(function (row) { apply(row, null); });
        }
        updateCounts();
    }

    // Visible order first, a couple of batches in flight at a time
    var pending = Array.prototype.slice.call(table.querySelectorAll('tbody tr[data-pending]'));
    var batches = [];
    for (var i = 0; i < pending.length; i += BATCH) { batches.push(pending.slice(i, i + BATCH)); }

    function next() {
        var batch = batches.shift();
        if (batch) { return load(batch).then(next); }
        return Promise.resolve();
    }
    for (var n = 0; n < IN_FLIGHT; n += 1) { next(); }
})();
