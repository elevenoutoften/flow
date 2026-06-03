(function () {
  const config = window.FlowSettings || {};
  const toastStack = document.getElementById("toast-stack");
  const searchParams = new URLSearchParams(window.location.search);
  const projectForm = document.getElementById("project-form");
  const apiKeyForm = document.getElementById("api-key-form");
  const liveState = {
    agents: [],
    workspaces: [],
    runs: [],
    rules: [],
    webhooks: [],
    selectedWorkspaceId: "",
    selectedRunId: "",
    selectedWebhookId: "",
  };
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
  bindLiveSettingsControls();
  initNumberSteppers();
  loadLiveSettingsData();
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
      keepSidebarLinkInView(linksById[id]);
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

  function keepSidebarLinkInView(link) {
    const sidebar = document.querySelector(".sidebar");
    if (!sidebar || !link) return;
    const linkRect = link.getBoundingClientRect();
    const sidebarRect = sidebar.getBoundingClientRect();
    if (linkRect.top < sidebarRect.top + 24) {
      sidebar.scrollTop -= sidebarRect.top + 24 - linkRect.top;
    } else if (linkRect.bottom > sidebarRect.bottom - 24) {
      sidebar.scrollTop += linkRect.bottom - (sidebarRect.bottom - 24);
    }
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
      if (select.id) hidden.id = select.id;
      if (select.required) hidden.required = true;
      if (select.disabled) hidden.disabled = true;
      Object.keys(select.dataset || {}).forEach((key) => {
        wrapper.dataset[key] = select.dataset[key];
        hidden.dataset[key] = select.dataset[key];
      });
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

  function initNumberSteppers() {
    document.querySelectorAll(".number-stepper, .priority-counter").forEach((counter) => {
      if (counter.dataset.ready === "true") return;
      counter.dataset.ready = "true";
      const input = counter.querySelector("input");
      if (!input) return;
      const min = Number(counter.dataset.min || input.min || 0);
      const hasMax = counter.dataset.max !== undefined || input.hasAttribute("max");
      const max = hasMax ? Number(counter.dataset.max || input.max) : Infinity;
      const step = Number(counter.dataset.step || input.step || 1);
      const increment = Number.isFinite(step) && step > 0 ? step : 1;
      const clamp = (value) => Math.max(min, Math.min(max, value));
      const numericValue = () => {
        const value = Number(input.value);
        return Number.isFinite(value) ? value : min;
      };
      const updateValue = (value) => {
        input.value = String(clamp(value));
        input.dispatchEvent(new Event("input", { bubbles: true }));
        input.dispatchEvent(new Event("change", { bubbles: true }));
      };
      counter.querySelector(".stepper-dec, .priority-dec")?.addEventListener("click", () => {
        updateValue(numericValue() - increment);
      });
      counter.querySelector(".stepper-inc, .priority-inc")?.addEventListener("click", () => {
        updateValue(numericValue() + increment);
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

  function bindLiveSettingsControls() {
    document.querySelector("[data-refresh-agents]")?.addEventListener("click", loadAgents);
    document.querySelector("[data-open-agent-form]")?.addEventListener("click", () => openAgentForm());
    document.querySelector("[data-close-agent-form]")?.addEventListener("click", closeAgentForm);
    document.querySelector("[data-agent-dispatch-selected]")?.addEventListener("click", openSelectedAgentDispatchTest);
    document.getElementById("agent-form")?.addEventListener("submit", saveAgent);
    document.getElementById("agent-list")?.addEventListener("click", handleAgentTableClick);
    document.getElementById("agent-list")?.addEventListener("change", handleAgentToggle);

    document.querySelector("[data-refresh-workspaces]")?.addEventListener("click", loadWorkspaces);
    document.querySelector("[data-open-workspace-form]")?.addEventListener("click", () => openWorkspaceForm());
    document.querySelector("[data-close-workspace-form]")?.addEventListener("click", closeWorkspaceForm);
    document.querySelector("[data-provision-workspace]")?.addEventListener("click", provisionWorkspace);
    document.querySelector("[data-cleanup-workspace]")?.addEventListener("click", cleanupWorkspace);
    document.getElementById("workspace-form")?.addEventListener("submit", saveWorkspace);
    document.getElementById("workspace-list")?.addEventListener("click", handleWorkspaceTableClick);
    document.getElementById("workspace-list")?.addEventListener("change", handleWorkspaceToggle);

    document.querySelector("[data-refresh-runs]")?.addEventListener("click", loadAgentRuns);
    document.querySelector("[data-apply-run-filters]")?.addEventListener("click", loadAgentRuns);
    document.querySelector("[data-clear-run-filters]")?.addEventListener("click", clearRunFilters);
    document.querySelector("[data-open-run-test]")?.addEventListener("click", () => openRunTest());
    document.querySelector("[data-close-run-test]")?.addEventListener("click", closeRunTest);
    document.querySelector("[data-run-test-dispatch]")?.addEventListener("click", runTestDispatch);
    document.querySelector("[data-stale-recovery]")?.addEventListener("click", recoverStaleRuns);
    document.getElementById("agent-run-list")?.addEventListener("click", handleRunTableClick);

    document.querySelector("[data-refresh-rules]")?.addEventListener("click", loadAutomationRules);
    document.querySelector("[data-open-rule-form]")?.addEventListener("click", () => openRuleForm());
    document.querySelector("[data-close-rule-form]")?.addEventListener("click", closeRuleForm);
    document.querySelector("[data-rule-build-json]")?.addEventListener("click", buildRuleJsonFromControls);
    document.querySelector("[data-close-rule-test]")?.addEventListener("click", () => document.getElementById("rule-test")?.setAttribute("hidden", ""));
    document.querySelector("[data-evaluate-rule]")?.addEventListener("click", evaluateRule);
    document.getElementById("rule-form")?.addEventListener("submit", saveAutomationRule);
    document.getElementById("automation-rule-list")?.addEventListener("click", handleRuleTableClick);
    document.getElementById("automation-rule-list")?.addEventListener("change", handleRuleToggle);

    document.querySelector("[data-refresh-webhooks]")?.addEventListener("click", loadWebhooks);
    document.querySelector("[data-open-webhook-form]")?.addEventListener("click", () => openWebhookForm());
    document.querySelector("[data-close-webhook-form]")?.addEventListener("click", closeWebhookForm);
    document.querySelector("[data-delete-webhook]")?.addEventListener("click", deleteWebhook);
    document.querySelector("[data-load-webhook-deliveries]")?.addEventListener("click", loadWebhookDeliveries);
    document.getElementById("webhook-form")?.addEventListener("submit", saveWebhook);
    document.getElementById("webhook-list")?.addEventListener("click", handleWebhookTableClick);
    document.getElementById("webhook-list")?.addEventListener("change", handleWebhookToggle);
    document.getElementById("webhook-delivery-list")?.addEventListener("click", handleWebhookDeliveryClick);
  }

  async function loadLiveSettingsData() {
    await loadAgents();
    await Promise.allSettled([loadWorkspaces(), loadAgentRuns(), loadAutomationRules(), loadWebhooks()]);
  }

  async function loadAgents() {
    const body = document.getElementById("agent-list");
    if (!body) return;
    body.innerHTML = loadingRow(7, "Loading agents...");
    try {
      liveState.agents = await requestJson("/api/agents");
      renderAgents();
      updateAgentSelects();
    } catch (error) {
      body.innerHTML = errorRow(7, error.message);
    }
  }

  function renderAgents() {
    const body = document.getElementById("agent-list");
    if (!body) return;
    if (!liveState.agents.length) {
      body.innerHTML = emptyRow(7, "No agents registered.");
      return;
    }
    body.innerHTML = liveState.agents
      .map((agent) => {
        const caps = commaList(agent.capabilities).map((cap) => '<span class="flow-chip chip-neutral">' + escapeHtml(cap) + "</span>").join("");
        return (
          '<tr><td><label class="toggle"><input type="checkbox" data-agent-toggle="' +
          escapeAttr(agent.id) +
          '"' +
          (agent.enabled ? " checked" : "") +
          '><span class="toggle-track"></span></label></td><td class="cell-name">' +
          escapeHtml(agent.name) +
          "</td><td><code>" +
          escapeHtml(agent.agent_type || "cli") +
          '</code></td><td><span class="chip-row">' +
          (caps || '<span class="flow-chip chip-neutral">any</span>') +
          "</span></td><td>" +
          escapeHtml(agent.max_concurrency) +
          "</td><td><code>" +
          escapeHtml(agent.dispatch_statuses || "-") +
          '</code></td><td class="actions"><button class="btn btn-ghost btn-sm" type="button" data-agent-edit="' +
          escapeAttr(agent.id) +
          '">Edit</button><button class="btn btn-ghost btn-sm" type="button" data-agent-test="' +
          escapeAttr(agent.id) +
          '">Test</button></td></tr>'
        );
      })
      .join("");
  }

  function handleAgentTableClick(event) {
    const edit = event.target.closest("[data-agent-edit]");
    if (edit) openAgentForm(liveState.agents.find((agent) => agent.id === edit.dataset.agentEdit));
    const test = event.target.closest("[data-agent-test]");
    if (test) openRunTest(test.dataset.agentTest);
  }

  async function handleAgentToggle(event) {
    const toggle = event.target.closest("[data-agent-toggle]");
    if (!toggle) return;
    try {
      await requestJson("/api/agents/" + encodeURIComponent(toggle.dataset.agentToggle), jsonOptions("PATCH", { enabled: toggle.checked }));
      notifyParent();
      await loadAgents();
      showToast("Agent updated.");
    } catch (error) {
      toggle.checked = !toggle.checked;
      showToast(error.message);
    }
  }

  function openAgentForm(agent) {
    const form = document.getElementById("agent-form");
    if (!form) return;
    form.hidden = false;
    form.reset();
    setPanelTitle("agent-form-title", agent ? "Edit agent - " + agent.name : "Add agent");
    setFormValue(form, "id", agent?.id || "");
    setFormValue(form, "name", agent?.name || "");
    setFormValue(form, "agent_type", agent?.agent_type || "cli");
    setFormValue(form, "description", agent?.description || "");
    setFormValue(form, "enabled", agent ? agent.enabled : true);
    setFormValue(form, "max_concurrency", agent?.max_concurrency || 1);
    setCheckList(form, "capabilities", commaList(agent?.capabilities || ""));
    setCheckList(form, "dispatch_statuses", commaList(agent?.dispatch_statuses || "backlog,todo"));
    setFormValue(form, "working_directory", agent?.working_directory || "");
    setFormValue(form, "command", agent?.command || "");
    setFormValue(form, "command_allowlist", agent?.command_allowlist || "");
    setFormValue(form, "env_allowlist", agent?.env_allowlist || "");
    setFormValue(form, "heartbeat_timeout_seconds", agent?.heartbeat_timeout_seconds || 300);
    setFormValue(form, "stale_claim_timeout_seconds", agent?.stale_claim_timeout_seconds || 600);
    document.getElementById("agent-output")?.setAttribute("hidden", "");
    form.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function closeAgentForm() {
    document.getElementById("agent-form")?.setAttribute("hidden", "");
  }

  async function saveAgent(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const id = form.elements.id.value;
    const payload = {
      name: fieldValue(form, "name"),
      description: fieldValue(form, "description"),
      enabled: checkboxValue(form, "enabled"),
      agent_type: fieldValue(form, "agent_type") || "cli",
      capabilities: getCheckList(form, "capabilities").join(","),
      command: fieldValue(form, "command"),
      command_allowlist: fieldValue(form, "command_allowlist"),
      env_allowlist: fieldValue(form, "env_allowlist"),
      working_directory: fieldValue(form, "working_directory"),
      max_concurrency: numberValue(form, "max_concurrency", 1),
      heartbeat_timeout_seconds: numberValue(form, "heartbeat_timeout_seconds", 300),
      stale_claim_timeout_seconds: numberValue(form, "stale_claim_timeout_seconds", 600),
      dispatch_statuses: getCheckList(form, "dispatch_statuses").join(","),
    };
    try {
      await requestJson(id ? "/api/agents/" + encodeURIComponent(id) : "/api/agents", jsonOptions(id ? "PATCH" : "POST", payload));
      notifyParent();
      closeAgentForm();
      await loadAgents();
      showToast("Agent saved.");
    } catch (error) {
      showToast(error.message);
    }
  }

  function openSelectedAgentDispatchTest() {
    const id = document.getElementById("agent-form")?.elements.id.value || "";
    if (!id) {
      showOutput("agent-output", "Save the agent before testing dispatch.");
      return;
    }
    openRunTest(id);
  }

  async function loadWorkspaces() {
    const body = document.getElementById("workspace-list");
    if (!body) return;
    body.innerHTML = loadingRow(7, "Loading workspaces...");
    try {
      liveState.workspaces = await requestJson("/api/workspace-configs");
      renderWorkspaces();
    } catch (error) {
      body.innerHTML = errorRow(7, error.message);
    }
  }

  function renderWorkspaces() {
    const body = document.getElementById("workspace-list");
    if (!body) return;
    if (!liveState.workspaces.length) {
      body.innerHTML = emptyRow(7, "No workspace configs.");
      return;
    }
    body.innerHTML = liveState.workspaces
      .map((workspace) => {
        return (
          '<tr><td><label class="toggle"><input type="checkbox" data-workspace-toggle="' +
          escapeAttr(workspace.id) +
          '"' +
          (workspace.enabled ? " checked" : "") +
          '><span class="toggle-track"></span></label></td><td class="cell-name">' +
          escapeHtml(workspace.name) +
          "</td><td><code>" +
          escapeHtml(workspace.strategy) +
          "</code></td><td><code>" +
          escapeHtml(workspace.base_branch || "-") +
          "</code></td><td><code>" +
          escapeHtml(workspace.root_dir || "-") +
          "</code></td><td><code>" +
          escapeHtml(workspace.scratch_root || "-") +
          '</code></td><td class="actions"><button class="btn btn-ghost btn-sm" type="button" data-workspace-edit="' +
          escapeAttr(workspace.id) +
          '">Edit</button></td></tr>'
        );
      })
      .join("");
  }

  function handleWorkspaceTableClick(event) {
    const edit = event.target.closest("[data-workspace-edit]");
    if (edit) openWorkspaceForm(liveState.workspaces.find((workspace) => workspace.id === edit.dataset.workspaceEdit));
  }

  async function handleWorkspaceToggle(event) {
    const toggle = event.target.closest("[data-workspace-toggle]");
    if (!toggle) return;
    try {
      await requestJson("/api/workspace-configs/" + encodeURIComponent(toggle.dataset.workspaceToggle), jsonOptions("PATCH", { enabled: toggle.checked }));
      notifyParent();
      await loadWorkspaces();
      showToast("Workspace updated.");
    } catch (error) {
      toggle.checked = !toggle.checked;
      showToast(error.message);
    }
  }

  function openWorkspaceForm(workspace) {
    const panels = document.getElementById("workspace-panels");
    const form = document.getElementById("workspace-form");
    if (!panels || !form) return;
    panels.hidden = false;
    form.reset();
    liveState.selectedWorkspaceId = workspace?.id || "";
    setPanelTitle("workspace-form-title", workspace ? "Edit workspace - " + workspace.name : "Create workspace");
    setFormValue(form, "id", workspace?.id || "");
    setFormValue(form, "name", workspace?.name || "");
    setFormValue(form, "strategy", workspace?.strategy || "git_worktree");
    setFormValue(form, "description", workspace?.description || "");
    setFormValue(form, "enabled", workspace ? workspace.enabled : true);
    setFormValue(form, "base_branch", workspace?.base_branch || "main");
    setFormValue(form, "branch_prefix", workspace?.branch_prefix || "task-");
    setFormValue(form, "root_dir", workspace?.root_dir || "");
    setFormValue(form, "scratch_root", workspace?.scratch_root || "/tmp/flow-scratch");
    setControlValue("workspace-cleanup-strategy", workspace?.strategy || "git_worktree");
    setValueById("workspace-tools-id", workspace?.id || "Save/select a workspace");
    hideOutput("workspace-output");
    panels.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function closeWorkspaceForm() {
    document.getElementById("workspace-panels")?.setAttribute("hidden", "");
  }

  async function saveWorkspace(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const id = form.elements.id.value;
    const payload = {
      name: fieldValue(form, "name"),
      strategy: fieldValue(form, "strategy") || "git_worktree",
      base_branch: fieldValue(form, "base_branch") || "main",
      branch_prefix: fieldValue(form, "branch_prefix") || "task-",
      root_dir: fieldValue(form, "root_dir"),
      scratch_root: fieldValue(form, "scratch_root") || "/tmp/flow-scratch",
      description: fieldValue(form, "description"),
      enabled: checkboxValue(form, "enabled"),
    };
    try {
      const saved = await requestJson(id ? "/api/workspace-configs/" + encodeURIComponent(id) : "/api/workspace-configs", jsonOptions(id ? "PATCH" : "POST", payload));
      notifyParent();
      await loadWorkspaces();
      openWorkspaceForm(saved);
      showToast("Workspace saved.");
    } catch (error) {
      showToast(error.message);
    }
  }

  async function provisionWorkspace() {
    const id = liveState.selectedWorkspaceId || document.getElementById("workspace-form")?.elements.id.value;
    const taskId = valueById("workspace-provision-task");
    if (!id || !taskId) {
      showOutput("workspace-output", "Select a workspace and enter a task ID.");
      return;
    }
    const params = new URLSearchParams({ task_id: taskId });
    const repo = valueById("workspace-provision-repo");
    if (repo) params.set("repo_path", repo);
    try {
      showOutput("workspace-output", JSON.stringify(await requestJson("/api/workspace-configs/" + encodeURIComponent(id) + "/provision?" + params.toString(), { method: "POST" }), null, 2));
    } catch (error) {
      showOutput("workspace-output", error.message);
    }
  }

  async function cleanupWorkspace() {
    const id = liveState.selectedWorkspaceId || document.getElementById("workspace-form")?.elements.id.value;
    const path = valueById("workspace-cleanup-path");
    const strategy = valueById("workspace-cleanup-strategy") || "git_worktree";
    if (!id || !path) {
      showOutput("workspace-output", "Select a workspace and enter a cleanup path.");
      return;
    }
    if (!window.confirm("Cleanup this workspace path?")) return;
    try {
      showOutput("workspace-output", JSON.stringify(await requestJson("/api/workspace-configs/" + encodeURIComponent(id) + "/cleanup", jsonOptions("POST", { path, strategy })), null, 2));
    } catch (error) {
      showOutput("workspace-output", error.message);
    }
  }

  async function loadAgentRuns() {
    const body = document.getElementById("agent-run-list");
    if (!body) return;
    body.innerHTML = loadingRow(6, "Loading agent runs...");
    const params = new URLSearchParams({ limit: "50" });
    const agentId = valueById("run-agent-filter");
    const status = valueById("run-status-filter");
    const taskId = valueById("run-task-filter");
    if (agentId) params.set("agent_id", agentId);
    if (status) params.set("status", status);
    if (taskId) params.set("task_id", taskId);
    try {
      const page = await requestJson("/api/agent-runs?" + params.toString());
      liveState.runs = page.items || [];
      renderAgentRuns();
    } catch (error) {
      body.innerHTML = errorRow(6, error.message);
    }
  }

  function renderAgentRuns() {
    const body = document.getElementById("agent-run-list");
    if (!body) return;
    if (!liveState.runs.length) {
      body.innerHTML = emptyRow(6, "No agent runs match the current filters.");
      return;
    }
    body.innerHTML = liveState.runs
      .map((run) => {
        const workspace = run.workspace_state || {};
        return (
          "<tr><td>" +
          statusChip(run.status) +
          "</td><td><code>" +
          escapeHtml(run.task_id || "-") +
          "</code></td><td>" +
          escapeHtml(agentName(run.agent_id)) +
          "</td><td><code>" +
          escapeHtml(formatDate(run.last_heartbeat_at || run.updated_at)) +
          "</code></td><td>" +
          (workspace.strategy ? '<span class="flow-chip chip-neutral">' + escapeHtml(workspace.strategy) + "</span>" : "-") +
          '</td><td class="actions"><button class="btn btn-ghost btn-sm" type="button" data-run-open="' +
          escapeAttr(run.id) +
          '">Open</button></td></tr>'
        );
      })
      .join("");
  }

  function handleRunTableClick(event) {
    const button = event.target.closest("[data-run-open]");
    if (!button) return;
    const run = liveState.runs.find((item) => item.id === button.dataset.runOpen);
    if (run) openRunDetail(run);
  }

  function openRunDetail(run) {
    liveState.selectedRunId = run.id;
    const workspace = run.workspace_state || {};
    const panel = document.getElementById("run-selected");
    if (!panel) return;
    panel.hidden = false;
    panel.innerHTML =
      '<div class="panel-title">Selected run - ' +
      escapeHtml(run.id) +
      '</div><div class="system-table">' +
      systemRow("status", statusChip(run.status)) +
      systemRow("agent_id", '<span class="system-value mono">' + escapeHtml(run.agent_id) + "</span>") +
      systemRow("task_id", '<span class="system-value mono">' + escapeHtml(run.task_id) + "</span>") +
      systemRow("pid", '<span class="system-value mono">' + escapeHtml(run.pid ?? "-") + "</span>") +
      systemRow("exit_code", '<span class="system-value mono">' + escapeHtml(run.exit_code ?? "-") + "</span>") +
      systemRow("workspace", '<span class="system-value mono">' + escapeHtml(workspace.path || workspace.strategy || "-") + "</span>") +
      systemRow("started_at", '<span class="system-value mono">' + escapeHtml(formatDate(run.started_at)) + "</span>") +
      systemRow("finished_at", '<span class="system-value mono">' + escapeHtml(formatDate(run.finished_at)) + "</span>") +
      systemRow("last_heartbeat_at", '<span class="system-value mono">' + escapeHtml(formatDate(run.last_heartbeat_at)) + "</span>") +
      '</div><div class="row-actions"><button class="btn btn-ghost btn-sm" type="button" data-run-heartbeat="' +
      escapeAttr(run.id) +
      '">Heartbeat</button><button class="btn btn-ghost btn-sm" type="button" data-run-complete="' +
      escapeAttr(run.id) +
      '">Complete 0</button><button class="btn btn-ghost btn-sm" type="button" data-run-close>Close</button></div>';
    panel.onclick = handleRunDetailClick;
    panel.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  async function handleRunDetailClick(event) {
    const panel = document.getElementById("run-selected");
    const close = event.target.closest("[data-run-close]");
    if (close) {
      panel?.setAttribute("hidden", "");
      return;
    }
    const heartbeat = event.target.closest("[data-run-heartbeat]");
    const complete = event.target.closest("[data-run-complete]");
    try {
      if (heartbeat) await requestJson("/api/agent-runs/" + encodeURIComponent(heartbeat.dataset.runHeartbeat) + "/heartbeat", { method: "POST" });
      if (complete) await requestJson("/api/agent-runs/" + encodeURIComponent(complete.dataset.runComplete) + "/complete?exit_code=0", { method: "POST" });
      if (heartbeat || complete) {
        notifyParent();
        await loadAgentRuns();
        showToast("Run updated.");
      }
    } catch (error) {
      showToast(error.message);
    }
  }

  function clearRunFilters() {
    setControlValue("run-agent-filter", "");
    setControlValue("run-status-filter", "");
    setValueById("run-task-filter", "");
    loadAgentRuns();
  }

  function openRunTest(agentId) {
    const panel = document.getElementById("run-test");
    if (!panel) return;
    panel.hidden = false;
    updateAgentSelects(agentId || valueById("run-test-agent"));
    hideOutput("run-test-output");
    panel.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function closeRunTest() {
    document.getElementById("run-test")?.setAttribute("hidden", "");
  }

  async function runTestDispatch() {
    const agentId = valueById("run-test-agent");
    const taskId = valueById("run-test-task");
    const dryRun = document.getElementById("run-test-dry")?.checked !== false;
    const agent = liveState.agents.find((item) => item.id === agentId);
    if (!agentId || !agent) {
      showOutput("run-test-output", "Select an agent first.");
      return;
    }
    if (dryRun) {
      showOutput(
        "run-test-output",
        [
          "Dry-run only: no task was claimed.",
          "Agent: " + agent.name,
          "Enabled: " + String(agent.enabled),
          "Dispatch statuses: " + (agent.dispatch_statuses || "-"),
          taskId ? "Task: " + taskId : "Task: next eligible task",
        ].join("\n")
      );
      return;
    }
    const params = new URLSearchParams();
    if (taskId) params.set("task_id", taskId);
    try {
      const run = await requestJson("/api/agents/" + encodeURIComponent(agentId) + "/dispatch" + (params.toString() ? "?" + params.toString() : ""), { method: "POST" });
      notifyParent();
      showOutput("run-test-output", JSON.stringify(run, null, 2));
      await loadAgentRuns();
    } catch (error) {
      showOutput("run-test-output", error.message);
    }
  }

  async function recoverStaleRuns() {
    if (!window.confirm("Run stale recovery now?")) return;
    try {
      showToast("Recovering stale runs...");
      await requestJson("/api/agent-runs/stale-recovery", { method: "POST" });
      notifyParent();
      await loadAgentRuns();
      showToast("Stale recovery finished.");
    } catch (error) {
      showToast(error.message);
    }
  }

  async function loadAutomationRules() {
    const body = document.getElementById("automation-rule-list");
    if (!body) return;
    body.innerHTML = loadingRow(6, "Loading automation rules...");
    try {
      const page = await requestJson("/api/automation-rules?limit=100");
      liveState.rules = page.items || [];
      renderAutomationRules();
    } catch (error) {
      body.innerHTML = errorRow(6, error.message);
    }
  }

  function renderAutomationRules() {
    const body = document.getElementById("automation-rule-list");
    if (!body) return;
    if (!liveState.rules.length) {
      body.innerHTML = emptyRow(6, "No automation rules.");
      return;
    }
    body.innerHTML = liveState.rules
      .map((rule) => {
        return (
          '<tr><td><label class="toggle"><input type="checkbox" data-rule-toggle="' +
          escapeAttr(rule.id) +
          '"' +
          (rule.enabled ? " checked" : "") +
          '><span class="toggle-track"></span></label></td><td class="cell-name">' +
          escapeHtml(rule.name) +
          "</td><td><code>" +
          escapeHtml(rule.trigger) +
          "</code></td><td>" +
          escapeHtml(rule.priority) +
          "</td><td><code>" +
          escapeHtml(formatDate(rule.last_run_at) || "-") +
          '</code></td><td class="actions"><button class="btn btn-ghost btn-sm" type="button" data-rule-edit="' +
          escapeAttr(rule.id) +
          '">Edit</button><button class="btn btn-ghost btn-sm" type="button" data-rule-test="' +
          escapeAttr(rule.id) +
          '">Test</button></td></tr>'
        );
      })
      .join("");
  }

  function handleRuleTableClick(event) {
    const edit = event.target.closest("[data-rule-edit]");
    if (edit) openRuleForm(liveState.rules.find((rule) => rule.id === edit.dataset.ruleEdit));
    const test = event.target.closest("[data-rule-test]");
    if (test) openRuleTest(liveState.rules.find((rule) => rule.id === test.dataset.ruleTest));
  }

  async function handleRuleToggle(event) {
    const toggle = event.target.closest("[data-rule-toggle]");
    if (!toggle) return;
    try {
      await requestJson("/api/automation-rules/" + encodeURIComponent(toggle.dataset.ruleToggle), jsonOptions("PATCH", { enabled: toggle.checked }));
      notifyParent();
      await loadAutomationRules();
      showToast("Rule updated.");
    } catch (error) {
      toggle.checked = !toggle.checked;
      showToast(error.message);
    }
  }

  function openRuleForm(rule) {
    const form = document.getElementById("rule-form");
    if (!form) return;
    form.hidden = false;
    form.reset();
    setPanelTitle("rule-form-title", rule ? "Edit rule - " + rule.name : "Create rule");
    setFormValue(form, "id", rule?.id || "");
    setFormValue(form, "name", rule?.name || "");
    setFormValue(form, "priority", rule?.priority ?? 50);
    setFormValue(form, "description", rule?.description || "");
    setFormValue(form, "enabled", rule ? rule.enabled : true);
    setFormValue(form, "trigger", rule?.trigger || "task_created");
    setFormValue(form, "trigger_config", prettyJsonText(rule?.trigger_config || "{}"));
    setFormValue(form, "conditions", prettyJsonText(rule?.conditions || "[]"));
    setFormValue(form, "actions", prettyJsonText(rule?.actions || "[]"));
    hydrateRuleBuilder(rule);
    form.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function hydrateRuleBuilder(rule) {
    const triggerConfig = parseJsonObject(rule?.trigger_config, {});
    setControlValueFromSelector("[data-rule-project]", triggerConfig.project || "*");
    setControlValueFromSelector("[data-rule-from-status]", triggerConfig.from_status || "*");
    setControlValueFromSelector("[data-rule-to-status]", triggerConfig.to_status || "*");
    setControlValueFromSelector("[data-rule-cron]", triggerConfig.cron || "");
    const condition = parseJsonArray(rule?.conditions, [])[0] || {};
    setControlValueFromSelector("[data-rule-condition-field]", condition.field || "");
    setControlValueFromSelector("[data-rule-condition-op]", condition.operator || "eq");
    setControlValueFromSelector("[data-rule-condition-value]", condition.value == null ? "" : String(condition.value));
    const action = parseJsonArray(rule?.actions, [])[0] || {};
    setControlValueFromSelector("[data-rule-action-type]", action.type || "notify");
    setControlValueFromSelector("[data-rule-action-target]", action.channel || action.agent_name || action.status || action.event || "");
    setControlValueFromSelector("[data-rule-action-value]", action.message || action.note || action.text || "");
  }

  function closeRuleForm() {
    document.getElementById("rule-form")?.setAttribute("hidden", "");
  }

  function buildRuleJsonFromControls() {
    const form = document.getElementById("rule-form");
    if (!form) return;
    const triggerConfig = compactObject({
      project: controlValueFromSelector("[data-rule-project]"),
      from_status: controlValueFromSelector("[data-rule-from-status]"),
      to_status: controlValueFromSelector("[data-rule-to-status]"),
      cron: controlValueFromSelector("[data-rule-cron]"),
    });
    const condition = compactObject({
      field: controlValueFromSelector("[data-rule-condition-field]"),
      operator: controlValueFromSelector("[data-rule-condition-op]") || "eq",
      value: controlValueFromSelector("[data-rule-condition-value]"),
    });
    const actionType = controlValueFromSelector("[data-rule-action-type]") || "notify";
    const action = compactObject({ type: actionType });
    const target = controlValueFromSelector("[data-rule-action-target]");
    const value = controlValueFromSelector("[data-rule-action-value]");
    if (actionType === "notify") {
      if (target) action.channel = target;
      if (value) action.message = value;
    } else if (actionType === "dispatch") {
      if (target) action.agent_name = target;
    } else if (actionType === "move") {
      if (target) action.status = target;
    } else if (actionType === "add_note") {
      if (value || target) action.body = value || target;
    } else if (actionType === "webhook") {
      if (target) action.event = target;
    }
    setFormValue(form, "trigger_config", JSON.stringify(triggerConfig, null, 2));
    setFormValue(form, "conditions", JSON.stringify(condition.field ? [condition] : [], null, 2));
    setFormValue(form, "actions", JSON.stringify([action], null, 2));
    showToast("Rule JSON updated.");
  }

  async function saveAutomationRule(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const id = form.elements.id.value;
    let payload;
    try {
      payload = {
        name: fieldValue(form, "name"),
        description: fieldValue(form, "description"),
        enabled: checkboxValue(form, "enabled"),
        priority: numberValue(form, "priority", 50),
        trigger: fieldValue(form, "trigger"),
        trigger_config: normalizeJsonText(fieldValue(form, "trigger_config"), "{}", "Trigger config"),
        conditions: normalizeJsonText(fieldValue(form, "conditions"), "[]", "Conditions"),
        actions: normalizeJsonText(fieldValue(form, "actions"), "[]", "Actions"),
      };
    } catch (error) {
      showToast(error.message);
      return;
    }
    try {
      await requestJson(id ? "/api/automation-rules/" + encodeURIComponent(id) : "/api/automation-rules", jsonOptions(id ? "PATCH" : "POST", payload));
      notifyParent();
      closeRuleForm();
      await loadAutomationRules();
      showToast("Rule saved.");
    } catch (error) {
      showToast(error.message);
    }
  }

  function openRuleTest(rule) {
    const panel = document.getElementById("rule-test");
    if (!panel) return;
    panel.hidden = false;
    setControlValue("rule-test-trigger", rule?.trigger || "task_created");
    hideOutput("rule-test-output");
    panel.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  async function evaluateRule() {
    let data = {};
    try {
      const parsed = JSON.parse(valueById("rule-test-data") || "{}");
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("invalid");
      data = parsed;
    } catch (_error) {
      showOutput("rule-test-output", "Data JSON must be a valid object.");
      return;
    }
    try {
      const result = await requestJson(
        "/api/automation-rules/evaluate",
        jsonOptions("POST", {
          trigger: valueById("rule-test-trigger") || "task_created",
          task_id: valueById("rule-test-task") || null,
          project: valueById("rule-test-project") || null,
          data,
        })
      );
      notifyParent();
      showOutput("rule-test-output", JSON.stringify(result, null, 2));
      await loadAutomationRules();
    } catch (error) {
      showOutput("rule-test-output", error.message);
    }
  }

  async function loadWebhooks() {
    const body = document.getElementById("webhook-list");
    if (!body) return;
    body.innerHTML = loadingRow(6, "Loading webhooks...");
    try {
      liveState.webhooks = await requestJson("/api/webhooks");
      renderWebhooks();
    } catch (error) {
      body.innerHTML = errorRow(6, error.message);
    }
  }

  function renderWebhooks() {
    const body = document.getElementById("webhook-list");
    if (!body) return;
    if (!liveState.webhooks.length) {
      body.innerHTML = emptyRow(6, "No webhooks configured.");
      return;
    }
    body.innerHTML = liveState.webhooks
      .map((webhook) => {
        return (
          '<tr><td><label class="toggle"><input type="checkbox" data-webhook-toggle="' +
          escapeAttr(webhook.id) +
          '"' +
          (webhook.active ? " checked" : "") +
          '><span class="toggle-track"></span></label></td><td class="cell-name">' +
          escapeHtml(webhook.name) +
          "</td><td><code>" +
          escapeHtml(shortUrl(webhook.url)) +
          "</code></td><td>" +
          escapeHtml((webhook.events || []).length + " events") +
          "</td><td><code>" +
          escapeHtml(webhook.project || "*") +
          '</code></td><td class="actions"><button class="btn btn-ghost btn-sm" type="button" data-webhook-edit="' +
          escapeAttr(webhook.id) +
          '">Edit</button><button class="btn btn-ghost btn-sm" type="button" data-webhook-deliveries="' +
          escapeAttr(webhook.id) +
          '">Deliveries</button></td></tr>'
        );
      })
      .join("");
  }

  function handleWebhookTableClick(event) {
    const edit = event.target.closest("[data-webhook-edit]");
    if (edit) openWebhookForm(liveState.webhooks.find((webhook) => webhook.id === edit.dataset.webhookEdit));
    const deliveries = event.target.closest("[data-webhook-deliveries]");
    if (deliveries) {
      const webhook = liveState.webhooks.find((item) => item.id === deliveries.dataset.webhookDeliveries);
      openWebhookDeliveries(webhook);
    }
  }

  async function handleWebhookToggle(event) {
    const toggle = event.target.closest("[data-webhook-toggle]");
    if (!toggle) return;
    try {
      await requestJson("/api/webhooks/" + encodeURIComponent(toggle.dataset.webhookToggle), jsonOptions("PATCH", { active: toggle.checked ? 1 : 0 }));
      notifyParent();
      await loadWebhooks();
      showToast("Webhook updated.");
    } catch (error) {
      toggle.checked = !toggle.checked;
      showToast(error.message);
    }
  }

  function openWebhookForm(webhook) {
    const panels = document.getElementById("webhook-panels");
    const form = document.getElementById("webhook-form");
    if (!panels || !form) return;
    panels.hidden = false;
    form.reset();
    liveState.selectedWebhookId = webhook?.id || "";
    setPanelTitle("webhook-form-title", webhook ? "Edit webhook - " + webhook.name : "Create webhook");
    setFormValue(form, "id", webhook?.id || "");
    setFormValue(form, "name", webhook?.name || "");
    setFormValue(form, "active", webhook ? Boolean(webhook.active) : true);
    setFormValue(form, "url", webhook?.url || "");
    setFormValue(form, "project", webhook?.project || "*");
    setFormValue(form, "max_retries", webhook?.max_retries ?? 3);
    setFormValue(form, "retry_backoff_seconds", webhook?.retry_backoff_seconds ?? 60);
    setCheckList(form, "webhook_events", webhook?.events || ["task_created", "task_moved", "task_claimed", "task_completed", "task_blocked"]);
    const deleteButton = document.querySelector("[data-delete-webhook]");
    if (deleteButton) deleteButton.hidden = !webhook;
    const note = document.getElementById("webhook-secret-note");
    if (note) {
      note.hidden = true;
      note.textContent = "";
    }
    setValueById("webhook-delivery-id", webhook?.id || "Save/select a webhook");
    panels.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function closeWebhookForm() {
    document.getElementById("webhook-panels")?.setAttribute("hidden", "");
  }

  async function saveWebhook(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const id = form.elements.id.value;
    const payload = {
      name: fieldValue(form, "name"),
      url: fieldValue(form, "url"),
      events: getCheckList(form, "webhook_events"),
      active: checkboxValue(form, "active") ? 1 : 0,
      project: fieldValue(form, "project") || "*",
      max_retries: numberValue(form, "max_retries", 3),
      retry_backoff_seconds: numberValue(form, "retry_backoff_seconds", 60),
    };
    if (!payload.events.length) {
      showToast("Select at least one webhook event.");
      return;
    }
    try {
      const saved = await requestJson(id ? "/api/webhooks/" + encodeURIComponent(id) : "/api/webhooks", jsonOptions(id ? "PATCH" : "POST", payload));
      notifyParent();
      await loadWebhooks();
      openWebhookForm(saved);
      const note = document.getElementById("webhook-secret-note");
      if (note && saved.secret) {
        note.hidden = false;
        note.textContent = "One-time secret: " + saved.secret;
      }
      showToast("Webhook saved.");
    } catch (error) {
      showToast(error.message);
    }
  }

  async function deleteWebhook() {
    const id = liveState.selectedWebhookId || document.getElementById("webhook-form")?.elements.id.value;
    if (!id || !window.confirm("Delete this webhook and its deliveries?")) return;
    try {
      await requestJson("/api/webhooks/" + encodeURIComponent(id), { method: "DELETE" });
      notifyParent();
      closeWebhookForm();
      await loadWebhooks();
      showToast("Webhook deleted.");
    } catch (error) {
      showToast(error.message);
    }
  }

  function openWebhookDeliveries(webhook) {
    if (!webhook) return;
    document.getElementById("webhook-panels")?.removeAttribute("hidden");
    liveState.selectedWebhookId = webhook.id;
    setValueById("webhook-delivery-id", webhook.id);
    hideOutput("webhook-delivery-detail");
    loadWebhookDeliveries();
    document.getElementById("webhook-deliveries")?.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  async function loadWebhookDeliveries() {
    const body = document.getElementById("webhook-delivery-list");
    const id = liveState.selectedWebhookId || valueById("webhook-delivery-id");
    if (!body) return;
    if (!id || id === "Save/select a webhook") {
      body.innerHTML = emptyRow(6, "Select a webhook.");
      return;
    }
    const params = new URLSearchParams({ limit: "25" });
    const status = valueById("webhook-delivery-status");
    if (status) params.set("status", status);
    body.innerHTML = loadingRow(6, "Loading deliveries...");
    try {
      const page = await requestJson("/api/webhooks/" + encodeURIComponent(id) + "/deliveries?" + params.toString());
      const deliveries = page.items || [];
      if (!deliveries.length) {
        body.innerHTML = emptyRow(6, "No deliveries.");
        return;
      }
      body.innerHTML = deliveries
        .map((delivery) => {
          return (
            "<tr><td><code>" +
            escapeHtml(formatDate(delivery.created_at)) +
            "</code></td><td>" +
            escapeHtml(delivery.event) +
            "</td><td>" +
            statusChip(delivery.status) +
            "</td><td><code>" +
            escapeHtml(delivery.last_response_code ?? "-") +
            "</code></td><td>" +
            escapeHtml(delivery.attempts) +
            '</td><td class="actions"><button class="btn btn-ghost btn-sm" type="button" data-delivery-view="' +
            escapeAttr(delivery.id) +
            '">View</button>' +
            (delivery.status === "failed"
              ? '<button class="btn btn-ghost btn-sm" type="button" data-delivery-retry="' + escapeAttr(delivery.id) + '">Retry</button>'
              : "") +
            "</td></tr>"
          );
        })
        .join("");
    } catch (error) {
      body.innerHTML = errorRow(6, error.message);
    }
  }

  async function handleWebhookDeliveryClick(event) {
    const view = event.target.closest("[data-delivery-view]");
    const retry = event.target.closest("[data-delivery-retry]");
    const webhookId = liveState.selectedWebhookId || valueById("webhook-delivery-id");
    if (!webhookId) return;
    try {
      if (view) {
        const delivery = await requestJson("/api/webhooks/" + encodeURIComponent(webhookId) + "/deliveries/" + encodeURIComponent(view.dataset.deliveryView));
        showOutput("webhook-delivery-detail", JSON.stringify(delivery, null, 2));
      }
      if (retry) {
        const result = await requestJson("/api/webhooks/" + encodeURIComponent(webhookId) + "/deliveries/" + encodeURIComponent(retry.dataset.deliveryRetry) + "/retry", { method: "POST" });
        showOutput("webhook-delivery-detail", JSON.stringify(result, null, 2));
        await loadWebhookDeliveries();
      }
    } catch (error) {
      showOutput("webhook-delivery-detail", error.message);
    }
  }

  function updateAgentSelects(selectedAgentId) {
    const agentOptions = liveState.agents.map((agent) => ({ value: agent.id, label: agent.name }));
    setDropdownOptions("run-agent-filter", [{ value: "", label: "All agents" }, ...agentOptions], valueById("run-agent-filter") || "");
    setDropdownOptions("run-test-agent", agentOptions, selectedAgentId || valueById("run-test-agent") || (agentOptions[0] && agentOptions[0].value) || "");
  }

  function setDropdownOptions(controlId, options, selectedValue) {
    const input = document.getElementById(controlId);
    if (!input) return;
    const dropdown = input.closest("[data-flow-select]");
    if (!dropdown) return;
    const trigger = dropdown.querySelector(".flow-select-trigger");
    const menu = dropdown.querySelector(".flow-select-menu");
    if (!menu) return;
    menu.innerHTML = "";
    options.forEach((item) => {
      const option = document.createElement("button");
      option.type = "button";
      option.className = "flow-select-option";
      option.setAttribute("role", "option");
      option.dataset.value = item.value;
      option.textContent = item.label;
      option.addEventListener("click", function (event) {
        event.stopPropagation();
        setFlowDropdownValue(dropdown, option.dataset.value || "", option.textContent.trim());
        closeFlowDropdowns();
      });
      option.addEventListener("keydown", function (event) {
        if (event.key === "Escape") {
          closeFlowDropdowns();
          trigger?.focus();
        }
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          option.click();
          trigger?.focus();
        }
      });
      menu.appendChild(option);
    });
    const selected = options.find((item) => item.value === selectedValue) || options[0] || { value: "", label: "" };
    setFlowDropdownValue(dropdown, selected.value, selected.label);
  }

  function loadingRow(colspan, message) {
    return '<tr><td colspan="' + colspan + '">' + escapeHtml(message) + "</td></tr>";
  }

  function emptyRow(colspan, message) {
    return '<tr><td colspan="' + colspan + '">' + escapeHtml(message) + "</td></tr>";
  }

  function errorRow(colspan, message) {
    return '<tr><td colspan="' + colspan + '"><span class="flow-chip chip-error">Error</span> ' + escapeHtml(message) + "</td></tr>";
  }

  function jsonOptions(method, payload) {
    return {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload || {}),
    };
  }

  function statusChip(status) {
    const value = String(status || "unknown");
    let cls = "chip-neutral";
    if (["success", "completed", "done"].includes(value)) cls = "chip-positive";
    else if (["failed", "error"].includes(value)) cls = "chip-error";
    else if (["running", "retrying"].includes(value)) cls = "chip-rose";
    else if (["pending", "stale"].includes(value)) cls = "chip-warning";
    return '<span class="flow-chip ' + cls + '">' + escapeHtml(value) + "</span>";
  }

  function systemRow(label, valueHtml) {
    return '<div class="system-row"><span class="system-label">' + escapeHtml(label) + "</span>" + valueHtml + "</div>";
  }

  function setPanelTitle(id, text) {
    const node = document.getElementById(id);
    if (node) node.textContent = text;
  }

  function setFormValue(form, name, value) {
    const field = form?.elements?.[name];
    if (!field) return;
    if (field.type === "checkbox") {
      field.checked = Boolean(value);
      return;
    }
    field.value = value == null ? "" : String(value);
    syncDropdownForInput(field);
  }

  function setControlValue(controlId, value) {
    const field = document.getElementById(controlId);
    if (!field) return;
    field.value = value == null ? "" : String(value);
    syncDropdownForInput(field);
  }

  function setControlValueFromSelector(selector, value) {
    const field = controlFromSelector(selector);
    if (!field) return;
    field.value = value == null ? "" : String(value);
    syncDropdownForInput(field);
  }

  function syncDropdownForInput(field) {
    const dropdown = field.closest?.("[data-flow-select]");
    if (!dropdown) return;
    const value = field.value || "";
    const option = Array.from(dropdown.querySelectorAll(".flow-select-option")).find((item) => (item.dataset.value || "") === value);
    setFlowDropdownValue(dropdown, value, option ? option.textContent.trim() : value);
  }

  function fieldValue(form, name) {
    return String(form?.elements?.[name]?.value || "").trim();
  }

  function checkboxValue(form, name) {
    return Boolean(form?.elements?.[name]?.checked);
  }

  function numberValue(form, name, fallback) {
    const value = Number(form?.elements?.[name]?.value);
    return Number.isFinite(value) ? value : fallback;
  }

  function valueById(id) {
    return String(document.getElementById(id)?.value || "").trim();
  }

  function setValueById(id, value) {
    const node = document.getElementById(id);
    if (node) node.value = value == null ? "" : String(value);
  }

  function controlValueFromSelector(selector) {
    const field = controlFromSelector(selector);
    return String(field?.value || "").trim();
  }

  function controlFromSelector(selector) {
    const node = document.querySelector(selector);
    if (!node) return null;
    if (node.matches("[data-flow-select]")) return node.querySelector('input[type="hidden"]');
    return node;
  }

  function setCheckList(form, name, values) {
    const wanted = new Set((values || []).map(String));
    form.querySelectorAll('[data-check-list="' + name + '"] input[type="checkbox"]').forEach((input) => {
      input.checked = wanted.has(input.value);
    });
  }

  function getCheckList(form, name) {
    return Array.from(form.querySelectorAll('[data-check-list="' + name + '"] input[type="checkbox"]:checked')).map((input) => input.value);
  }

  function commaList(value) {
    if (Array.isArray(value)) return value.map(String).filter(Boolean);
    return String(value || "")
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);
  }

  function agentName(agentId) {
    return (liveState.agents.find((agent) => agent.id === agentId) || {}).name || agentId || "-";
  }

  function shortUrl(value) {
    const text = String(value || "");
    if (text.length <= 42) return text;
    return text.slice(0, 26) + "..." + text.slice(-10);
  }

  function formatDate(value) {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toISOString().slice(0, 19).replace("T", " ");
  }

  function showOutput(id, text) {
    const output = document.getElementById(id);
    if (!output) return;
    output.hidden = false;
    output.textContent = text || "";
  }

  function hideOutput(id) {
    document.getElementById(id)?.setAttribute("hidden", "");
  }

  function parseJsonObject(value, fallback) {
    if (!String(value || "").trim()) return fallback;
    try {
      const parsed = JSON.parse(value);
      return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : fallback;
    } catch (_error) {
      return fallback;
    }
  }

  function parseJsonArray(value, fallback) {
    if (!String(value || "").trim()) return fallback;
    try {
      const parsed = JSON.parse(value);
      return Array.isArray(parsed) ? parsed : fallback;
    } catch (_error) {
      return fallback;
    }
  }

  function prettyJsonText(value) {
    try {
      return JSON.stringify(JSON.parse(value || "{}"), null, 2);
    } catch (_error) {
      return value || "";
    }
  }

  function normalizeJsonText(value, fallback, label) {
    try {
      return JSON.stringify(JSON.parse(value || fallback));
    } catch (_error) {
      throw new Error((label || "JSON") + " must be valid JSON.");
    }
  }

  function compactObject(value) {
    const result = {};
    Object.keys(value || {}).forEach((key) => {
      const item = value[key];
      if (item !== "" && item != null && item !== "*") result[key] = item;
    });
    return result;
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function escapeAttr(value) {
    return escapeHtml(value);
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
