/* Client-side behaviour for the shared filter bar (templates/_macros.html). */
(function () {
    'use strict';

    function initFilterBar(root) {
        var table = document.querySelector(root.dataset.filterTarget);
        var input = root.querySelector('[data-filter-search]');
        var startInput = root.querySelector('[data-date-start]');
        var endInput = root.querySelector('[data-date-end]');
        var countEl = document.querySelector('[data-filter-count]');
        var statusButtons = root.querySelectorAll('[data-status-filter]');
        var status = 'all';

        var emptyRow = table ? table.querySelector('[data-filter-empty]') : null;
        var totalEl = document.querySelector('[data-filter-total]');

        function apply() {
            // Re-read rows each time: the "All" view appends older meetings later
            var rows = table ? Array.prototype.slice.call(table.querySelectorAll('tbody tr[data-search]')) : [];
            if (totalEl) { totalEl.textContent = rows.length; }
            var terms = (input && input.value ? input.value : '')
                .toLowerCase()
                .split(',')
                .map(function (t) { return t.trim(); })
                .filter(Boolean);
            var shown = 0;
            rows.forEach(function (row) {
                var text = row.dataset.search || '';
                var textOk = !terms.length || terms.some(function (t) { return text.indexOf(t) !== -1; });
                var statusOk = status === 'all'
                    || (status === 'video' ? row.dataset.video === 'yes' : row.dataset.status === status);
                row.hidden = !(textOk && statusOk);
                if (!row.hidden) { shown += 1; }
            });
            if (countEl) { countEl.textContent = shown; }
            if (emptyRow) { emptyRow.hidden = shown !== 0 || rows.length === 0; }
        }

        if (input) { input.addEventListener('input', apply); }

        var badge = root.querySelector('[data-filter-badge]');
        function updateBadge() {
            if (!badge) { return; }
            var n = (parseInt(badge.dataset.base, 10) || 0) + (status === 'all' ? 0 : 1);
            badge.textContent = n;
            badge.classList.toggle('d-none', n === 0);
        }

        statusButtons.forEach(function (btn) {
            btn.addEventListener('click', function () {
                status = btn.dataset.statusFilter;
                statusButtons.forEach(function (b) { b.classList.toggle('active', b === btn); });
                apply();
                updateBadge();
            });
        });

        // Keep the custom range valid: To >= From
        function syncDates() {
            if (!startInput || !endInput) { return true; }
            endInput.min = startInput.value || '';
            var bad = startInput.value && endInput.value && endInput.value < startInput.value;
            endInput.classList.toggle('is-invalid', !!bad);
            return !bad;
        }

        if (startInput && endInput) {
            startInput.addEventListener('change', syncDates);
            endInput.addEventListener('change', syncDates);
            syncDates();
        }

        root.addEventListener('submit', function (e) {
            if (!syncDates()) {
                e.preventDefault();
                var panel = root.querySelector('.collapse');
                if (panel && window.bootstrap) { window.bootstrap.Collapse.getOrCreateInstance(panel).show(); }
                endInput.focus();
            }
        });

        // "/" jumps to search, Esc clears it
        document.addEventListener('keydown', function (e) {
            var tag = (document.activeElement && document.activeElement.tagName) || '';
            if (e.key === '/' && tag !== 'INPUT' && tag !== 'TEXTAREA' && input) {
                e.preventDefault();
                input.focus();
            } else if (e.key === 'Escape' && document.activeElement === input && input.value) {
                input.value = '';
                apply();
            }
        });

        apply();
        // Let other scripts (e.g. "load older") re-run the filter on new rows
        if (table) { table.addEventListener('rows-added', apply); }
    }

    document.querySelectorAll('[data-filter-root]').forEach(initFilterBar);

    // Clicking anywhere on a table row with data-href opens it (links inside keep their
    // own behaviour, so Ctrl/middle-click on the subject still opens a new tab).
    document.addEventListener('click', function (e) {
        var row = e.target.closest('tr[data-href]');
        if (!row || e.target.closest('a, button, input, label, [data-bs-toggle]')) { return; }
        if (window.getSelection && String(window.getSelection())) { return; }  // selecting text
        window.location.href = row.dataset.href;
    });
    document.addEventListener('keydown', function (e) {
        var row = e.target.closest && e.target.closest('tr[data-href]');
        if (row && e.target === row && e.key === 'Enter') { window.location.href = row.dataset.href; }
    });

    if (window.bootstrap) {
        document.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(function (el) {
            window.bootstrap.Tooltip.getOrCreateInstance(el);
        });
    }
})();
