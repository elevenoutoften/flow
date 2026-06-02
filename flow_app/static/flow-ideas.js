(function () {
  const config = window.FlowIdeas || {};
  const originalCardMarkup = new Map();
  const wall = document.getElementById("ideaWall");
  const toastStack = document.getElementById("toast-stack");
  const searchParams = new URLSearchParams(window.location.search);
  const hoverTip = document.createElement("div");
  let tipTimer = null;

  if (searchParams.get("embedded") === "1") document.body.classList.add("is-embedded");
  hoverTip.id = "hover-tip";
  document.body.appendChild(hoverTip);

  document.addEventListener("click", function (event) {
    const editorButton = event.target.closest("[data-editor-action]");
    if (editorButton) {
      handleEditorAction(editorButton);
      return;
    }

    const openEditor = document.querySelector(".idea-card.is-editing");
    if (openEditor) {
      if (openEditor.contains(event.target)) return;
      closeFlowDropdowns();
      restoreCard(openEditor);
      return;
    }

    if (event.target.closest("button,input,textarea,[data-flow-select],label")) return;
    const card = event.target.closest(".idea-card");
    if (!card) return;
    openIdeaEditor(card);
  });

  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") return;
    const openEditor = document.querySelector(".idea-card.is-editing");
    if (openEditor) restoreCard(openEditor);
    else closeIdeasSurface();
  });

  setupAuthors(document);
  initFlowDropdowns(document);
  if (searchParams.get("new") === "1") {
    const newIdeaCard = document.querySelector(".idea-card.is-template");
    if (newIdeaCard) window.setTimeout(() => openIdeaEditor(newIdeaCard), 0);
  }

  window.closeIdeasSurface = closeIdeasSurface;

  function openIdeaEditor(card) {
    if (document.querySelector(".idea-card.is-editing")) return;
    if (!config.canCreate && card.classList.contains("is-template")) {
      showToast("Authentication is required.");
      return;
    }
    if (!config.canEdit && !card.classList.contains("is-template")) {
      showToast("Editing ideas requires permission.");
      return;
    }

    if (!originalCardMarkup.has(card)) originalCardMarkup.set(card, card.innerHTML);
    const isNew = card.classList.contains("is-template");
    const title = isNew ? "" : card.querySelector(".idea-title")?.textContent.trim() || "";
    const rawDescription = isNew ? "" : card.querySelector(".idea-preview")?.textContent.trim() || "";
    const description = rawDescription === "No description." ? "" : rawDescription;
    const project = isNew
      ? config.selectedProject || firstProject()
      : card.dataset.project || card.querySelector(".project-name")?.textContent.trim() || firstProject();
    const projectOptions = getProjectOptions(project);
    const selectedProject = projectOptions.find((option) => option.value === project) || projectOptions[0];

    card.classList.add("is-editing");
    card.innerHTML =
      '<div class="idea-editor-panel"><form class="idea-editor" aria-label="' +
      (isNew ? "Add new idea" : "Edit idea") +
      '">' +
      '<label class="editor-label">Title<input class="editor-input" name="title" value="' +
      escapeAttribute(title) +
      '" placeholder="Idea title" required></label>' +
      '<label class="editor-label">Description<textarea class="editor-textarea" name="description" placeholder="What should be remembered or explored?">' +
      escapeHTML(description) +
      "</textarea></label>" +
      '<label class="editor-label">Project' +
      projectDropdownMarkup(projectOptions, selectedProject) +
      "</label>" +
      '<div class="editor-actions">' +
      '<button class="btn btn-ghost" type="button" data-editor-action="cancel">Cancel</button>' +
      (isNew ? "" : '<button class="btn btn-danger" type="button" data-editor-action="archive">Archive</button>') +
      '<button class="btn btn-primary" type="button" data-editor-action="save">Save</button>' +
      "</div></form></div>";
    initFlowDropdowns(card);
    if (window.FlowScroll) window.FlowScroll.initAll(card);
    setEditorAlignment(card);
    card.querySelector('[name="title"]')?.focus();
  }

  function handleEditorAction(button) {
    const card = button.closest(".idea-card");
    const action = button.dataset.editorAction;
    if (action === "cancel") {
      restoreCard(card);
      return;
    }
    if (action === "archive") {
      archiveIdea(card);
      return;
    }
    if (action === "save") saveIdea(card);
  }

  async function saveIdea(card) {
    const form = card.querySelector(".idea-editor");
    const titleInput = form.elements.title;
    const title = titleInput.value.trim();
    const description = form.elements.description.value.trim();
    const project = form.elements.project.value.trim() || "unassigned";
    if (!title) {
      titleInput.focus();
      showToast("Title is required");
      return;
    }

    const isNew = card.classList.contains("is-template");
    try {
      const idea = await requestJson(isNew ? "/api/ideas" : "/api/ideas/" + encodeURIComponent(card.dataset.id), {
        method: isNew ? "POST" : "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title, description, project }),
      });
      if (isNew) {
        const newCard = createIdeaCard(idea);
        card.insertAdjacentElement("afterend", newCard);
        restoreCard(card);
        setupAuthors(newCard);
      } else {
        card.dataset.project = idea.project || "unassigned";
        card.dataset.id = idea.id;
        card.innerHTML = cardContent(idea);
        card.classList.remove("is-editing", "editor-align-left", "editor-align-right", "editor-align-up");
        originalCardMarkup.delete(card);
        setupAuthors(card);
      }
      showToast("Saved: " + title);
      notifyParent();
    } catch (error) {
      showToast(error.message);
    }
  }

  async function archiveIdea(target) {
    const card = target.closest ? target.closest(".idea-card") : target;
    if (!card || card.classList.contains("is-template")) return;
    const title = card.querySelector(".idea-title")?.textContent.trim() || card.querySelector('[name="title"]')?.value.trim() || "Untitled idea";
    try {
      await requestJson("/api/ideas/" + encodeURIComponent(card.dataset.id) + "/archive", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      card.classList.add("is-archived");
      window.setTimeout(() => card.remove(), 230);
      showToast("Archived: " + title);
      notifyParent();
    } catch (error) {
      showToast(error.message);
    }
  }

  function createIdeaCard(idea) {
    const card = document.createElement("article");
    const titleLength = String(idea.title || "").length;
    card.className = "idea-card" + (titleLength > 80 ? " wide" : titleLength < 28 ? " narrow" : "");
    card.dataset.project = idea.project || "unassigned";
    card.dataset.id = idea.id;
    card.dataset.author = idea.author || "unknown";
    card.innerHTML = cardContent(idea);
    return card;
  }

  function cardContent(idea) {
    return (
      '<div class="project-name">' +
      escapeHTML(idea.project || "unassigned") +
      '</div><h2 class="idea-title">' +
      escapeHTML(idea.title || "Untitled idea") +
      '</h2><p class="idea-preview">' +
      escapeHTML(idea.description || "No description.") +
      '</p><div class="idea-footer"><span class="author">' +
      escapeHTML(idea.author || "unknown") +
      '</span><span class="date">' +
      escapeHTML(formatDate(idea.created_at)) +
      "</span></div>"
    );
  }

  function restoreCard(card) {
    const markup = originalCardMarkup.get(card);
    if (!markup) return;
    card.innerHTML = markup;
    card.classList.remove("is-editing", "editor-align-left", "editor-align-right", "editor-align-up");
    originalCardMarkup.delete(card);
    setupAuthors(card);
  }

  function setupAuthors(scope) {
    scope.querySelectorAll(".idea-card").forEach((card) => {
      const date = card.querySelector(".date")?.textContent.trim();
      if (date && date !== "NEW DRAFT") card.dataset.date = date;
    });
    scope.querySelectorAll(".author").forEach((author) => {
      const fullName = author.getAttribute("aria-label") || author.title || author.textContent.trim();
      const card = author.closest(".idea-card");
      if (card) card.dataset.author = fullName;
      author.dataset.tip = fullName;
      author.removeAttribute("title");
      author.setAttribute("aria-label", fullName);
      author.textContent = fullName.charAt(0).toUpperCase() || "?";
    });
    bindTooltips(scope);
  }

  function showTip() {
    const text = this.dataset.tip;
    if (!text) return;
    window.clearTimeout(tipTimer);
    tipTimer = window.setTimeout(() => {
      hoverTip.textContent = text;
      hoverTip.style.opacity = "1";
      const rect = this.getBoundingClientRect();
      hoverTip.style.left = rect.left + rect.width / 2 + "px";
      hoverTip.style.bottom = window.innerHeight - rect.top + 8 + "px";
    }, 450);
  }

  function hideTip() {
    window.clearTimeout(tipTimer);
    hoverTip.style.opacity = "0";
  }

  function bindTooltips(scope) {
    (scope || document).querySelectorAll("[data-tip]").forEach((element) => {
      element.removeEventListener("mouseenter", showTip);
      element.removeEventListener("mouseleave", hideTip);
      element.addEventListener("mouseenter", showTip);
      element.addEventListener("mouseleave", hideTip);
    });
  }

  function getProjectOptions(selectedProject) {
    const options = (config.projects || []).slice();
    if (selectedProject && !options.find((option) => option.value === selectedProject)) {
      options.unshift({ value: selectedProject, label: selectedProject });
    }
    if (!options.length) options.push({ value: "default", label: "default" });
    return options;
  }

  function firstProject() {
    return (config.projects && config.projects[0] && config.projects[0].value) || "default";
  }

  function projectDropdownMarkup(options, selected) {
    return (
      '<div class="flow-select" data-flow-select><button class="flow-select-trigger" type="button" aria-haspopup="listbox" aria-expanded="false"><span class="flow-select-value">' +
      escapeHTML(selected.label) +
      '</span><svg class="flow-select-caret" viewBox="0 0 12 12" aria-hidden="true"><path fill="currentColor" d="M6 8L1 3h10z"/></svg></button><input type="hidden" name="project" value="' +
      escapeAttribute(selected.value) +
      '" required><div class="flow-select-menu flow-scroll flow-scroll-auto" role="listbox">' +
      options
        .map((option) => {
          return (
            '<button class="flow-select-option' +
            (option.value === selected.value ? " is-selected" : "") +
            '" type="button" role="option" aria-selected="' +
            (option.value === selected.value ? "true" : "false") +
            '" data-value="' +
            escapeAttribute(option.value) +
            '">' +
            escapeHTML(option.label) +
            "</button>"
          );
        })
        .join("") +
      "</div></div>"
    );
  }

  function setEditorAlignment(card) {
    card.classList.remove("editor-align-left", "editor-align-right", "editor-align-up");
    const panel = card.querySelector(".idea-editor-panel");
    if (!panel) return;
    const rect = card.getBoundingClientRect();
    const wallRect = wall?.getBoundingClientRect();
    const boundaryLeft = wallRect ? Math.max(wallRect.left, 16) : 16;
    const boundaryRight = wallRect ? Math.min(wallRect.right, window.innerWidth - 16) : window.innerWidth - 16;
    const editorWidth = Math.min(560, window.innerWidth - 32);
    if (rect.left + editorWidth > boundaryRight && rect.right - editorWidth >= boundaryLeft) card.classList.add("editor-align-left");
    if (rect.top + 320 > window.innerHeight - 16) card.classList.add("editor-align-up");
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
    dropdown.querySelector(".flow-select-value").textContent = label;
    const input = dropdown.querySelector('input[type="hidden"]');
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

  async function requestJson(url, options) {
    const response = await fetch(url, {
      ...(options || {}),
      headers: { Accept: "application/json", ...((options && options.headers) || {}) },
    });
    const text = await response.text();
    let data = null;
    if (text) {
      try {
        data = JSON.parse(text);
      } catch (_error) {}
    }
    if (!response.ok) {
      const message = (data && (data.detail || data.error)) || "Request failed with HTTP " + response.status + ".";
      throw new Error(Array.isArray(message) ? "Validation failed." : String(message));
    }
    return data;
  }

  function closeIdeasSurface() {
    if (window.parent && window.parent !== window) {
      window.parent.postMessage({ type: "flow:close-ideas" }, "*");
      return;
    }
    window.close();
  }

  function notifyParent() {
    if (window.parent && window.parent !== window) window.parent.postMessage({ type: "flow:ideas-mutated" }, "*");
  }

  function showToast(message) {
    const toast = document.createElement("div");
    toast.className = "toast";
    toast.textContent = message || "Done.";
    toastStack.appendChild(toast);
    window.setTimeout(() => toast.remove(), 2600);
  }

  function formatDate(value) {
    const date = value ? new Date(value) : null;
    if (!date || Number.isNaN(date.getTime())) return new Date().toISOString().slice(0, 10);
    return date.toISOString().slice(0, 10);
  }

  function escapeHTML(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function escapeAttribute(value) {
    return escapeHTML(value).replace(/`/g, "&#96;");
  }
})();
