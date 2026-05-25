(function () {
  const state = {
    currentTask: null,
    currentDependencies: null,
    currentHandoffs: [],
    currentProjectSlug: null,
    currentIdea: null,
    apiKeys: [],
    ideas: [],
    importItems: [],
    dragState: null,
    showArchivedIdeas: false,
    humanRequiredOnly: false,
    suppressClickUntil: 0,
  };

  const DRAG_THRESHOLD_PX = 8;
  const apiBaseUrl = getApiBaseUrl();

  const boardNode = document.querySelector(".board");
  const columnNodes = Array.from(document.querySelectorAll(".column[data-column]"));
  const cardLists = Array.from(document.querySelectorAll("[data-drop-zone]"));
  const detailDrawer = document.getElementById("detail-drawer");
  const createDrawer = document.getElementById("create-drawer");
  const projectsDrawer = document.getElementById("projects-drawer");
  const apiKeysDrawer = document.getElementById("api-keys-drawer");
  const importDrawer = document.getElementById("import-drawer");
  const ideasWorkspace = document.getElementById("ideas-workspace");
  const settingsDrawer = document.getElementById("settings-drawer");
  const detailForm = document.getElementById("detail-form");
  const createForm = document.getElementById("create-form");
  const projectForm = document.getElementById("project-form");
  const apiKeyForm = document.getElementById("api-key-form");
  const importForm = document.getElementById("import-form");
  const ideaEditForm = document.getElementById("idea-edit-form");
  const ideaPromoteForm = document.getElementById("idea-promote-form");
  const noteForm = document.getElementById("note-form");
  const notesNode = document.getElementById("detail-notes");
  const dependenciesNode = document.getElementById("detail-dependencies");
  const handoffNode = document.getElementById("detail-handoff");
  const jsonNode = document.getElementById("detail-json");
  const sourceNode = document.getElementById("detail-source");
  const projectList = document.getElementById("project-list");
  const projectFormTitle = document.getElementById("project-form-title");
  const projectFormCancel = document.getElementById("project-form-cancel");
  const projectSubmitButton = document.getElementById("project-submit-button");
  const projectSlugHint = document.getElementById("project-slug-hint");
  const toastStack = document.getElementById("toast-stack");
  const apiKeyList = document.getElementById("api-key-list");
  const generatedApiKey = document.getElementById("generated-api-key");
  const generatedApiKeyValue = document.getElementById("generated-api-key-value");
  const importPreview = document.getElementById("import-preview");
  const importFile = document.getElementById("import-file");
  const commitImportButton = document.getElementById("commit-import");
  const projectSelect = document.getElementById("project-select");
  const ideasList = document.getElementById("ideas-list");
  const ideasWorkspaceTitle = document.getElementById("ideas-workspace-title");
  const ideasQuickAddInput = document.getElementById("ideas-quick-add-input");
  const ideasQuickAddButton = document.getElementById("ideas-quick-add-btn");
  const ideasArchivedToggle = document.getElementById("ideas-archived-toggle");
  const ideaDetail = document.getElementById("idea-detail");
  const ideaDetailTitle = document.getElementById("idea-detail-title");
  const ideaDetailStatus = document.getElementById("idea-detail-status");
  const ideaDetailMeta = document.getElementById("idea-detail-meta");
  const ideaDetailDescription = document.getElementById("idea-detail-description");
  const humanFilterButton = document.querySelector("[data-filter-human-required]");
  const clearHumanFilterButton = document.querySelector("[data-clear-human-filter]");

  document.addEventListener("click", handleClick);
  document.addEventListener("keydown", handleKeydown);
  detailForm.addEventListener("submit", handleDetailSave);
  createForm.addEventListener("submit", handleCreate);
  projectForm.addEventListener("submit", handleProjectSubmit);
  if (apiKeyForm) {
    apiKeyForm.addEventListener("submit", handleApiKeyCreate);
  }
  if (ideaEditForm) {
    ideaEditForm.addEventListener("submit", handleIdeaEdit);
  }
  if (ideaPromoteForm) {
    ideaPromoteForm.addEventListener("submit", handleIdeaPromote);
  }
  if (ideasArchivedToggle) {
    ideasArchivedToggle.addEventListener("change", handleIdeasArchiveFilter);
  }
  if (ideasQuickAddButton) {
    ideasQuickAddButton.addEventListener("click", handleIdeasQuickAdd);
  }
  if (ideasQuickAddInput) {
    ideasQuickAddInput.addEventListener("keydown", function (event) {
      if (event.key === "Enter") {
        event.preventDefault();
        handleIdeasQuickAdd();
      }
    });
  }
  importForm.addEventListener("submit", handleImportPreview);
  noteForm.addEventListener("submit", handleNote);
  importFile.addEventListener("change", handleImportFile);
  commitImportButton.addEventListener("click", handleImportCommit);
  const themeSelect = document.querySelector("[data-theme-select]");
  if (themeSelect) {
    const savedTheme = localStorage.getItem("flow-theme");
    if (savedTheme) {
      document.documentElement.setAttribute("data-theme", savedTheme);
      themeSelect.value = savedTheme;
    }

    themeSelect.addEventListener("change", function () {
      const theme = themeSelect.value;
      document.documentElement.setAttribute("data-theme", theme);
      localStorage.setItem("flow-theme", theme);
    });
  }
  initializeBoardDnD();
  resetProjectForm();

  function handleClick(event) {
    const revokeApiKeyButton = event.target.closest("[data-revoke-api-key]");
    if (revokeApiKeyButton) {
      revokeApiKey(revokeApiKeyButton.dataset.revokeApiKey);
      return;
    }

    if (event.target.closest("[data-filter-human-required]")) {
      setHumanRequiredFilter(!state.humanRequiredOnly);
      return;
    }

    if (event.target.closest("[data-clear-human-filter]")) {
      setHumanRequiredFilter(false);
      return;
    }

    const card = event.target.closest("[data-task-id]");
    if (card) {
      if (isCardClickSuppressed()) {
        event.preventDefault();
        return;
      }
      openTask(card.dataset.taskId);
      return;
    }

    if (event.target.closest("[data-open-create]")) {
      createDrawer.classList.remove("hidden");
      return;
    }

    if (event.target.closest("[data-close-create]")) {
      createDrawer.classList.add("hidden");
      return;
    }

    if (event.target.closest("[data-open-settings]")) {
      settingsDrawer.classList.remove("hidden");
      return;
    }

    if (event.target.closest("[data-close-settings]")) {
      settingsDrawer.classList.add("hidden");
      return;
    }

    if (event.target.closest("[data-open-projects]")) {
      resetProjectForm();
      settingsDrawer.classList.add("hidden");
      projectsDrawer.classList.remove("hidden");
      return;
    }

    if (event.target.closest("[data-close-projects]")) {
      resetProjectForm();
      projectsDrawer.classList.add("hidden");
      return;
    }

    const editProjectButton = event.target.closest("[data-edit-project]");
    if (editProjectButton) {
      void openProjectForEdit(editProjectButton.dataset.editProject);
      return;
    }

    if (event.target.closest("#project-form-cancel")) {
      resetProjectForm();
      return;
    }

    if (event.target.closest("[data-open-api-keys]")) {
      settingsDrawer.classList.add("hidden");
      openApiKeys();
      return;
    }

    if (event.target.closest("[data-close-api-keys]")) {
      apiKeysDrawer.classList.add("hidden");
      return;
    }

    if (event.target.closest("[data-copy-api-key]")) {
      copyGeneratedApiKey();
      return;
    }

    if (event.target.closest("[data-open-import]")) {
      importDrawer.classList.remove("hidden");
      return;
    }

    if (event.target.closest("[data-close-import]")) {
      importDrawer.classList.add("hidden");
      return;
    }

    if (event.target.closest("[data-open-ideas]")) {
      openIdeas();
      return;
    }

    if (event.target.closest("[data-close-ideas]")) {
      closeIdeas();
      return;
    }

    const ideaCard = event.target.closest("[data-idea-id]");
    if (ideaCard) {
      openIdea(ideaCard.dataset.ideaId);
      return;
    }

    if (event.target.closest("[data-archive-idea]")) {
      archiveCurrentIdea(true);
      return;
    }

    if (event.target.closest("[data-unarchive-idea]")) {
      archiveCurrentIdea(false);
      return;
    }

    if (event.target.closest("[data-edit-idea]")) {
      openIdeaEdit();
      return;
    }

    if (event.target.closest("[data-cancel-edit-idea]")) {
      cancelIdeaEdit();
      return;
    }

    if (event.target.closest("[data-toggle-promote-idea]")) {
      ideaPromoteForm.classList.toggle("hidden");
      return;
    }

    if (event.target.closest("[data-close-detail]")) {
      detailDrawer.classList.add("hidden");
      return;
    }

    if (event.target.closest("[data-claim]")) {
      claimTask();
      return;
    }

    if (event.target.closest("[data-release]")) {
      releaseTask();
      return;
    }

    if (event.target.closest("[data-done]")) {
      completeTask();
    }
  }

  function handleKeydown(event) {
    if (event.key === "Escape" && ideasWorkspace && !ideasWorkspace.classList.contains("hidden")) {
      closeIdeas();
    }
  }

  async function openTask(taskId) {
    try {
      const [task, dependencies, handoffs] = await Promise.all([
        requestJson("/api/tasks/" + encodeURIComponent(taskId)),
        requestJson("/api/tasks/" + encodeURIComponent(taskId) + "/dependencies"),
        requestJson("/api/tasks/" + encodeURIComponent(taskId) + "/handoffs"),
      ]);
      state.currentTask = task;
      state.currentDependencies = dependencies;
      state.currentHandoffs = handoffs || [];
      renderTask(task, dependencies, handoffs);
      detailDrawer.classList.remove("hidden");
    } catch (error) {
      showToast(error.message, "error");
    }
  }

  function renderTask(task, dependencies, handoffs) {
    document.getElementById("detail-id").textContent = task.id;
    document.getElementById("detail-title").textContent = task.title;
    detailForm.elements.title.value = task.title || "";
    detailForm.elements.status.value = task.status || "backlog";
    detailForm.elements.priority.value = task.priority || 0;
    detailForm.elements.project.value = task.project || "";
    detailForm.elements.assignee.value = task.assignee || "";
    if (detailForm.elements.human_required) {
      detailForm.elements.human_required.checked = Boolean(task.human_required);
    }
    detailForm.elements.blocker_reason.value = task.blocker_reason || "";
    detailForm.elements.complexity.value = task.complexity || "small";
    detailForm.elements.impact.value = task.impact || "medium";
    detailForm.elements.effort.value = task.effort || "medium";
    detailForm.elements.risk.value = task.risk || "low";
    detailForm.elements.description.value = task.description || "";
    detailForm.elements.acceptance_criteria.value = task.acceptance_criteria || "";
    renderSource(task);
    renderDependencies(dependencies || state.currentDependencies);
    renderHandoff(task, handoffs || state.currentHandoffs);
    notesNode.innerHTML = renderNotes(task.notes || []);
    jsonNode.textContent = JSON.stringify(task, null, 2);
  }

  function renderSource(task) {
    const parts = [];
    if (task.source_filename) {
      parts.push("File: " + task.source_filename);
    }
    if (task.source_line) {
      parts.push("Line: " + task.source_line);
    }
    if (task.source_title) {
      parts.push("Section: " + task.source_title);
    }
    if (task.import_batch_id) {
      parts.push("Import: " + task.import_batch_id);
    }

    sourceNode.classList.toggle("hidden", parts.length === 0);
    sourceNode.innerHTML = parts.map((part) => '<span class="meta">' + escapeHtml(part) + "</span>").join("<br>");
  }

  function renderDependencies(dependencies) {
    if (!dependenciesNode) {
      return;
    }
    if (!dependencies) {
      dependenciesNode.innerHTML = '<p class="meta">No dependency links.</p>';
      return;
    }

    const groups = [
      ["Parents", dependencies.parent_tasks || [], dependencies.parents || [], "parent_id"],
      ["Children", dependencies.child_tasks || [], dependencies.children || [], "child_id"],
    ];
    const html = groups
      .filter((group) => group[1].length)
      .map(([label, tasks, links, linkTaskKey]) => renderDependencyGroup(label, tasks, links, linkTaskKey))
      .join("");
    dependenciesNode.innerHTML = html || '<p class="meta">No dependency links.</p>';
  }

  function renderDependencyGroup(label, tasks, links, linkTaskKey) {
    const linkByTaskId = (links || []).reduce((index, link) => {
      index[link[linkTaskKey]] = link;
      return index;
    }, {});
    const items = tasks
      .map((task) => {
        const link = linkByTaskId[task.id] || {};
        const meta = [link.link_type, task.status, "P" + task.priority].filter(Boolean).join(" / ");
        return (
          "<li><strong>" +
          escapeHtml(task.title || task.id) +
          '</strong><span class="meta">' +
          escapeHtml(meta) +
          "</span></li>"
        );
      })
      .join("");
    return (
      '<div class="dependency-detail-group"><h4>' +
      escapeHtml(label) +
      '</h4><ul class="dependency-list">' +
      items +
      "</ul></div>"
    );
  }

  function renderHandoff(task, handoffs) {
    if (!handoffNode) {
      return;
    }
    const latest = (handoffs && handoffs[0]) || task.latest_handoff;
    if (!latest) {
      handoffNode.innerHTML = '<p class="meta">No handoff recorded.</p>';
      return;
    }

    const meta = [
      "Outcome " + (latest.outcome || "unknown"),
      "Author " + (latest.author || "unknown"),
      latest.created_at ? "Created " + formatBoardTimestamp(latest.created_at) : "",
    ]
      .filter(Boolean)
      .map((part) => '<span class="meta">' + escapeHtml(part) + "</span>")
      .join("");
    const remaining = latest.remaining_work
      ? '<p class="handoff-remaining"><span>Remaining</span>' + escapeHtml(latest.remaining_work) + "</p>"
      : "";
    handoffNode.innerHTML =
      '<article class="handoff-card"><p>' +
      escapeHtml(latest.summary || "No summary.") +
      '</p><div class="handoff-meta">' +
      meta +
      "</div>" +
      remaining +
      "</article>";
  }

  async function handleDetailSave(event) {
    event.preventDefault();
    if (!state.currentTask) {
      return;
    }

    try {
      const payload = formPayload(detailForm);
      payload.priority = Number(payload.priority || 0);
      payload.assignee = payload.assignee || null;
      if (detailForm.elements.human_required && !detailForm.elements.human_required.disabled) {
        payload.human_required = detailForm.elements.human_required.checked;
      }
      const task = await requestJson("/api/tasks/" + encodeURIComponent(state.currentTask.id), {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      state.currentTask = task;
      showToast("Saved.");
      window.location.reload();
    } catch (error) {
      showToast(error.message, "error");
    }
  }

  async function handleCreate(event) {
    event.preventDefault();
    try {
      const payload = formPayload(createForm);
      payload.priority = Number(payload.priority || 50);
      payload.assignee = payload.assignee || null;
      const task = await requestJson("/api/tasks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      showToast("Created " + task.id + ".");
      window.location.reload();
    } catch (error) {
      showToast(error.message, "error");
    }
  }

  async function openProjectForEdit(projectSlug) {
    if (!projectSlug) {
      return;
    }

    try {
      const project = await requestJson("/api/projects/" + encodeURIComponent(projectSlug));
      state.currentProjectSlug = project.slug;
      projectForm.elements.slug.value = project.slug || "";
      projectForm.elements.slug.readOnly = true;
      projectForm.elements.name.value = project.name || "";
      projectForm.elements.repo_url.value = project.repo_url || "";
      projectForm.elements.repo_path.value = project.repo_path || "";
      projectForm.elements.default_branch.value = project.default_branch || "main";
      projectForm.elements.description.value = project.description || "";
      projectFormTitle.textContent = "Edit project";
      projectSubmitButton.textContent = "Save project";
      projectFormCancel.classList.remove("hidden");
      projectSlugHint.classList.remove("hidden");
      highlightProjectCard(project.slug);
      projectsDrawer.classList.remove("hidden");
    } catch (error) {
      showToast(error.message, "error");
    }
  }

  function resetProjectForm() {
    state.currentProjectSlug = null;
    projectForm.reset();
    projectForm.elements.slug.readOnly = false;
    projectForm.elements.default_branch.value = projectForm.elements.default_branch.value || "main";
    projectFormTitle.textContent = "Create project";
    projectSubmitButton.textContent = "Create project";
    projectFormCancel.classList.add("hidden");
    projectSlugHint.classList.add("hidden");
    highlightProjectCard(null);
  }

  function highlightProjectCard(projectSlug) {
    if (!projectList) {
      return;
    }
    Array.from(projectList.querySelectorAll("[data-project-card]")).forEach((card) => {
      card.classList.toggle("is-active", card.dataset.projectCard === projectSlug);
    });
  }

  async function handleProjectSubmit(event) {
    event.preventDefault();
    try {
      const payload = formPayload(projectForm);
      const isEditing = Boolean(state.currentProjectSlug);
      if (isEditing) {
        delete payload.slug;
      }
      await requestJson(
        isEditing
          ? "/api/projects/" + encodeURIComponent(state.currentProjectSlug)
          : "/api/projects",
        {
          method: isEditing ? "PATCH" : "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        }
      );
      showToast(isEditing ? "Project updated." : "Project created.");
      window.location.reload();
    } catch (error) {
      showToast(error.message, "error");
    }
  }

  async function openApiKeys() {
    if (!apiKeysDrawer) {
      showToast("API key panel is not available on this page.", "error");
      return;
    }
    apiKeysDrawer.classList.remove("hidden");
    await loadApiKeys();
  }

  async function loadApiKeys() {
    if (!apiKeyList) {
      return;
    }
    try {
      state.apiKeys = await requestJson("/api/api-keys");
      renderApiKeys(state.apiKeys);
    } catch (error) {
      showToast(error.message, "error");
    }
  }

  async function handleApiKeyCreate(event) {
    event.preventDefault();
    try {
      const payload = formPayload(apiKeyForm);
      const response = await requestJson("/api/api-keys", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      apiKeyForm.reset();
      showGeneratedApiKey(response.api_key);
      showToast("API key generated.");
      await loadApiKeys();
    } catch (error) {
      showToast(error.message, "error");
    }
  }

  async function openIdeas() {
    ideasWorkspace.classList.remove("hidden");
    await loadIdeas();
    if (ideasQuickAddInput) {
      ideasQuickAddInput.focus();
    }
  }

  function closeIdeas() {
    if (ideasWorkspace) {
      ideasWorkspace.classList.add("hidden");
    }
  }

  async function loadIdeas() {
    if (!ideasList) {
      return;
    }
    try {
      const selectedProject = getSelectedProject();
      const params = new URLSearchParams();
      if (state.showArchivedIdeas) {
        params.set("archived", "true");
      }
      if (selectedProject) {
        params.set("project", selectedProject);
      }
      updateIdeasWorkspaceTitle(selectedProject);
      const query = params.toString() ? "?" + params.toString() : "";
      const response = await requestJson("/api/ideas" + query);
      state.ideas = Array.isArray(response) ? response : response.items;
      renderIdeas(state.ideas);
      if (state.currentIdea) {
        const current = state.ideas.find((idea) => idea.id === state.currentIdea.id);
        if (current) {
          state.currentIdea = current;
          renderIdeaDetail(current);
        } else {
          clearIdeaDetail();
        }
      }
    } catch (error) {
      showToast(error.message, "error");
    }
  }

  function getSelectedProject() {
    return projectSelect ? projectSelect.value.trim() : "";
  }

  function updateIdeasWorkspaceTitle(projectSlug) {
    if (!ideasWorkspaceTitle) {
      return;
    }
    ideasWorkspaceTitle.textContent = projectSlug ? "Ideas for " + projectSlug : "Ideas across projects";
  }

  function renderIdeas(ideas) {
    if (!ideasList) {
      return;
    }
    if (!ideas.length) {
      ideasList.innerHTML = '<p class="meta">No ideas found.</p>';
      return;
    }

    const byProject = ideas.reduce((groups, idea) => {
      const project = idea.project || "Unassigned";
      groups[project] = groups[project] || [];
      groups[project].push(idea);
      return groups;
    }, {});

    ideasList.innerHTML = Object.keys(byProject)
      .sort()
      .map((project) => {
        const cards = byProject[project].map(renderIdeaCard).join("");
        return (
          '<section class="idea-project-group"><div class="section-heading"><h3>' +
          escapeHtml(project) +
          '</h3><span class="count">' +
          byProject[project].length +
          "</span></div>" +
          cards +
          "</section>"
        );
      })
      .join("");
  }

  function renderIdeaCard(idea) {
    const promotedIds = idea.promoted_task_ids || [];
    const promoted = promotedIds.length
      ? '<span class="badge badge-promoted">Promoted ' + promotedIds.map(escapeHtml).join(", ") + "</span>"
      : "";
    const archived = idea.archived_at ? '<span class="badge">archived</span>' : "";
    return (
      '<button class="idea-card" type="button" data-idea-id="' +
      escapeHtml(idea.id) +
      '"><span class="idea-card__top"><strong>' +
      escapeHtml(idea.title) +
      "</strong>" +
      promoted +
      archived +
      '</span><span class="idea-preview">' +
      escapeHtml(previewText(idea.description)) +
      '</span><span class="meta">' +
      escapeHtml("author " + (idea.author || "unknown")) +
      '</span><span class="meta">' +
      escapeHtml(idea.project || "") +
      '</span><span class="updated">' +
      escapeHtml(formatBoardTimestamp(idea.created_at)) +
      "</span></button>"
    );
  }

  async function openIdea(ideaId) {
    try {
      const idea = await requestJson("/api/ideas/" + encodeURIComponent(ideaId));
      state.currentIdea = idea;
      renderIdeaDetail(idea);
    } catch (error) {
      showToast(error.message, "error");
    }
  }

  function renderIdeaDetail(idea) {
    const promotedIds = idea.promoted_task_ids || [];
    ideaDetail.classList.remove("hidden");
    ideaDetailTitle.textContent = idea.title || "Idea";
    ideaDetailStatus.classList.toggle("hidden", !idea.archived_at);
    ideaDetailMeta.innerHTML = [
      "ID " + idea.id,
      "Project " + idea.project,
      "Author " + (idea.author || "unknown"),
      "Created " + formatBoardTimestamp(idea.created_at),
      "Updated " + formatBoardTimestamp(idea.updated_at),
      idea.archived_at ? "Archived " + formatBoardTimestamp(idea.archived_at) : "",
      promotedIds.length ? "Promoted " + promotedIds.join(", ") : "",
    ]
      .filter(Boolean)
      .map((part) => '<span class="meta">' + escapeHtml(part) + "</span>")
      .join("");
    ideaDetailDescription.textContent = idea.description || "No description.";
    document.querySelector("[data-archive-idea]").classList.toggle("hidden", Boolean(idea.archived_at));
    document.querySelector("[data-unarchive-idea]").classList.toggle("hidden", !idea.archived_at);
    if (ideaEditForm) {
      ideaEditForm.classList.add("hidden");
    }
    ideaPromoteForm.classList.add("hidden");
    ideaPromoteForm.reset();
  }

  function clearIdeaDetail() {
    state.currentIdea = null;
    if (ideaDetail) {
      ideaDetail.classList.add("hidden");
    }
    if (ideaEditForm) {
      ideaEditForm.classList.add("hidden");
    }
  }

  function openIdeaEdit() {
    if (!state.currentIdea || !ideaEditForm) {
      return;
    }
    const idea = state.ideas.find((item) => item.id === state.currentIdea.id) || state.currentIdea;
    ideaEditForm.elements.title.value = idea.title || "";
    ideaEditForm.elements.description.value = idea.description || "";
    ideaEditForm.elements.project.value = idea.project || "";
    ideaEditForm.elements.author.value = idea.author || "";
    if (ideaPromoteForm) {
      ideaPromoteForm.classList.add("hidden");
    }
    ideaDetail.classList.add("hidden");
    ideaEditForm.classList.remove("hidden");
  }

  function cancelIdeaEdit() {
    if (ideaEditForm) {
      ideaEditForm.classList.add("hidden");
    }
    if (ideaDetail && state.currentIdea) {
      ideaDetail.classList.remove("hidden");
    }
  }

  async function handleIdeaEdit(event) {
    event.preventDefault();
    if (!state.currentIdea || !ideaEditForm) {
      return;
    }
    const ideaId = state.currentIdea.id;
    const payload = {
      title: ideaEditForm.elements.title.value.trim(),
      description: ideaEditForm.elements.description.value.trim(),
      project: ideaEditForm.elements.project.value.trim() || null,
      author: ideaEditForm.elements.author.value.trim() || null,
    };
    try {
      const idea = await requestJson("/api/ideas/" + encodeURIComponent(ideaId), {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const index = state.ideas.findIndex((item) => item.id === ideaId);
      if (index !== -1) {
        state.ideas[index] = idea;
      }
      state.currentIdea = idea;
      renderIdeas(state.ideas);
      renderIdeaDetail(idea);
      cancelIdeaEdit();
    } catch (error) {
      showToast("Failed to update idea: " + error.message, "error");
    }
  }

  async function handleIdeasQuickAdd() {
    if (!ideasQuickAddInput) {
      return;
    }
    const title = ideasQuickAddInput.value.trim();
    if (!title) {
      ideasQuickAddInput.focus();
      return;
    }
    try {
      const payload = { title: title };
      const selectedProject = getSelectedProject();
      if (selectedProject) {
        payload.project = selectedProject;
      }
      const idea = await requestJson("/api/ideas", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      ideasQuickAddInput.value = "";
      state.currentIdea = idea;
      state.showArchivedIdeas = false;
      if (ideasArchivedToggle) {
        ideasArchivedToggle.checked = false;
      }
      showToast("Created " + idea.id + ".");
      await loadIdeas();
      renderIdeaDetail(idea);
    } catch (error) {
      showToast(error.message, "error");
    }
  }

  async function handleIdeasArchiveFilter(event) {
    state.showArchivedIdeas = Boolean(event.target.checked);
    clearIdeaDetail();
    await loadIdeas();
  }

  async function archiveCurrentIdea(archive) {
    if (!state.currentIdea) {
      return;
    }
    try {
      const suffix = archive ? "/archive" : "/unarchive";
      const idea = await requestJson("/api/ideas/" + encodeURIComponent(state.currentIdea.id) + suffix, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      state.currentIdea = idea;
      showToast(archive ? "Idea archived." : "Idea unarchived.");
      await loadIdeas();
      if (state.showArchivedIdeas === Boolean(idea.archived_at)) {
        renderIdeaDetail(idea);
      }
    } catch (error) {
      showToast(error.message, "error");
    }
  }

  async function handleIdeaPromote(event) {
    event.preventDefault();
    if (!state.currentIdea) {
      return;
    }
    const specs = ideaPromoteForm.elements.titles.value
      .split(/\r?\n/)
      .map((title) => title.trim())
      .filter(Boolean)
      .map((title) => ({ title }));
    if (!specs.length) {
      showToast("Enter at least one task title.", "error");
      return;
    }
    try {
      const idea = await requestJson("/api/ideas/" + encodeURIComponent(state.currentIdea.id) + "/promote", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(specs),
      });
      state.currentIdea = idea;
      showToast("Promoted " + idea.promoted_task_ids.length + " task(s).");
      await loadIdeas();
      renderIdeaDetail(idea);
    } catch (error) {
      showToast(error.message, "error");
    }
  }

  async function revokeApiKey(apiKeyId) {
    if (!apiKeyId || !window.confirm("Revoke this API key?")) {
      return;
    }
    try {
      await requestJson("/api/api-keys/" + encodeURIComponent(apiKeyId) + "/revoke", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      showToast("API key revoked.");
      await loadApiKeys();
    } catch (error) {
      showToast(error.message, "error");
    }
  }

  function showGeneratedApiKey(apiKey) {
    if (!generatedApiKey || !generatedApiKeyValue) {
      return;
    }
    generatedApiKeyValue.value = apiKey || "";
    generatedApiKey.classList.toggle("hidden", !apiKey);
  }

  async function copyGeneratedApiKey() {
    if (!generatedApiKeyValue || !generatedApiKeyValue.value) {
      return;
    }
    try {
      await navigator.clipboard.writeText(generatedApiKeyValue.value);
      showToast("Copied.");
    } catch (_error) {
      generatedApiKeyValue.select();
      generatedApiKeyValue.setSelectionRange(0, generatedApiKeyValue.value.length);
      showToast("Select the key and press Ctrl+C to copy.");
    }
  }

  function renderApiKeys(apiKeys) {
    if (!apiKeyList) {
      return;
    }
    if (!apiKeys.length) {
      apiKeyList.innerHTML = '<p class="meta">No API keys yet.</p>';
      return;
    }
    apiKeyList.innerHTML = apiKeys
      .map((apiKey) => {
        const revoked = Boolean(apiKey.revoked_at);
        const revokeControl = revoked
          ? '<span class="badge">revoked</span>'
          : '<button class="button button-ghost" type="button" data-revoke-api-key="' +
            escapeHtml(apiKey.id) +
            '">Revoke</button>';
        return (
          '<article class="api-key-card' +
          (revoked ? " is-revoked" : "") +
          '">' +
          '<div class="api-key-card__top"><div><strong>' +
          escapeHtml(apiKey.name) +
          '</strong><span class="badge badge-role-' + escapeHtml(apiKey.role) + '">' +
          escapeHtml(formatRole(apiKey.role)) +
          '</span><span class="meta">' +
          escapeHtml(apiKey.key_prefix) +
          "...</span></div>" +
          revokeControl +
          "</div>" +
          (apiKey.description ? "<p>" + escapeHtml(apiKey.description) + "</p>" : "") +
          '<span class="meta">Created ' +
          escapeHtml(formatBoardTimestamp(apiKey.created_at)) +
          "</span></article>"
        );
      })
      .join("");
  }

  async function handleImportFile(event) {
    const file = event.target.files && event.target.files[0];
    if (!file) {
      return;
    }
    importForm.elements.markdown.value = await file.text();
    importForm.elements.source_filename.value = file.name;
  }

  async function handleImportPreview(event) {
    event.preventDefault();
    try {
      const payload = formPayload(importForm);
      payload.default_priority = Number(payload.default_priority || 50);
      const response = await requestJson("/api/import/markdown/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      state.importItems = response.items || [];
      renderImportPreview(state.importItems);
    } catch (error) {
      showToast(error.message, "error");
    }
  }

  async function handleImportCommit() {
    const selected = Array.from(importPreview.querySelectorAll("[data-import-select]:checked"))
      .map((input) => state.importItems[Number(input.value)])
      .filter(Boolean);
    if (!selected.length) {
      showToast("Select at least one task to import.", "error");
      return;
    }

    try {
      const response = await requestJson("/api/import/markdown/commit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items: selected }),
      });
      showToast("Imported " + (response.created || []).length + " task(s).");
      window.location.reload();
    } catch (error) {
      showToast(error.message, "error");
    }
  }

  function renderImportPreview(items) {
    importPreview.classList.remove("hidden");
    commitImportButton.classList.toggle("hidden", !items.length);

    if (!items.length) {
      importPreview.innerHTML = '<p class="meta">No checklist tasks were found.</p>';
      return;
    }

    importPreview.innerHTML = items
      .map((item, index) => {
        const disabled = item.duplicate ? " disabled" : "";
        const checked = item.duplicate ? "" : " checked";
        const duplicate = item.duplicate
          ? '<span class="badge badge-warn">duplicate ' + escapeHtml(item.duplicate_task_id || "") + "</span>"
          : "";
        return (
          '<article class="import-item' +
          (item.duplicate ? " is-duplicate" : "") +
          '">' +
          '<div class="import-item__top">' +
          '<input type="checkbox" data-import-select value="' +
          index +
          '"' +
          checked +
          disabled +
          ">" +
          "<div><strong>" +
          escapeHtml(item.title) +
          "</strong><div class=\"meta\">" +
          escapeHtml(item.project + " / " + item.status + " / P" + item.priority) +
          "</div></div>" +
          duplicate +
          "</div>" +
          '<div class="meta">' +
          escapeHtml([item.source_filename, item.source_line ? "line " + item.source_line : "", item.source_title].filter(Boolean).join(" / ")) +
          "</div>" +
          (item.description ? "<p>" + escapeHtml(item.description) + "</p>" : "") +
          "</article>"
        );
      })
      .join("");
  }

  async function claimTask() {
    if (!state.currentTask) {
      return;
    }
    const agentName = window.prompt("Agent name");
    if (!agentName) {
      return;
    }
    await mutateCurrent("/claim", { agent_name: agentName });
  }

  async function releaseTask() {
    await mutateCurrent("/release", {});
  }

  async function completeTask() {
    if (!state.currentTask) {
      return;
    }
    const summary = window.prompt("Completion summary");
    if (!summary) {
      return;
    }
    await mutateCurrent("/done", { summary });
  }

  async function handleNote(event) {
    event.preventDefault();
    if (!state.currentTask) {
      return;
    }
    try {
      const task = await requestJson("/api/tasks/" + encodeURIComponent(state.currentTask.id) + "/note", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ note: noteForm.elements.note.value }),
      });
      noteForm.reset();
      state.currentTask = task;
      renderTask(task, state.currentDependencies);
    } catch (error) {
      showToast(error.message, "error");
    }
  }

  async function mutateCurrent(suffix, payload) {
    if (!state.currentTask) {
      return;
    }
    try {
      const task = await requestJson("/api/tasks/" + encodeURIComponent(state.currentTask.id) + suffix, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload || {}),
      });
      state.currentTask = task;
      renderTask(task, state.currentDependencies);
      showToast("Updated " + task.id + ".");
      window.location.reload();
    } catch (error) {
      showToast(error.message, "error");
    }
  }

  function formPayload(form) {
    const data = new FormData(form);
    const payload = {};
    data.forEach((value, key) => {
      payload[key] = typeof value === "string" ? value.trim() : value;
    });
    return payload;
  }

  function setHumanRequiredFilter(enabled) {
    state.humanRequiredOnly = Boolean(enabled);
    document.body.classList.toggle("is-human-filtered", state.humanRequiredOnly);
    if (humanFilterButton) {
      humanFilterButton.classList.toggle("is-active", state.humanRequiredOnly);
      humanFilterButton.setAttribute("aria-pressed", state.humanRequiredOnly ? "true" : "false");
    }
    if (clearHumanFilterButton) {
      clearHumanFilterButton.classList.toggle("hidden", !state.humanRequiredOnly);
    }
    updateColumnCounts();
  }

  function updateColumnCounts() {
    columnNodes.forEach((column) => {
      const countNode = column.querySelector("[data-column-count]");
      if (!countNode) {
        return;
      }
      const cards = Array.from(column.querySelectorAll(".task-card[data-task-id]"));
      const visibleCount = cards.filter((card) => {
        return !state.humanRequiredOnly || card.dataset.humanRequired === "true";
      }).length;
      countNode.textContent = String(visibleCount);
    });
  }

  function renderNotes(notes) {
    if (!notes.length) {
      return '<p class="meta">No notes yet.</p>';
    }
    return notes
      .map((note) => {
        const author = note.author || "unknown";
        const created = note.created_at || "";
        return (
          '<article class="note"><span class="meta">' +
          escapeHtml(author + " / " + created) +
          "</span><p>" +
          escapeHtml(note.body || "") +
          "</p></article>"
        );
      })
      .join("");
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
      const message =
        (data && (data.detail || data.error)) || "Request failed with HTTP " + response.status + ".";
      throw new Error(Array.isArray(message) ? "Validation failed." : String(message));
    }
    return data;
  }

  function getApiBaseUrl() {
    const fromWindow = (window.Flow && window.Flow.apiBaseUrl) || window.FLOW_API_BASE_URL || "";
    const meta = document.querySelector('meta[name="flow-api-base"]');
    const fromMeta = meta ? meta.getAttribute("content") || "" : "";
    return String(fromWindow || fromMeta || "").replace(/\/+$/, "");
  }

  function resolveApiUrl(url) {
    if (!apiBaseUrl || /^https?:\/\//i.test(url)) {
      return url;
    }
    return apiBaseUrl + (url.startsWith("/") ? url : "/" + url);
  }

  function showToast(message, tone) {
    const toast = document.createElement("div");
    toast.className = "toast" + (tone === "error" ? " toast-error" : "");
    toast.textContent = message || "Done.";
    toastStack.appendChild(toast);
    window.setTimeout(() => toast.remove(), 3000);
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function initializeBoardDnD() {
    if (!boardNode || !cardLists.length || !window.PointerEvent) {
      return;
    }

    boardNode.addEventListener("pointerdown", handleBoardPointerDown);
    document.addEventListener("pointermove", handleBoardPointerMove);
    document.addEventListener("pointerup", handleBoardPointerUp);
    document.addEventListener("pointercancel", handleBoardPointerCancel);
    document.addEventListener("dragstart", preventNativeDrag);
  }

  function handleBoardPointerDown(event) {
    if (event.button !== 0) {
      return;
    }

    const card = event.target.closest(".task-card[data-task-id]");
    if (!card || !boardNode.contains(card)) {
      return;
    }

    const rect = card.getBoundingClientRect();
    state.dragState = {
      active: false,
      card,
      ghost: null,
      offsetX: event.clientX - rect.left,
      offsetY: event.clientY - rect.top,
      pointerId: event.pointerId,
      sourceStatus: getColumnName(card),
      startX: event.clientX,
      startY: event.clientY,
      targetList: null,
      taskId: card.dataset.taskId,
    };

    try {
      card.setPointerCapture(event.pointerId);
    } catch (_error) {
      // Pointer capture is best-effort here.
    }
  }

  function handleBoardPointerMove(event) {
    const dragState = state.dragState;
    if (!dragState || event.pointerId !== dragState.pointerId) {
      return;
    }

    if (!dragState.active) {
      const deltaX = event.clientX - dragState.startX;
      const deltaY = event.clientY - dragState.startY;
      if (Math.hypot(deltaX, deltaY) < DRAG_THRESHOLD_PX) {
        return;
      }
      startPointerDrag(dragState);
    }

    event.preventDefault();
    positionDragGhost(dragState, event.clientX, event.clientY);
    updateDropTarget(findDropZoneAtPoint(event.clientX, event.clientY), dragState);
  }

  function handleBoardPointerUp(event) {
    const dragState = state.dragState;
    if (!dragState || event.pointerId !== dragState.pointerId) {
      return;
    }

    if (!dragState.active) {
      releasePointerCapture(dragState);
      state.dragState = null;
      return;
    }

    event.preventDefault();
    const targetList = findDropZoneAtPoint(event.clientX, event.clientY) || dragState.targetList;
    void completePointerDrag(dragState, targetList);
  }

  function handleBoardPointerCancel(event) {
    const dragState = state.dragState;
    if (!dragState || event.pointerId !== dragState.pointerId) {
      return;
    }

    cleanupPointerDrag(dragState, true);
  }

  function preventNativeDrag(event) {
    if (!state.dragState) {
      return;
    }

    const card = event.target.closest(".task-card[data-task-id]");
    if (card) {
      event.preventDefault();
    }
  }

  function startPointerDrag(dragState) {
    const rect = dragState.card.getBoundingClientRect();
    const ghost = dragState.card.cloneNode(true);

    ghost.disabled = true;
    ghost.setAttribute("aria-hidden", "true");
    ghost.classList.add("task-card-ghost", "is-dragging");
    ghost.style.width = rect.width + "px";
    ghost.style.height = rect.height + "px";
    document.body.appendChild(ghost);

    dragState.active = true;
    dragState.ghost = ghost;
    dragState.card.classList.add("is-dragging");
    suppressCardClick();
    positionDragGhost(dragState, dragState.startX, dragState.startY);
  }

  function positionDragGhost(dragState, clientX, clientY) {
    if (!dragState.ghost) {
      return;
    }

    dragState.ghost.style.left = clientX - dragState.offsetX + "px";
    dragState.ghost.style.top = clientY - dragState.offsetY + "px";
  }

  function updateDropTarget(list, dragState) {
    if (!canDropInto(list, dragState)) {
      dragState.targetList = null;
      clearDropTargets();
      return;
    }

    dragState.targetList = list;
    setDropTarget(list);
  }

  async function completePointerDrag(dragState, targetList) {
    const card = dragState.card;
    const targetStatus = getColumnName(targetList);
    const sourceStatus = dragState.sourceStatus;

    if (!targetList || !targetStatus || !sourceStatus || targetStatus === sourceStatus) {
      cleanupPointerDrag(dragState, true);
      return;
    }

    card.classList.add("is-moving");

    try {
      const task = await requestJson("/api/tasks/" + encodeURIComponent(dragState.taskId) + "/move", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: targetStatus }),
      });
      syncTaskCard(card, task);
      insertCardByPriority(card, targetList);
      updateColumnCounts();
      if (state.currentTask && state.currentTask.id === task.id) {
        state.currentTask = task;
        renderTask(task);
      }
      showToast("Moved " + task.id + " to " + formatStatus(targetStatus) + ".");
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      card.classList.remove("is-moving");
      cleanupPointerDrag(dragState, true);
    }
  }

  function canDropInto(list, dragState) {
    if (!list || !dragState || !dragState.card) {
      return false;
    }
    return getColumnName(list) !== dragState.sourceStatus;
  }

  function getColumnName(node) {
    if (!node) {
      return "";
    }
    if (node.dataset && node.dataset.column) {
      return node.dataset.column;
    }
    const column = node.closest("[data-column]");
    return column ? column.dataset.column || "" : "";
  }

  function setDropTarget(list) {
    clearDropTargets();
    list.classList.add("is-drop-target");
  }

  function clearDropTargets() {
    cardLists.forEach((list) => list.classList.remove("is-drop-target"));
  }

  function cleanupPointerDrag(dragState, suppressClick) {
    if (state.dragState !== dragState) {
      return;
    }

    releasePointerCapture(dragState);
    clearDropTargets();
    dragState.card.classList.remove("is-dragging");
    if (dragState.ghost) {
      dragState.ghost.remove();
    }

    state.dragState = null;
    if (suppressClick) {
      suppressCardClick();
    }
  }

  function releasePointerCapture(dragState) {
    const card = dragState.card;
    if (!card || !card.hasPointerCapture || !card.hasPointerCapture(dragState.pointerId)) {
      return;
    }

    try {
      card.releasePointerCapture(dragState.pointerId);
    } catch (_error) {
      // Ignore capture release failures during cleanup.
    }
  }

  function findDropZoneAtPoint(clientX, clientY) {
    const target = document.elementFromPoint(clientX, clientY);
    return target ? target.closest("[data-drop-zone]") : null;
  }

  function insertCardByPriority(card, list) {
    const taskPriority = Number(card.dataset.taskPriority || 0);
    const peers = Array.from(list.querySelectorAll(".task-card")).filter((peer) => peer !== card);
    const before = peers.find((peer) => Number(peer.dataset.taskPriority || 0) < taskPriority);
    if (before) {
      list.insertBefore(card, before);
      return;
    }
    list.appendChild(card);
  }

  function syncTaskCard(card, task) {
    card.dataset.taskPriority = String(task.priority || 0);

    const priorityNode = card.querySelector(".priority");
    if (priorityNode) {
      priorityNode.textContent = "P" + task.priority;
    }

    const titleNode = card.querySelector("strong");
    if (titleNode) {
      titleNode.textContent = task.title || "";
    }

    const metaNodes = card.querySelectorAll(".meta");
    if (metaNodes[0]) {
      metaNodes[0].textContent = task.project || "";
    }
    if (metaNodes[1]) {
      metaNodes[1].textContent = task.assignee || "unclaimed";
    }

    const updatedNode = card.querySelector(".updated");
    if (updatedNode) {
      updatedNode.textContent = formatBoardTimestamp(task.updated_at);
    }
  }



  function suppressCardClick() {
    state.suppressClickUntil = Date.now() + 250;
  }

  function isCardClickSuppressed() {
    return state.suppressClickUntil > Date.now();
  }

  function formatRole(role) {
    var labels = {
      admin: "Admin",
      architect: "Architect",
      implementer: "Implementer",
      reviewer: "Reviewer",
      read_only: "Read only",
    };
    return labels[role] || String(role || "");
  }

  function formatStatus(status) {
    return String(status || "")
      .replace(/_/g, " ")
      .replace(/\b\w/g, (match) => match.toUpperCase());
  }

  function previewText(value) {
    const text = String(value || "").replace(/\s+/g, " ").trim();
    if (!text) {
      return "No description.";
    }
    return text.length > 140 ? text.slice(0, 137) + "..." : text;
  }

  function formatBoardTimestamp(value) {
    const date = value ? new Date(value) : null;
    if (!date || Number.isNaN(date.getTime())) {
      return "";
    }
    return (
      date.getUTCFullYear() +
      "-" +
      String(date.getUTCMonth() + 1).padStart(2, "0") +
      "-" +
      String(date.getUTCDate()).padStart(2, "0") +
      " " +
      String(date.getUTCHours()).padStart(2, "0") +
      ":" +
      String(date.getUTCMinutes()).padStart(2, "0")
    );
  }
})();
