(function () {
  const state = {
    currentTask: null,
    currentDependencies: null,
    currentHandoffs: [],
    filter: "all",
    drag: null,
    suppressClickUntil: 0,
    hoveredCardId: null,
    depLinesVisible: true,
    boardDirty: false,
  };

  const apiBaseUrl = getApiBaseUrl();
  const boardArea = document.getElementById("boardArea");
  const createForm = document.getElementById("create-form");
  const detailOverlay = document.getElementById("detail-drawer");
  const noteForm = document.getElementById("note-form");
  const searchInput = document.getElementById("searchInput");
  const depSvg = document.getElementById("depSvg");
  const toastStack = document.getElementById("toastStack");
  let depFrame = null;
  let depLiveFrame = null;
  let depLastLayoutSignature = "";

  // Apply a locally-persisted theme override before first paint of interactions.
  try {
    const savedTheme = localStorage.getItem("flow.theme");
    if (savedTheme) document.documentElement.setAttribute("data-theme", savedTheme);
  } catch (_error) {}

  document.addEventListener("click", function () {
    closeFlowDropdowns();
  });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
      closeDetail();
      closeNewTask();
      closeIdeas();
      closeSettings();
      closeFlowDropdowns();
    }
    if (
      event.key.toLowerCase() === "n" &&
      !event.ctrlKey &&
      !event.metaKey &&
      !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)
    ) {
      event.preventDefault();
      openNewTask();
    }
  });
  window.addEventListener("message", function (event) {
    const data = event.data || {};
    if (data.type === "flow:close-ideas") closeIdeas();
    if (data.type === "flow:close-settings") closeSettings();
    // Defer the board refresh until the overlay is dismissed. Reloading the
    // parent while the iframe is open would destroy in-flight content such as a
    // freshly generated one-time API key before it can be copied.
    if (data.type === "flow:ideas-mutated" || data.type === "flow:settings-mutated") state.boardDirty = true;
    if (data.type === "flow:theme" && data.theme) applyTheme(data.theme);
  });

  if (createForm) createForm.addEventListener("submit", handleCreate);
  if (noteForm) noteForm.addEventListener("submit", handleNoteSubmit);
  if (searchInput) searchInput.addEventListener("input", applyBoardFilters);
  window.addEventListener("flow-scroll:init", function () {
    resetDepLayoutSignature();
    initDepHover();
    scheduleDepRender();
  });
  window.addEventListener("flow-scroll:position", function () {
    scheduleDepRender();
  });

  Object.assign(window, {
    switchColumn,
    setFilter,
    filterTasks: applyBoardFilters,
    applyBoardFilters,
    openNewTask,
    closeNewTask,
    openDetail,
    closeDetail,
    claimTask,
    releaseTask,
    completeTask,
    copyId,
    openIdeas,
    closeIdeas,
    openSettings,
    closeSettings,
  });

  initFlowDropdowns();
  initPriorityCounters();
  initMobile();
  initCardDrag();
  initTooltips();
  initDepLines();
  applyBoardFilters();

  window.switchColumn = switchColumn;
  window.setFilter = setFilter;
  window.filterTasks = applyBoardFilters;
  window.applyBoardFilters = applyBoardFilters;
  window.openNewTask = openNewTask;
  window.closeNewTask = closeNewTask;
  window.openDetail = openDetail;
  window.closeDetail = closeDetail;
  window.claimTask = claimTask;
  window.releaseTask = releaseTask;
  window.completeTask = completeTask;
  window.copyId = copyId;
  window.openIdeas = openIdeas;
  window.closeIdeas = closeIdeas;
  window.openSettings = openSettings;
  window.closeSettings = closeSettings;

  function getPriorityDots(priority) {
    const value = Number(priority) || 0;
    const filled = value >= 900 ? 3 : value >= 700 ? 2 : value >= 200 ? 1 : 0;
    let html = "";
    for (let index = 0; index < 3; index += 1) {
      html += '<span class="dot' + (index < filled ? " filled" : "") + '"></span>';
    }
    return html;
  }

  function switchColumn(tabEl) {
    document.querySelectorAll(".mobile-tab").forEach((tab) => tab.classList.remove("active"));
    tabEl.classList.add("active");
    const columnName = tabEl.dataset.column;
    document.querySelectorAll(".column[data-column]").forEach((column) => {
      column.style.display = column.dataset.column === columnName ? "" : "none";
    });
    localStorage.setItem("flow-active-column", columnName);
    scheduleDepRender();
  }

  function initMobile() {
    if (!window.matchMedia("(max-width: 768px)").matches) return;
    const saved = localStorage.getItem("flow-active-column") || "backlog";
    const tab = document.querySelector('.mobile-tab[data-column="' + cssEscape(saved) + '"]');
    if (tab) switchColumn(tab);
  }

  function initFlowDropdowns(scope) {
    (scope || document).querySelectorAll("[data-flow-select]").forEach((dropdown) => {
      if (dropdown.dataset.ready === "true") return;
      dropdown.dataset.ready = "true";
      const trigger = dropdown.querySelector(".flow-select-trigger");
      const options = Array.from(dropdown.querySelectorAll(".flow-select-option"));
      if (!trigger) return;
      trigger?.addEventListener("click", function (event) {
        event.stopPropagation();
        const shouldOpen = !dropdown.classList.contains("is-open");
        closeFlowDropdowns(dropdown);
        dropdown.classList.toggle("is-open", shouldOpen);
        trigger.setAttribute("aria-expanded", String(shouldOpen));
      });
      trigger.addEventListener("keydown", function (event) {
        if (event.key === "Escape") closeFlowDropdowns();
        if (event.key === "ArrowDown") {
          event.preventDefault();
          dropdown.classList.add("is-open");
          trigger.setAttribute("aria-expanded", "true");
          const selected = dropdown.querySelector(".flow-select-option.is-selected") || options[0];
          selected?.focus();
        }
      });
      options.forEach((option) => {
        option.addEventListener("click", function (event) {
          event.stopPropagation();
          setFlowDropdownValue(dropdown, option.dataset.value || "", option.textContent.trim());
          closeFlowDropdowns();
          const handler = dropdown.dataset.onChange;
          if (handler && typeof window[handler] === "function") window[handler]();
        });
        option.addEventListener("keydown", function (event) {
          if (event.key === "Escape") {
            closeFlowDropdowns();
            trigger.focus();
          }
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            option.click();
            trigger.focus();
          }
        });
      });
    });
  }

  function setFlowDropdownValue(dropdown, value, label) {
    const valueNode = dropdown.querySelector(".flow-select-value");
    const input = dropdown.querySelector('input[type="hidden"]');
    if (valueNode) valueNode.textContent = label;
    if (input) input.value = value;
    dropdown.querySelectorAll(".flow-select-option").forEach((option) => {
      const selected = (option.dataset.value || "") === value;
      option.classList.toggle("is-selected", selected);
      option.setAttribute("aria-selected", String(selected));
    });
  }

  function closeFlowDropdowns(except) {
    document.querySelectorAll("[data-flow-select].is-open").forEach((dropdown) => {
      if (dropdown === except) return;
      dropdown.classList.remove("is-open");
      dropdown.querySelector(".flow-select-trigger")?.setAttribute("aria-expanded", "false");
    });
  }

  function initPriorityCounters() {
    document.querySelectorAll(".priority-counter").forEach((counter) => {
      const input = counter.querySelector("input");
      if (!input) return;
      const min = Number(counter.dataset.min || 0);
      const max = Number(counter.dataset.max || 1000);
      const clamp = (value) => Math.max(min, Math.min(max, value));
      counter.querySelector(".priority-dec")?.addEventListener("click", () => {
        input.value = String(clamp((Number(input.value) || 0) - 1));
      });
      counter.querySelector(".priority-inc")?.addEventListener("click", () => {
        input.value = String(clamp((Number(input.value) || 0) + 1));
      });
      input.addEventListener("blur", () => {
        const value = Number(input.value);
        input.value = String(Number.isNaN(value) ? min : clamp(value));
      });
    });
  }

  function setFilter(element) {
    document.querySelectorAll(".filter-pill").forEach((pill) => pill.classList.remove("active"));
    element.classList.add("active");
    state.filter = element.dataset.filter || "all";
    applyBoardFilters();
  }

  function applyBoardFilters() {
    const query = (searchInput?.value || "").trim().toLowerCase();
    const projectFilter = document.querySelector('#projectFilter input[type="hidden"]')?.value || "";
    document.querySelectorAll(".task-card").forEach((card) => {
      const title = card.querySelector(".card-title")?.textContent.toLowerCase() || "";
      const taskId = (card.dataset.id || "").toLowerCase();
      const project = card.dataset.project || "";
      const assignee = card.dataset.assignee || "";
      const matchesSearch = !query || title.includes(query) || taskId.includes(query) || project.includes(query);
      const matchesProjectDropdown = !projectFilter || project === projectFilter;
      let matchesPill = true;
      if (state.filter === "human") matchesPill = card.dataset.human === "true";
      else if (state.filter === "unclaimed") matchesPill = assignee === "unclaimed";
      else if (state.filter !== "all") matchesPill = project === state.filter;
      card.style.display = matchesSearch && matchesProjectDropdown && matchesPill ? "" : "none";
    });
    syncColumnCountLabels();
    scheduleDepRender();
  }

  function syncColumnCountLabels() {
    document.querySelectorAll(".column[data-column]").forEach((column) => {
      const visible = Array.from(column.querySelectorAll(".task-card")).filter((card) => card.style.display !== "none").length;
      const headerCount = column.querySelector(".column-header .count");
      const tabCount = document.querySelector('.mobile-tab[data-column="' + cssEscape(column.dataset.column) + '"] .tab-count');
      if (headerCount) headerCount.textContent = String(visible);
      if (tabCount) tabCount.textContent = String(visible);
    });
  }

  async function handleCreate(event) {
    event.preventDefault();
    const payload = formPayload(createForm);
    payload.priority = Number(payload.priority || 500);
    try {
      await requestJson("/api/tasks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      showToast("Task created.");
      window.location.reload();
    } catch (error) {
      showToast(error.message, "error");
    }
  }

  function openNewTask() {
    document.getElementById("newTaskModal")?.classList.add("active");
    window.setTimeout(() => document.getElementById("create-title")?.focus(), 40);
  }

  function closeNewTask() {
    document.getElementById("newTaskModal")?.classList.remove("active");
  }

  async function openDetail(card) {
    if (shouldSuppressCardOpen(card)) return;
    const taskId = card.dataset.id;
    if (!taskId) return;
    try {
      const [task, dependencies, handoffs] = await Promise.all([
        requestJson("/api/tasks/" + encodeURIComponent(taskId)),
        requestJson("/api/tasks/" + encodeURIComponent(taskId) + "/dependencies"),
        requestJson("/api/tasks/" + encodeURIComponent(taskId) + "/handoffs"),
      ]);
      state.currentTask = task;
      state.currentDependencies = dependencies;
      state.currentHandoffs = handoffs || [];
      renderDetail(task, dependencies, handoffs || []);
      detailOverlay.classList.add("active");
    } catch (error) {
      showToast(error.message, "error");
    }
  }

  function closeDetail() {
    detailOverlay?.classList.remove("active");
  }

  function renderDetail(task, dependencies, handoffs) {
    setText("detailProject", task.project || "project");
    setText("detailId", task.id);
    setText("detailTitle", task.title || "Task");
    setText("detailDesc", task.description || "No description.");
    setText("detailMetaStatus", formatStatus(task.status));
    setText("detailMetaPriority", String(task.priority || 0));
    setText("detailMetaAssignee", task.assignee || "unclaimed");
    renderWarning(task, dependencies);
    renderAcceptance(task);
    renderDependencies(dependencies);
    renderNotes(task.notes || []);
    renderHandoffs(handoffs || []);
    renderTimeline(task, handoffs || []);
    renderTriage(task, dependencies);
    hydrateDetailForm(task);
  }

  function renderWarning(task, dependencies) {
    const warning = document.getElementById("detailWarning");
    const text = document.getElementById("detailWarningText");
    const blockers = dependencies?.blocked_by_tasks || [];
    const message =
      task.blocker_reason ||
      (task.human_required ? "Human review is required before this task can be completed." : "") ||
      (blockers.length
        ? "Blocked by " + blockers.length + " dependency" + (blockers.length === 1 ? ": " : " dependencies: ") + blockers.map((item) => item.id || item.title).filter(Boolean).join(", ")
        : "");
    if (text) text.textContent = message;
    if (warning) warning.classList.toggle("active", Boolean(message));
  }

  function renderAcceptance(task) {
    const list = document.getElementById("detailAcceptanceList");
    const count = document.getElementById("detailAcceptanceCount");
    if (!list) return;
    const items = splitLines(task.acceptance_criteria);
    if (count) count.textContent = String(items.length);
    if (!items.length) {
      list.innerHTML = '<li class="ac-empty">No acceptance criteria recorded.</li>';
      return;
    }
    list.innerHTML = items
      .map((item, index) => {
        const checked = getAcceptanceState(task.id, index);
        return (
          '<li class="ac-item" data-ac="' +
          index +
          '"><div class="ac-check' +
          (checked ? " checked" : "") +
          '" role="checkbox" tabindex="0" aria-checked="' +
          (checked ? "true" : "false") +
          '" onclick="window.FlowToggleAcceptance(this)" onkeydown="if(event.key===\'Enter\'||event.key===\' \'){event.preventDefault();window.FlowToggleAcceptance(this)}"><svg viewBox="0 0 16 16"><polyline points="3.5 8 7 11.5 12.5 4.5"/></svg></div><span class="ac-text"' +
          (checked ? ' style="text-decoration:line-through;color:var(--text-muted)"' : "") +
          ">" +
          escapeHtml(item) +
          "</span></li>"
        );
      })
      .join("");
  }

  window.FlowToggleAcceptance = function (button) {
    const item = button.closest(".ac-item");
    const index = Number(item?.dataset.ac || 0);
    const taskId = state.currentTask?.id;
    if (!taskId) return;
    const checked = !button.classList.contains("checked");
    button.classList.toggle("checked", checked);
    button.setAttribute("aria-checked", String(checked));
    const text = item.querySelector(".ac-text");
    if (text) {
      text.style.textDecoration = checked ? "line-through" : "";
      text.style.color = checked ? "var(--text-muted)" : "";
    }
    const states = JSON.parse(localStorage.getItem(taskId + "_ac") || "{}");
    states[index] = checked;
    localStorage.setItem(taskId + "_ac", JSON.stringify(states));
  };

  function getAcceptanceState(taskId, index) {
    try {
      const states = JSON.parse(localStorage.getItem(taskId + "_ac") || "{}");
      return Boolean(states[index]);
    } catch (_error) {
      return false;
    }
  }

  function renderDependencies(dependencies) {
    const list = document.getElementById("detailDepsList");
    const count = document.getElementById("detailDepsCount");
    if (!list) return;
    const parents = dependencies?.parent_tasks || [];
    const children = dependencies?.child_tasks || [];
    const total = parents.length + children.length;
    if (count) count.textContent = String(total);
    if (!total) {
      list.innerHTML = '<div class="empty-state">No dependency links.</div>';
      return;
    }
    const rows = [];
    parents.forEach((task) => rows.push(renderDepItem("parent", task)));
    children.forEach((task) => rows.push(renderDepItem("child", task)));
    list.innerHTML = rows.join("");
  }

  function renderDepItem(relation, task) {
    return (
      '<div class="dep-item"><span class="flow-chip dep-status status-' +
      escapeHtml(task.status || "todo") +
      '">' +
      escapeHtml(task.status || "todo") +
      '</span><div class="dep-body"><div class="dep-title"><span class="dep-id">' +
      escapeHtml(task.id) +
      '</span><span class="dep-name">' +
      escapeHtml(task.title || "") +
      '</span></div><div class="dep-meta"><span class="dep-relation">' +
      escapeHtml(relation) +
      "</span></div></div></div>"
    );
  }

  function renderNotes(notes) {
    const list = document.getElementById("detailNotesList");
    const count = document.getElementById("detailNotesCount");
    if (!list) return;
    if (count) count.textContent = String(notes.length);
    if (!notes.length) {
      list.innerHTML = '<div class="note-card"><div class="note-body"><p>No notes yet.</p></div></div>';
      return;
    }
    list.innerHTML = notes
      .map((note) => {
        return (
          '<div class="note-card"><div class="note-header"><span class="note-author">' +
          escapeHtml(note.author || "unknown") +
          '</span><span class="note-time">' +
          escapeHtml(formatDateTime(note.created_at)) +
          '</span></div><div class="note-body"><p>' +
          escapeHtml(note.body || "") +
          "</p></div></div>"
        );
      })
      .join("");
  }

  function renderHandoffs(handoffs) {
    const list = document.getElementById("detail-handoff");
    const count = document.getElementById("detailHandoffCount");
    if (!list) return;
    if (count) count.textContent = String(handoffs.length);
    if (!handoffs.length) {
      list.innerHTML = '<div class="note-card"><div class="note-body"><p>No handoff recorded.</p></div></div>';
      return;
    }
    list.innerHTML = handoffs
      .map((handoff) => {
        const details = [
          handoff.outcome ? "Outcome: " + handoff.outcome : "",
          handoff.next_recommended_agent ? "Next: " + handoff.next_recommended_agent : "",
          handoff.remaining_work ? "Remaining: " + handoff.remaining_work : "",
        ].filter(Boolean);
        return (
          '<div class="note-card handoff-card"><div class="note-header"><span class="note-author">' +
          escapeHtml(handoff.author || "unknown") +
          '</span><span class="note-time">' +
          escapeHtml(formatDateTime(handoff.created_at)) +
          '</span></div><div class="note-body"><p>' +
          escapeHtml(handoff.summary || "") +
          "</p>" +
          (details.length ? '<p class="dep-meta">' + details.map(escapeHtml).join(" / ") + "</p>" : "") +
          "</div></div>"
        );
      })
      .join("");
  }

  function renderTimeline(task, handoffs) {
    const list = document.getElementById("detailTimelineList");
    if (!list) return;
    const notes = task.notes || [];
    const latestNote = notes.length ? notes[notes.length - 1] : null;
    const latestHandoff = handoffs && handoffs.length ? handoffs[0] : task.latest_handoff;
    const rows = [
      latestHandoff ? [formatDateTime(latestHandoff.created_at), "handoff recorded"] : null,
      latestNote ? [formatDateTime(latestNote.created_at), "note from " + (latestNote.author || "unknown")] : null,
      task.updated_at ? [formatDateTime(task.updated_at), "updated"] : null,
      task.created_at ? [formatDateTime(task.created_at), "created"] : null,
    ].filter((row) => row && row[0]);
    if (!rows.length) {
      list.innerHTML = '<div class="timeline-entry"><span class="timeline-label">No activity recorded.</span></div>';
      return;
    }
    list.innerHTML = rows
      .map(([time, label]) => {
        return '<div class="timeline-entry"><span class="timeline-time">' + escapeHtml(time) + '</span><span class="timeline-label">' + escapeHtml(label) + "</span></div>";
      })
      .join("");
  }

  function renderTriage(task, dependencies) {
    const triage = document.getElementById("detailTriageRow");
    if (!triage) return;
    const totalDeps = (dependencies?.parent_tasks?.length || 0) + (dependencies?.child_tasks?.length || 0);
    const rows = [
      ["complexity", task.complexity || "small"],
      ["impact", task.impact || "medium"],
      ["effort", task.effort || "medium"],
      ["risk", task.risk || "low"],
      ["links", totalDeps ? String(totalDeps) : "none"],
    ];
    triage.innerHTML = rows
      .map(([label, value]) => {
        return '<div class="system-row"><span class="system-label">' + escapeHtml(label) + '</span><span class="flow-chip ' + triageChipClass(value) + '">' + escapeHtml(value) + "</span></div>";
      })
      .join("");
  }

  function hydrateDetailForm(task) {
    const form = document.getElementById("detail-form");
    if (!form) return;
    Object.keys(task).forEach((key) => {
      if (form.elements[key] && form.elements[key].type !== "checkbox") form.elements[key].value = task[key] == null ? "" : task[key];
    });
    if (form.elements.human_required) form.elements.human_required.checked = Boolean(task.human_required);
  }

  async function handleNoteSubmit(event) {
    event.preventDefault();
    if (!state.currentTask) return;
    const noteInput = document.getElementById("detailNoteInput");
    const note = noteInput?.value.trim();
    if (!note) return;
    try {
      const task = await requestJson("/api/tasks/" + encodeURIComponent(state.currentTask.id) + "/note", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ note }),
      });
      state.currentTask = task;
      noteInput.value = "";
      renderNotes(task.notes || []);
      renderTimeline(task, state.currentHandoffs || []);
      showToast("Note added.");
    } catch (error) {
      showToast(error.message, "error");
    }
  }

  async function claimTask() {
    if (!state.currentTask) return;
    const agentName = window.prompt("Agent name");
    if (!agentName) return;
    await mutateCurrent("/claim", { agent_name: agentName });
  }

  async function releaseTask() {
    await mutateCurrent("/release", {});
  }

  async function completeTask() {
    if (!state.currentTask) return;
    const summary = window.prompt("Completion summary");
    if (!summary) return;
    await mutateCurrent("/done", { summary });
  }

  async function mutateCurrent(suffix, payload) {
    if (!state.currentTask) return;
    try {
      await requestJson("/api/tasks/" + encodeURIComponent(state.currentTask.id) + suffix, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload || {}),
      });
      showToast("Task updated.");
      window.location.reload();
    } catch (error) {
      showToast(error.message, "error");
    }
  }

  function copyId() {
    const id = document.getElementById("detailId")?.textContent || "";
    if (!id) return;
    navigator.clipboard?.writeText(id).then(
      () => showToast("Copied " + id),
      () => showToast(id)
    );
  }

  function initCardDrag() {
    document.querySelectorAll(".task-card").forEach((card) => {
      card.addEventListener("pointerdown", handleCardPointerDown);
    });
  }

  function handleCardPointerDown(event) {
    if (event.button !== 0) return;
    const card = event.currentTarget;
    const list = card.closest(".card-list");
    if (!list) return;
    const rect = card.getBoundingClientRect();
    state.drag = {
      active: false,
      card,
      list,
      pointerId: event.pointerId,
      sourceColumn: list.dataset.column,
      startX: event.clientX,
      startY: event.clientY,
      offsetX: event.clientX - rect.left,
      offsetY: event.clientY - rect.top,
      placeholder: null,
      targetList: list,
    };
    try {
      card.setPointerCapture(event.pointerId);
    } catch (_error) {}
    document.addEventListener("pointermove", handleCardPointerMove, true);
    document.addEventListener("pointerup", handleCardPointerUp, true);
    document.addEventListener("pointercancel", handleCardPointerCancel, true);
  }

  function handleCardPointerMove(event) {
    const drag = state.drag;
    if (!drag || event.pointerId !== drag.pointerId) return;
    const distance = Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY);
    if (!drag.active && distance < 8) return;
    if (!drag.active) startDrag(drag);
    event.preventDefault();
    positionDrag(drag, event.clientX, event.clientY);
    movePlaceholder(drag, event.clientX, event.clientY);
    autoScrollDuringDrag(drag, event.clientX, event.clientY);
  }

  function handleCardPointerUp(event) {
    const drag = state.drag;
    if (!drag || event.pointerId !== drag.pointerId) return;
    if (!drag.active) {
      cleanupDrag(false);
      return;
    }
    event.preventDefault();
    finishDrop(drag);
  }

  function handleCardPointerCancel() {
    cancelDrag();
  }

  function startDrag(drag) {
    const rect = drag.card.getBoundingClientRect();
    const placeholder = document.createElement("div");
    placeholder.className = "drag-placeholder";
    placeholder.style.height = rect.height + "px";
    drag.card.parentNode.insertBefore(placeholder, drag.card.nextSibling);
    drag.placeholder = placeholder;
    drag.active = true;
    drag.card.classList.add("dragging");
    drag.card.style.position = "fixed";
    drag.card.style.left = rect.left + "px";
    drag.card.style.top = rect.top + "px";
    drag.card.style.width = rect.width + "px";
    drag.card.style.height = rect.height + "px";
    drag.card.style.zIndex = "300";
    drag.card.style.pointerEvents = "none";
    document.body.classList.add("is-card-dragging");
  }

  function positionDrag(drag, x, y) {
    drag.card.style.left = x - drag.offsetX + "px";
    drag.card.style.top = y - drag.offsetY + "px";
  }

  function movePlaceholder(drag, x, y) {
    const target = document.elementFromPoint(x, y);
    const list = target?.closest?.(".card-list") || drag.targetList;
    if (!list) return;
    drag.targetList = list;
    document.querySelectorAll(".column.drag-over").forEach((column) => column.classList.remove("drag-over"));
    list.closest(".column")?.classList.add("drag-over");
    const cards = Array.from(list.querySelectorAll(".task-card:not(.dragging)")).filter((card) => card.style.display !== "none");
    const before = cards.find((card) => y < card.getBoundingClientRect().top + card.getBoundingClientRect().height / 2);
    if (before) list.insertBefore(drag.placeholder, before);
    else list.appendChild(drag.placeholder);
  }

  function autoScrollDuringDrag(drag, x, y) {
    const margin = 56;
    const step = 18;
    if (boardArea) {
      if (x < margin) boardArea.scrollLeft -= step;
      else if (x > window.innerWidth - margin) boardArea.scrollLeft += step;
    }

    const list = drag.targetList;
    if (!list) return;
    const rect = list.getBoundingClientRect();
    if (y < rect.top + margin) list.scrollTop -= step;
    else if (y > rect.bottom - margin) list.scrollTop += step;
  }

  async function finishDrop(drag) {
    const targetList = drag.placeholder?.parentElement || drag.list;
    const targetColumn = targetList.dataset.column;
    const sourceColumn = drag.sourceColumn;
    targetList.insertBefore(drag.card, drag.placeholder);
    resetDragCard(drag.card);
    cleanupDrag(true);
    if (!targetColumn || targetColumn === sourceColumn) {
      scheduleDepRender();
      return;
    }
    try {
      const task = await requestJson("/api/tasks/" + encodeURIComponent(drag.card.dataset.id) + "/move", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: targetColumn }),
      });
      drag.card.dataset.status = task.status;
      syncCardFromTask(drag.card, task);
      showToast(task.id + " moved to " + formatStatus(task.status));
      syncColumnCountLabels();
      scheduleDepRender();
    } catch (error) {
      showToast(error.message, "error");
      window.location.reload();
    }
  }

  function cancelDrag() {
    const drag = state.drag;
    if (!drag) return;
    if (drag.placeholder && drag.list) drag.list.insertBefore(drag.card, drag.placeholder);
    resetDragCard(drag.card);
    cleanupDrag(false);
  }

  function resetDragCard(card) {
    card.classList.remove("dragging");
    card.style.position = "";
    card.style.left = "";
    card.style.top = "";
    card.style.width = "";
    card.style.height = "";
    card.style.zIndex = "";
    card.style.pointerEvents = "";
  }

  function cleanupDrag(suppressClick) {
    const drag = state.drag;
    if (!drag) return;
    try {
      drag.card.releasePointerCapture(drag.pointerId);
    } catch (_error) {}
    drag.placeholder?.remove();
    document.removeEventListener("pointermove", handleCardPointerMove, true);
    document.removeEventListener("pointerup", handleCardPointerUp, true);
    document.removeEventListener("pointercancel", handleCardPointerCancel, true);
    document.querySelectorAll(".column.drag-over").forEach((column) => column.classList.remove("drag-over"));
    document.body.classList.remove("is-card-dragging");
    if (suppressClick) {
      drag.card.dataset.dragSuppress = "true";
      state.suppressClickUntil = nowMs() + 450;
      window.setTimeout(() => delete drag.card.dataset.dragSuppress, 500);
    }
    state.drag = null;
  }

  function shouldSuppressCardOpen(card) {
    if (!card) return false;
    return card.dataset.dragSuppress === "true" || state.suppressClickUntil > nowMs();
  }

  function syncCardFromTask(card, task) {
    card.dataset.priority = String(task.priority || 0);
    card.dataset.project = task.project || "";
    card.dataset.assignee = task.assignee || "unclaimed";
    card.dataset.human = task.human_required ? "true" : "false";
    const dots = card.querySelector(".priority-dots");
    if (dots) {
      dots.dataset.tip = "P" + (task.priority || 0);
      dots.innerHTML = getPriorityDots(task.priority);
    }
  }

  function initTooltips() {
    const tip = document.createElement("div");
    tip.id = "hover-tip";
    document.body.appendChild(tip);
    let timer = null;
    document.querySelectorAll(".agent-avatar[title], .priority-dots[title]").forEach((element) => {
      element.dataset.tip = element.getAttribute("title");
      element.removeAttribute("title");
      element.addEventListener("mouseenter", function () {
        clearTimeout(timer);
        timer = window.setTimeout(() => {
          tip.textContent = element.dataset.tip || "";
          tip.style.opacity = "1";
          const rect = element.getBoundingClientRect();
          tip.style.left = rect.left + rect.width / 2 + "px";
          tip.style.bottom = window.innerHeight - rect.top + 8 + "px";
        }, 450);
      });
      element.addEventListener("mouseleave", function () {
        clearTimeout(timer);
        tip.style.opacity = "0";
      });
    });
  }

  function initDepLines() {
    state.depLinesVisible = getStoredDepLinesVisible();
    updateDepLinesUI();
    if (state.depLinesVisible) {
      scheduleFrame(function () {
        renderDepDots();
        initDepHover();
      });
    }
    if (boardArea) boardArea.addEventListener("scroll", scheduleDepRender, { passive: true });
    document.querySelectorAll(".card-list").forEach(function (list) {
      list.addEventListener("scroll", scheduleDepRender, { passive: true });
    });
    window.addEventListener("resize", scheduleDepRender);
    if (window.visualViewport) window.visualViewport.addEventListener("resize", scheduleDepRender);
    startDepLiveRender();
    window.addEventListener("storage", function (event) {
      if (event.key !== "flow-dep-lines") return;
      state.depLinesVisible = event.newValue !== "false";
      updateDepLinesUI();
      resetDepLayoutSignature();
      renderDepDots();
      clearHighlight();
      if (state.depLinesVisible) startDepLiveRender();
    });
  }

  function scheduleDepRender() {
    if (depFrame) return;
    depFrame = scheduleFrame(() => {
      depFrame = null;
      renderDepDots();
    });
  }

  function startDepLiveRender() {
    if (depLiveFrame || !state.depLinesVisible) return;
    depLiveFrame = scheduleFrame(function tick() {
      depLiveFrame = null;
      if (!state.depLinesVisible) {
        resetDepLayoutSignature();
        return;
      }
      const signature = getDepLayoutSignature();
      if (signature !== depLastLayoutSignature) {
        renderDepDots();
      }
      depLiveFrame = scheduleFrame(tick);
    });
  }

  function getDepLayoutSignature() {
    const parts = [window.innerWidth, window.innerHeight];
    if (window.visualViewport) {
      parts.push(Math.round(window.visualViewport.width), Math.round(window.visualViewport.height));
    }
    if (boardArea) {
      parts.push(Math.round(boardArea.scrollLeft), Math.round(boardArea.scrollTop), boardArea.clientWidth, boardArea.clientHeight);
    }
    document.querySelectorAll(".card-list").forEach(function (list) {
      parts.push(list.dataset.column || "", Math.round(list.scrollLeft), Math.round(list.scrollTop), list.clientWidth, list.clientHeight);
    });
    return parts.join("|");
  }

  function resetDepLayoutSignature() {
    depLastLayoutSignature = "";
  }

  function renderDepDots() {
    const svg = document.getElementById("depSvg");
    if (!svg) return;
    svg.innerHTML = "";
    if (!state.depLinesVisible) return;
    const viewport = syncDepSvgViewport(svg);
    const h = viewport.h;
    const defs = document.createElementNS("http://www.w3.org/2000/svg", "defs");
    const clipCache = {};
    svg.appendChild(defs);

    getDependencyEdges().forEach(function (pair) {
      const blockerId = pair[0];
      const blockedId = pair[1];
      const blocker = document.querySelector('.task-card[data-id="' + cssEscape(blockerId) + '"]');
      const blocked = document.querySelector('.task-card[data-id="' + cssEscape(blockedId) + '"]');
      if (!blocker || !blocked) return;
      if (blocker.style.display === "none" || blocked.style.display === "none") return;

      const blockerCol = blocker.closest(".column");
      const blockedCol = blocked.closest(".column");
      if (blockerCol && getComputedStyle(blockerCol).display === "none") return;
      if (blockedCol && getComputedStyle(blockedCol).display === "none") return;

      const bRect = blocker.getBoundingClientRect();
      const dRect = blocked.getBoundingClientRect();
      if (bRect.bottom < 0 && dRect.bottom < 0) return;
      if (bRect.top > h && dRect.top > h) return;

      const bY = bRect.top + bRect.height / 2;
      const dY = dRect.top + dRect.height / 2;
      const points = depEdgePoints(bRect, dRect, bY, dY);
      [
        { x: points.x1, y: points.y1, id: blockerId, card: blocker },
        { x: points.x2, y: points.y2, id: blockedId, card: blocked },
      ].forEach(function (point) {
        const clip = getDepCardClipRect(point.card);
        const clipId = depClipPathForCard(svg, defs, clipCache, point.card);
        if (!clipId || !depPointInsideClip(point, clip)) return;

        const dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
        dot.setAttribute("cx", point.x);
        dot.setAttribute("cy", point.y);
        dot.setAttribute("r", "3");
        dot.setAttribute("fill", "#ff8fbe");
        dot.setAttribute("clip-path", "url(#" + clipId + ")");
        dot.setAttribute("data-card", point.id);
        dot.setAttribute("data-dep-from", blockerId);
        dot.setAttribute("data-dep-to", blockedId);
        dot.classList.add("dep-dot");
        svg.appendChild(dot);
      });
    });

    if (state.hoveredCardId) highlightCardDeps(state.hoveredCardId);
    depLastLayoutSignature = getDepLayoutSignature();
  }

  function updateDepLinesUI() {
    if (!depSvg) return;
    depSvg.style.opacity = state.depLinesVisible ? "1" : "0";
  }

  function getStoredDepLinesVisible() {
    try {
      return localStorage.getItem("flow-dep-lines") !== "false";
    } catch (_error) {
      return true;
    }
  }

  function syncDepSvgViewport(svg) {
    const width = window.innerWidth;
    const height = window.innerHeight;
    svg.setAttribute("width", width);
    svg.setAttribute("height", height);
    svg.setAttribute("viewBox", "0 0 " + width + " " + height);
    return { w: width, h: height };
  }

  function getDepCardClipRect(card) {
    const list = card?.closest(".card-list");
    if (!list) return null;
    const rect = list.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return null;
    return {
      list,
      left: rect.left,
      top: rect.top,
      right: rect.right,
      bottom: rect.bottom,
      width: rect.width,
      height: rect.height,
    };
  }

  function depPointInsideClip(point, clip) {
    return Boolean(clip && point.x >= clip.left && point.x <= clip.right && point.y >= clip.top && point.y <= clip.bottom);
  }

  function depClipPathForCard(svg, defs, clipCache, card) {
    const clip = getDepCardClipRect(card);
    if (!clip) return null;
    const key = clip.list.dataset.column || String(Array.from(document.querySelectorAll(".card-list")).indexOf(clip.list));
    if (clipCache[key]) return clipCache[key];
    const id = "depClip-" + key.replace(/[^a-zA-Z0-9_-]/g, "-");
    const clipPath = document.createElementNS("http://www.w3.org/2000/svg", "clipPath");
    const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    clipPath.setAttribute("id", id);
    clipPath.setAttribute("clipPathUnits", "userSpaceOnUse");
    rect.setAttribute("x", String(clip.left));
    rect.setAttribute("y", String(clip.top));
    rect.setAttribute("width", String(clip.width));
    rect.setAttribute("height", String(clip.height));
    clipPath.appendChild(rect);
    defs.appendChild(clipPath);
    clipCache[key] = id;
    return id;
  }

  function depEdgePoints(fromRect, toRect, fromY, toY) {
    return { x1: fromRect.left, y1: fromY, x2: toRect.right, y2: toY };
  }

  function depMakePath(svg, x1, y1, x2, y2, isResolved, fromId, toId) {
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    const dx = Math.abs(x2 - x1);
    const dy = Math.abs(y2 - y1);
    const offset = Math.max(28, Math.min(140, dx * 0.38 + dy * 0.08));
    path.setAttribute("d", "M " + x1 + " " + y1 + " C " + (x1 - offset) + " " + y1 + ", " + (x2 + offset) + " " + y2 + ", " + x2 + " " + y2);
    path.setAttribute("stroke", "#ff8fbe");
    path.setAttribute("stroke-width", isResolved ? "1" : "1.5");
    path.setAttribute("fill", "none");
    path.setAttribute("stroke-linecap", "round");
    if (isResolved) path.setAttribute("stroke-dasharray", "4 3");
    path.setAttribute("data-from", fromId || "");
    path.setAttribute("data-to", toId || "");
    path.classList.add("dep-path");
    svg.appendChild(path);
  }

  function highlightCardDeps(cardId) {
    const svg = document.getElementById("depSvg");
    if (!svg || !state.depLinesVisible) return;
    syncDepSvgViewport(svg);
    svg.querySelectorAll(".dep-path").forEach(function (path) {
      path.remove();
    });

    getDependencyEdges().forEach(function (pair) {
      const fromId = pair[0];
      const toId = pair[1];
      if (fromId !== cardId && toId !== cardId) return;

      const blocker = document.querySelector('.task-card[data-id="' + cssEscape(fromId) + '"]');
      const blocked = document.querySelector('.task-card[data-id="' + cssEscape(toId) + '"]');
      if (!blocker || !blocked) return;
      if (blocker.style.display === "none" || blocked.style.display === "none") return;

      const blockerCol = blocker.closest(".column");
      const blockedCol = blocked.closest(".column");
      if (blockerCol && getComputedStyle(blockerCol).display === "none") return;
      if (blockedCol && getComputedStyle(blockedCol).display === "none") return;

      const bRect = blocker.getBoundingClientRect();
      const dRect = blocked.getBoundingClientRect();
      const bY = bRect.top + bRect.height / 2;
      const dY = dRect.top + dRect.height / 2;
      const isResolved = blockerCol && blockerCol.dataset.column === "done";
      const points = depEdgePoints(bRect, dRect, bY, dY);
      depMakePath(svg, points.x1, points.y1, points.x2, points.y2, isResolved, fromId, toId);
    });

    svg.querySelectorAll(".dep-dot").forEach(function (dot) {
      const depFrom = dot.getAttribute("data-dep-from");
      const depTo = dot.getAttribute("data-dep-to");
      if (depFrom === cardId || depTo === cardId) {
        dot.setAttribute("r", "3.5");
        dot.setAttribute("fill", "#ff8fbe");
      }
    });
  }

  function clearHighlight() {
    const svg = document.getElementById("depSvg");
    if (!svg) return;
    svg.querySelectorAll(".dep-path").forEach(function (path) {
      path.remove();
    });
    svg.querySelectorAll(".dep-dot").forEach(function (dot) {
      dot.removeAttribute("opacity");
      dot.setAttribute("r", "3");
      dot.setAttribute("fill", "#ff8fbe");
    });
  }

  function initDepHover() {
    document.querySelectorAll(".task-card[data-id]").forEach(function (card) {
      if (card.dataset.depHoverReady === "true") return;
      card.dataset.depHoverReady = "true";
      card.addEventListener("mouseenter", function () {
        if (!state.depLinesVisible) return;
        state.hoveredCardId = card.dataset.id;
        renderDepDots();
        card.style.zIndex = "10";
      });
      card.addEventListener("mouseleave", function () {
        if (state.hoveredCardId === card.dataset.id) {
          state.hoveredCardId = null;
          card.style.zIndex = "";
          clearHighlight();
        }
      });
    });
  }

  function getDependencyEdges() {
    return (window.FlowBoard && window.FlowBoard.dependencyEdges) || [];
  }

  function openIdeas(expandNew) {
    const overlay = document.getElementById("ideasOverlay");
    const frame = document.getElementById("ideasFrame");
    const selectedProject = document.querySelector('#projectFilter input[type="hidden"]')?.value || "";
    const params = new URLSearchParams({ embedded: "1" });
    if (selectedProject) params.set("project", selectedProject);
    if (expandNew) params.set("new", "1");
    frame.src = "/ideas.html?" + params.toString();
    overlay.classList.add("active");
  }

  function closeIdeas() {
    if (flushBoardDirty()) return;
    const overlay = document.getElementById("ideasOverlay");
    const frame = document.getElementById("ideasFrame");
    overlay?.classList.remove("active");
    window.setTimeout(() => {
      if (overlay && !overlay.classList.contains("active")) frame.src = "about:blank";
    }, 220);
  }

  function openSettings() {
    const overlay = document.getElementById("settingsOverlay");
    const frame = document.getElementById("settingsFrame");
    frame.src = "/settings.html?embedded=1";
    overlay.classList.add("active");
  }

  function closeSettings() {
    if (flushBoardDirty()) return;
    const overlay = document.getElementById("settingsOverlay");
    const frame = document.getElementById("settingsFrame");
    overlay?.classList.remove("active");
    window.setTimeout(() => {
      if (overlay && !overlay.classList.contains("active")) frame.src = "about:blank";
    }, 220);
  }

  // Reload the board once an overlay closes if an embedded surface reported a
  // mutation, so project/key changes are reflected without a disruptive reload
  // while the user is still working inside the overlay.
  function flushBoardDirty() {
    if (!state.boardDirty) return false;
    state.boardDirty = false;
    window.location.reload();
    return true;
  }

  function applyTheme(theme) {
    if (!theme) return;
    document.documentElement.setAttribute("data-theme", theme);
    try {
      localStorage.setItem("flow.theme", theme);
    } catch (_error) {}
    scheduleDepRender();
  }

  function formPayload(form) {
    const payload = {};
    new FormData(form).forEach((value, key) => {
      payload[key] = typeof value === "string" ? value.trim() : value;
    });
    return payload;
  }

  async function requestJson(url, options) {
    const response = await fetch(resolveApiUrl(url), {
      ...(options || {}),
      headers: {
        Accept: "application/json",
        ...((options && options.headers) || {}),
      },
    });
    const text = await response.text();
    let data = null;
    if (text) {
      try {
        data = JSON.parse(text);
      } catch (_error) {
        data = null;
      }
    }
    if (!response.ok) {
      const message = (data && (data.detail || data.error)) || "Request failed with HTTP " + response.status + ".";
      throw new Error(Array.isArray(message) ? "Validation failed." : String(message));
    }
    return data;
  }

  function getApiBaseUrl() {
    const meta = document.querySelector('meta[name="flow-api-base"]');
    return String((window.Flow && window.Flow.apiBaseUrl) || window.FLOW_API_BASE_URL || (meta && meta.content) || "").replace(/\/+$/, "");
  }

  function resolveApiUrl(url) {
    if (!apiBaseUrl || /^https?:\/\//i.test(url)) return url;
    return apiBaseUrl + (url.startsWith("/") ? url : "/" + url);
  }

  function showToast(message, tone) {
    if (!toastStack) return;
    const toast = document.createElement("div");
    toast.className = "toast" + (tone === "error" ? " toast-error" : " toast-success");
    toast.textContent = message || "Done.";
    toastStack.appendChild(toast);
    window.setTimeout(() => toast.remove(), 3000);
  }

  function setText(id, value) {
    const node = document.getElementById(id);
    if (node) node.textContent = value == null ? "" : String(value);
  }

  function splitLines(value) {
    return String(value || "")
      .split(/\r?\n/)
      .map((line) => line.replace(/^[-*]\s*/, "").trim())
      .filter(Boolean);
  }

  function formatStatus(status) {
    return String(status || "")
      .replace(/_/g, " ")
      .replace(/\b\w/g, (match) => match.toUpperCase());
  }

  function formatDateTime(value) {
    const date = value ? new Date(value) : null;
    if (!date || Number.isNaN(date.getTime())) return "";
    return date.toISOString().slice(0, 16).replace("T", " ");
  }

  function triageChipClass(value) {
    const normalized = String(value || "").toLowerCase();
    if (["critical", "high", "large", "epic"].includes(normalized)) return "chip-error";
    if (["medium"].includes(normalized)) return "chip-warning";
    if (["low", "small", "trivial"].includes(normalized)) return "chip-positive";
    return "chip-neutral";
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function cssEscape(value) {
    if (window.CSS && typeof window.CSS.escape === "function") return window.CSS.escape(String(value));
    return String(value).replace(/["\\]/g, "\\$&");
  }

  function scheduleFrame(callback) {
    if (typeof window.requestAnimationFrame === "function") return window.requestAnimationFrame(callback);
    return window.setTimeout(() => callback(nowMs()), 16);
  }

  function nowMs() {
    return window.performance && typeof window.performance.now === "function" ? window.performance.now() : Date.now();
  }

})();
