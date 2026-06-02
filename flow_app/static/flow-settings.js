(function () {
  const config = window.FlowSettings || {};
  const toastStack = document.getElementById("toast-stack");
  const searchParams = new URLSearchParams(window.location.search);
  const projectForm = document.getElementById("project-form");
  const apiKeyForm = document.getElementById("api-key-form");
  let editingProjectSlug = "";
  let importPreviewItems = [];

  if (searchParams.get("embedded") === "1") document.body.classList.add("is-embedded");

  enhanceSelects(document);
  bindNavigation();
  bindScrollSpy();
  bindThemeControls();
  bindProjectControls();
  bindApiKeyControls();
  bindImportControls();
  initPriorityCounters();
  document.addEventListener("click", function () {
    closeFlowDropdowns();
  });
  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") return;
    if (document.querySelector("[data-flow-select].is-open")) {
      closeFlowDropdowns();
      return;
    }
    if (document.body.classList.contains("is-embedded")) closeSettingsSurface();
  });

  window.closeSettingsSurface = closeSettingsSurface;

  function bindNavigation() {
    document.querySelectorAll(".sidebar-link").forEach((link) => {
      link.addEventListener("click", function () {
        document.querySelectorAll(".sidebar-link.active").forEach((item) => item.classList.remove("active"));
        link.classList.add("active");
      });
    });
  }

  function bindScrollSpy() {
    const links = Array.from(document.querySelectorAll(".sidebar-link"));
    const sections = links
      .map((link) => document.getElementById((link.getAttribute("href") || "").replace(/^#/, "")))
      .filter(Boolean);
    const linksById = {};
    links.forEach((link) => {
      const id = (link.getAttribute("href") || "").replace(/^#/, "");
      if (id) linksById[id] = link;
    });
    let activeId = "";
    let scheduled = false;
    const setActive = (id) => {
      if (!id || id === activeId) return;
      activeId = id;
      links.forEach((link) => link.classList.toggle("active", linksById[id] === link));
    };
    const compute = () => {
      scheduled = false;
      let current = sections.length ? sections[0].id : "";
      sections.forEach((section) => {
        if (section.getBoundingClientRect().top <= 120) current = section.id;
      });
      setActive(current);
    };
    const onScroll = () => {
      compute();
      if (!scheduled && typeof requestAnimationFrame === "function") {
        scheduled = true;
        requestAnimationFrame(compute);
      }
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll, { passive: true });
    document.querySelector(".content")?.addEventListener("scroll", onScroll, { passive: true });
    links.forEach((link) => {
      link.addEventListener("click", function () {
        setActive((link.getAttribute("href") || "").replace(/^#/, ""));
      });
    });
    compute();
  }

  function enhanceSelects(scope) {
    (scope || document).querySelectorAll("select.form-input").forEach((select) => {
      const wrapper = document.createElement("div");
      wrapper.className = "flow-select" + (select.classList.contains("form-input-sm") ? " form-input-sm" : "");
      wrapper.setAttribute("data-flow-select", "");
      const options = Array.from(select.options);
      const chosen = select.options[select.selectedIndex] || options[0];
      const trigger = document.createElement("button");
      trigger.type = "button";
      trigger.className = "flow-select-trigger";
      trigger.setAttribute("aria-haspopup", "listbox");
      trigger.setAttribute("aria-expanded", "false");
      if (select.disabled) trigger.disabled = true;
      const value = document.createElement("span");
      value.className = "flow-select-value";
      value.textContent = chosen ? chosen.textContent : "";
      trigger.appendChild(value);
      trigger.insertAdjacentHTML("beforeend", '<svg class="flow-select-caret" viewBox="0 0 12 12" aria-hidden="true"><path fill="currentColor" d="M6 8L1 3h10z"/></svg>');
      const hidden = document.createElement("input");
      hidden.type = "hidden";
      hidden.value = chosen ? chosen.value : "";
      if (select.name) hidden.name = select.name;
      if (select.required) hidden.required = true;
      if (select.disabled) hidden.disabled = true;
      const menu = document.createElement("div");
      menu.className = "flow-select-menu flow-scroll flow-scroll-auto";
      menu.setAttribute("role", "listbox");
      options.forEach((nativeOption) => {
        const option = document.createElement("button");
        option.type = "button";
        option.className = "flow-select-option" + (nativeOption === chosen ? " is-selected" : "") + (nativeOption.disabled ? " is-disabled" : "");
        option.setAttribute("role", "option");
        option.setAttribute("aria-selected", nativeOption === chosen ? "true" : "false");
        if (nativeOption.disabled) option.setAttribute("aria-disabled", "true");
        option.dataset.value = nativeOption.value;
        option.textContent = nativeOption.textContent;
        menu.appendChild(option);
      });
      wrapper.appendChild(trigger);
      wrapper.appendChild(hidden);
      wrapper.appendChild(menu);
      select.parentNode.replaceChild(wrapper, select);
    });
    initFlowDropdowns(scope);
  }

  function initFlowDropdowns(scope) {
    (scope || document).querySelectorAll("[data-flow-select]").forEach((dropdown) => {
      if (dropdown.dataset.ready === "true") return;
      dropdown.dataset.ready = "true";
      const trigger = dropdown.querySelector(".flow-select-trigger");
      const options = Array.from(dropdown.querySelectorAll(".flow-select-option"));
      if (!trigger) return;
      trigger.addEventListener("click", function (event) {
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
        if (option.classList.contains("is-disabled")) return;
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
    const valueNode = dropdown.querySelector(".flow-select-value");
    const hidden = dropdown.querySelector('input[type="hidden"]');
    if (valueNode) valueNode.textContent = label;
    if (hidden) hidden.value = value;
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

  function bindThemeControls() {
    syncThemeFromStorage();
    document.querySelectorAll('input[name="theme"]').forEach((input) => {
      input.addEventListener("change", function () {
        if (!input.checked) return;
        const theme = input.value;
        applyTheme(theme);
        try {
          window.localStorage.setItem("flow.theme", theme);
        } catch (_error) {}
        // Sync the choice live to the parent board when embedded in its overlay.
        if (window.parent && window.parent !== window) window.parent.postMessage({ type: "flow:theme", theme }, "*");
        showToast("Theme updated.");
      });
    });
  }

  function initPriorityCounters() {
    document.querySelectorAll(".priority-counter").forEach((counter) => {
      if (counter.dataset.ready === "true") return;
      counter.dataset.ready = "true";
      const input = counter.querySelector("input");
      if (!input) return;
      const min = Number(counter.dataset.min || input.min || 0);
      const max = Number(counter.dataset.max || input.max || 1000);
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

  function applyTheme(theme) {
    if (theme) document.body.dataset.theme = theme;
  }

  function syncThemeFromStorage() {
    let saved = null;
    try {
      saved = window.localStorage.getItem("flow.theme");
    } catch (_error) {}
    if (!saved) return;
    applyTheme(saved);
    const radio = document.querySelector('input[name="theme"][value="' + saved + '"]');
    if (radio) radio.checked = true;
  }

  function bindProjectControls() {
    document.querySelector("[data-project-new]")?.addEventListener("click", openProjectCreateForm);
    document.getElementById("project-form-cancel")?.addEventListener("click", closeProjectForm);

    document.querySelectorAll("[data-edit-project]").forEach((button) => {
      button.addEventListener("click", function () {
        openProjectEditForm(button.dataset.editProject || "");
      });
    });

    projectForm?.addEventListener("submit", async function (event) {
      event.preventDefault();
      const payload = formPayload(projectForm);
      const slug = (payload.slug || editingProjectSlug || "").trim().toLowerCase();
      if (!slug) {
        projectForm.elements.slug?.focus();
        showToast("Project slug is required.");
        return;
      }
      payload.slug = slug;

      try {
        await requestJson(editingProjectSlug ? "/api/projects/" + encodeURIComponent(editingProjectSlug) : "/api/projects", {
          method: editingProjectSlug ? "PATCH" : "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(editingProjectSlug ? projectUpdatePayload(payload) : payload),
        });
        notifyParent();
        showToast(editingProjectSlug ? "Project updated." : "Project created.");
        window.setTimeout(() => window.location.reload(), 350);
      } catch (error) {
        showToast(error.message);
      }
    });
  }

  function bindApiKeyControls() {
    document.querySelector("[data-open-key-form]")?.addEventListener("click", function () {
      if (!config.canManageApiKeys) {
        showToast("Admin session required.");
        return;
      }
      document.getElementById("key-gen")?.removeAttribute("hidden");
      apiKeyForm?.elements.name?.focus();
    });

    document.querySelector("[data-close-key-form]")?.addEventListener("click", function () {
      closeApiKeyForm();
    });

    document.querySelector("[data-refresh-api-keys]")?.addEventListener("click", function () {
      window.location.reload();
    });

    document.querySelectorAll("[data-revoke-api-key]").forEach((button) => {
      button.addEventListener("click", async function () {
        const keyId = button.dataset.revokeApiKey;
        if (!keyId || !window.confirm("Revoke this API key?")) return;
        try {
          await requestJson("/api/api-keys/" + encodeURIComponent(keyId) + "/revoke", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: "{}",
          });
          notifyParent();
          showToast("API key revoked.");
          window.setTimeout(() => window.location.reload(), 350);
        } catch (error) {
          showToast(error.message);
        }
      });
    });

    document.querySelector("[data-copy-api-key]")?.addEventListener("click", async function () {
      const input = document.getElementById("generated-api-key-value");
      if (!input?.value) return;
      try {
        await navigator.clipboard.writeText(input.value);
        showToast("Copied.");
      } catch (_error) {
        input.select();
        document.execCommand("copy");
        showToast("Copied.");
      }
    });

    apiKeyForm?.addEventListener("submit", async function (event) {
      event.preventDefault();
      if (!config.canManageApiKeys) {
        showToast("Admin session required.");
        return;
      }
      try {
        const created = await requestJson("/api/api-keys", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(formPayload(apiKeyForm)),
        });
        const generatedPanel = document.getElementById("generated-api-key");
        const valueInput = document.getElementById("generated-api-key-value");
        if (valueInput) valueInput.value = created.api_key || "";
        generatedPanel?.removeAttribute("hidden");
        apiKeyForm.reset();
        notifyParent();
        showToast("API key generated.");
      } catch (error) {
        showToast(error.message);
      }
    });
  }

  function bindImportControls() {
    const importForm = document.getElementById("import-form");
    const commitButton = document.getElementById("import-commit-button");
    const preview = document.getElementById("import-preview");
    if (!importForm || !commitButton || !preview) return;

    importForm.addEventListener("submit", async function (event) {
      event.preventDefault();
      importPreviewItems = [];
      commitButton.disabled = true;
      const payload = formPayload(importForm);
      payload.default_priority = Number(payload.default_priority || 50);
      if (!payload.markdown) {
        importForm.elements.markdown?.focus();
        showToast("Markdown is required.");
        return;
      }
      try {
        const result = await requestJson("/api/import/markdown/preview", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        importPreviewItems = result.items || [];
        preview.hidden = false;
        preview.textContent = importPreviewText(importPreviewItems);
        commitButton.disabled = !importPreviewItems.length || importPreviewItems.every((item) => item.duplicate);
        showToast("Preview ready.");
      } catch (error) {
        preview.hidden = false;
        preview.textContent = error.message;
        showToast(error.message);
      }
    });

    commitButton.addEventListener("click", async function () {
      if (!importPreviewItems.length) return;
      try {
        const result = await requestJson("/api/import/markdown/commit", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ items: importPreviewItems }),
        });
        preview.hidden = false;
        preview.textContent =
          "Created " +
          (result.created || []).length +
          " task(s). Skipped " +
          (result.skipped || []).length +
          ".\nBatch: " +
          (result.import_batch_id || "-");
        importPreviewItems = [];
        commitButton.disabled = true;
        notifyParent();
        showToast("Import committed.");
      } catch (error) {
        showToast(error.message);
      }
    });
  }

  function importPreviewText(items) {
    if (!items.length) return "No tasks found.";
    return items
      .map((item, index) => {
        const duplicate = item.duplicate ? " duplicate" + (item.duplicate_task_id ? " of " + item.duplicate_task_id : "") : "";
        return (
          index + 1 +
          ". [" +
          (item.status || "backlog") +
          "] " +
          (item.title || "Untitled") +
          " / " +
          (item.project || "default") +
          " / P" +
          (item.priority == null ? 50 : item.priority) +
          duplicate
        );
      })
      .join("\n");
  }

  function openProjectCreateForm() {
    editingProjectSlug = "";
    projectForm?.reset();
    if (!projectForm) return;
    projectForm.hidden = false;
    projectForm.elements.slug.readOnly = false;
    projectForm.elements.default_branch.value = "main";
    setProjectFormMode("Create project", "Create project", true);
    projectForm.elements.name?.focus();
  }

  async function openProjectEditForm(slug) {
    if (!slug || !projectForm) return;
    try {
      const project = await requestJson("/api/projects/" + encodeURIComponent(slug));
      editingProjectSlug = project.slug;
      projectForm.hidden = false;
      projectForm.elements.name.value = project.name || "";
      projectForm.elements.slug.value = project.slug || "";
      projectForm.elements.slug.readOnly = true;
      projectForm.elements.description.value = project.description || "";
      projectForm.elements.repo_url.value = project.repo_url || "";
      projectForm.elements.repo_path.value = project.repo_path || "";
      projectForm.elements.default_branch.value = project.default_branch || "main";
      setProjectFormMode("Edit project", "Save project", false);
      projectForm.scrollIntoView({ behavior: "smooth", block: "center" });
      projectForm.elements.name?.focus();
    } catch (error) {
      showToast(error.message);
    }
  }

  function closeProjectForm() {
    editingProjectSlug = "";
    projectForm?.reset();
    if (projectForm) projectForm.hidden = true;
  }

  function closeApiKeyForm() {
    apiKeyForm?.reset();
    document.getElementById("key-gen")?.setAttribute("hidden", "");
    document.getElementById("generated-api-key")?.setAttribute("hidden", "");
    const valueInput = document.getElementById("generated-api-key-value");
    if (valueInput) valueInput.value = "";
  }

  function setProjectFormMode(title, submitLabel, isNew) {
    const titleNode = document.getElementById("project-form-title");
    const submitNode = document.getElementById("project-submit-button");
    const hint = document.getElementById("project-slug-hint");
    if (titleNode) titleNode.textContent = title;
    if (submitNode) submitNode.textContent = submitLabel;
    if (hint) hint.hidden = isNew;
  }

  function projectUpdatePayload(payload) {
    return {
      name: payload.name,
      description: payload.description,
      repo_url: payload.repo_url,
      repo_path: payload.repo_path,
      default_branch: payload.default_branch,
    };
  }

  function formPayload(form) {
    const payload = {};
    new FormData(form).forEach((value, key) => {
      payload[key] = typeof value === "string" ? value.trim() : value;
    });
    return payload;
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
      const detail = data && (data.detail || data.error);
      const message = Array.isArray(detail) ? "Validation failed." : detail || "Request failed with HTTP " + response.status + ".";
      throw new Error(String(message));
    }
    return data;
  }

  function closeSettingsSurface() {
    if (window.parent && window.parent !== window) {
      window.parent.postMessage({ type: "flow:close-settings" }, "*");
      return;
    }
    window.close();
  }

  function notifyParent() {
    if (window.parent && window.parent !== window) window.parent.postMessage({ type: "flow:settings-mutated" }, "*");
  }

  function showToast(message) {
    if (!toastStack) return;
    const toast = document.createElement("div");
    toast.className = "toast";
    toast.textContent = message || "Done.";
    toastStack.appendChild(toast);
    window.setTimeout(() => toast.remove(), 2600);
  }
})();
