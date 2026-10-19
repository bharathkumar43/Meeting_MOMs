/* Teams meetings page: background Transcript/Video status + "All" view paging. */
(function () {
    'use strict';

    var table = document.getElementById('meetingsTable');
    if (!table) { return; }
    var statusUrl = table.dataset.statusUrl;
    var tbody = table.querySelector('tbody');
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
        var tx = 0, video = 0;
        tbody.querySelectorAll('tr[data-event-id]').forEach(function (row) {
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
        var tCell = row.querySelector('[data-cell="transcript"]');
        var vCell = row.querySelector('[data-cell="video"]');
        if (!info) {
            tCell.innerHTML = PILL.error;
            vCell.innerHTML = PILL.error;
            return;
        }
        row.dataset.status = info.transcript ? 'transcript' : 'manual';
        row.dataset.video = info.video ? 'yes' : 'no';
        tCell.innerHTML = PILL.transcript[info.transcript ? 'yes' : 'no'];
        vCell.innerHTML = PILL.video[info.video ? 'yes' : 'no'];
    }

    async function loadStatus(rows) {
        try {
            var res = await fetch(statusUrl, {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                body: JSON.stringify({ ids: rows.map(function (r) { return r.dataset.eventId; }) })
            });
            var body = await res.json().catch(function () { return {}; });
            var data = (res.ok && body.status === 'ok') ? body.data : {};
            rows.forEach(function (row) { apply(row, data[row.dataset.eventId]); });
        } catch (err) {
            rows.forEach(function (row) { apply(row, null); });
        }
        updateCounts();
    }

    // Status queue: newest rows first, a few batches in flight
    var queue = [];
    var running = 0;
    function pump() {
        while (running < IN_FLIGHT && queue.length) {
            var batch = queue.splice(0, BATCH);
            running += 1;
            loadStatus(batch).then(function () { running -= 1; pump(); });
        }
    }
    function enqueuePending(scope) {
        Array.prototype.forEach.call(scope.querySelectorAll('tr[data-pending]'), function (row) {
            row.removeAttribute('data-pending');
            queue.push(row);
        });
        pump();
    }
    enqueuePending(tbody);

    // Sync button: pull the latest calendar now, then reload to show it
    var syncBtn = document.getElementById('syncBtn');
    if (syncBtn) {
        syncBtn.addEventListener('click', async function () {
            var label = syncBtn.querySelector('span');
            syncBtn.disabled = true;
            syncBtn.querySelector('.bi').classList.add('spin');
            label.textContent = 'Syncing…';
            try {
                var res = await fetch(syncBtn.dataset.syncUrl, {
                    method: 'POST', credentials: 'same-origin',
                    headers: { 'X-Requested-With': 'XMLHttpRequest' }
                });
                var body = await res.json().catch(function () { return {}; });
                if (res.ok && body.status === 'ok') {
                    if (body.data && body.data.skipped) {
                        label.textContent = 'Up to date';
                        setTimeout(function () { label.textContent = 'Sync'; syncBtn.disabled = false; }, 2500);
                    } else {
                        window.location.reload();
                        return;
                    }
                } else {
                    label.textContent = 'Sync failed';
                    setTimeout(function () { label.textContent = 'Sync'; syncBtn.disabled = false; }, 2500);
                }
            } catch (err) {
                label.textContent = 'Sync failed';
                setTimeout(function () { label.textContent = 'Sync'; syncBtn.disabled = false; }, 2500);
            }
            syncBtn.querySelector('.bi').classList.remove('spin');
        });
    }

    // "All" view: load older months when the loader scrolls into view (or on click)
    var loader = document.querySelector('[data-load-older]');
    if (!loader) { return; }
    var button = loader.querySelector('button');
    var status = loader.querySelector('.load-older-status');
    var emptyRow = tbody.querySelector('[data-filter-empty]');
    var busy = false;

    async function loadOlder() {
        if (busy || loader.dataset.offset === '') { return; }
        busy = true;
        button.classList.add('d-none');
        status.classList.remove('d-none');
        try {
            var url = loader.dataset.loadOlder;
            url += (url.indexOf('?') === -1 ? '?' : '&') + 'offset=' + encodeURIComponent(loader.dataset.offset);
            var res = await fetch(url, { credentials: 'same-origin', headers: { 'X-Requested-With': 'XMLHttpRequest' } });
            var body = await res.json();
            if (!res.ok || body.status !== 'ok') { throw new Error(body.message || 'failed'); }

            var holder = document.createElement('tbody');
            holder.innerHTML = body.data.html;
            var added = Array.prototype.slice.call(holder.querySelectorAll('tr'));
            added.forEach(function (row) { tbody.insertBefore(row, emptyRow); });
            enqueuePending(tbody);
            table.dispatchEvent(new CustomEvent('rows-added'));

            if (body.data.next_offset !== null && body.data.next_offset !== undefined) {
                loader.dataset.offset = body.data.next_offset;
                button.classList.remove('d-none');
            } else {
                loader.dataset.offset = '';
                loader.innerHTML = '<span class="text-muted small"><i class="bi bi-check2-all"></i> All meetings loaded</span>';
                observer && observer.disconnect();
            }
        } catch (err) {
            button.classList.remove('d-none');
            button.innerHTML = '<i class="bi bi-arrow-clockwise"></i> Retry loading older meetings';
        } finally {
            busy = false;
            if (status) { status.classList.add('d-none'); }
        }
    }

    button.addEventListener('click', loadOlder);
    var observer = ('IntersectionObserver' in window) ? new IntersectionObserver(function (entries) {
        if (entries.some(function (e) { return e.isIntersecting; })) { loadOlder(); }
    }, { rootMargin: '400px' }) : null;
    if (observer) { observer.observe(loader); }
})();
