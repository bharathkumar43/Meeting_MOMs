let discussionCount = 1;
let decisionCount = 1;

function addField(type) {
    if (type === 'discussion') {
        discussionCount++;
        const container = document.getElementById('discussionContainer');
        const div = document.createElement('div');
        div.className = 'input-group mb-2 discussion-item';
        div.innerHTML = `
            <span class="input-group-text">${discussionCount}</span>
            <input type="text" class="form-control" name="discussion_points"
                   placeholder="Enter a discussion point...">
            <button type="button" class="btn btn-outline-danger" onclick="removeField(this)">
                <i class="bi bi-trash"></i>
            </button>
        `;
        container.appendChild(div);
    } else if (type === 'decision') {
        decisionCount++;
        const container = document.getElementById('decisionContainer');
        const div = document.createElement('div');
        div.className = 'input-group mb-2 decision-item';
        div.innerHTML = `
            <span class="input-group-text">${decisionCount}</span>
            <input type="text" class="form-control" name="decisions"
                   placeholder="Enter a decision made...">
            <button type="button" class="btn btn-outline-danger" onclick="removeField(this)">
                <i class="bi bi-trash"></i>
            </button>
        `;
        container.appendChild(div);
    }
}

function addActionItem() {
    const container = document.getElementById('actionContainer');
    const div = document.createElement('div');
    div.className = 'row g-2 mb-2 action-item align-items-center';
    div.innerHTML = `
        <div class="col-md-5">
            <input type="text" class="form-control" name="action_desc"
                   placeholder="Action item description...">
        </div>
        <div class="col-md-3">
            <input type="text" class="form-control" name="action_assignee"
                   placeholder="Assigned to...">
        </div>
        <div class="col-md-3">
            <input type="date" class="form-control" name="action_due">
        </div>
        <div class="col-md-1">
            <button type="button" class="btn btn-outline-danger btn-sm" onclick="removeField(this)">
                <i class="bi bi-trash"></i>
            </button>
        </div>
    `;
    container.appendChild(div);
}

function removeField(btn) {
    const parent = btn.closest('.input-group, .row');
    if (parent) {
        parent.remove();
        renumberFields();
    }
}

function renumberFields() {
    document.querySelectorAll('#discussionContainer .discussion-item').forEach((item, idx) => {
        const badge = item.querySelector('.input-group-text');
        if (badge) badge.textContent = idx + 1;
    });
    discussionCount = document.querySelectorAll('#discussionContainer .discussion-item').length;

    document.querySelectorAll('#decisionContainer .decision-item').forEach((item, idx) => {
        const badge = item.querySelector('.input-group-text');
        if (badge) badge.textContent = idx + 1;
    });
    decisionCount = document.querySelectorAll('#decisionContainer .decision-item').length;
}
