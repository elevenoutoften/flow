from __future__ import annotations

from sqlalchemy import inspect, text


def ensure_compatible_schema(engine) -> None:
    inspector = inspect(engine)
    table_names = inspector.get_table_names()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS agents (
                    id VARCHAR(32) NOT NULL PRIMARY KEY,
                    name VARCHAR(180) NOT NULL UNIQUE,
                    description TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    agent_type VARCHAR(24) NOT NULL DEFAULT 'cli',
                    capabilities TEXT NOT NULL DEFAULT '',
                    command TEXT NOT NULL DEFAULT '',
                    command_allowlist TEXT NOT NULL DEFAULT '',
                    env_allowlist TEXT NOT NULL DEFAULT '',
                    working_directory VARCHAR(500) NOT NULL DEFAULT '',
                    max_concurrency INTEGER NOT NULL DEFAULT 1,
                    heartbeat_timeout_seconds INTEGER NOT NULL DEFAULT 300,
                    stale_claim_timeout_seconds INTEGER NOT NULL DEFAULT 600,
                    dispatch_statuses TEXT NOT NULL DEFAULT 'backlog,todo',
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS agent_runs (
                    id VARCHAR(32) NOT NULL PRIMARY KEY,
                    agent_id VARCHAR(32) NOT NULL,
                    task_id VARCHAR(32) NOT NULL,
                    status VARCHAR(24) NOT NULL DEFAULT 'pending',
                    pid INTEGER,
                    exit_code INTEGER,
                    started_at DATETIME,
                    finished_at DATETIME,
                    last_heartbeat_at DATETIME,
                    workspace_state TEXT NOT NULL DEFAULT '',
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE CASCADE,
                    FOREIGN KEY(task_id) REFERENCES tasks (id) ON DELETE CASCADE
                )
                """
            )
        )
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_agent_runs_agent_id ON agent_runs (agent_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_agent_runs_task_id ON agent_runs (task_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_agent_runs_status ON agent_runs (status)"))
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS automation_rules (
                    id VARCHAR(32) NOT NULL PRIMARY KEY,
                    name VARCHAR(180) NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    priority INTEGER NOT NULL DEFAULT 50,
                    trigger VARCHAR(60) NOT NULL,
                    trigger_config TEXT NOT NULL DEFAULT '',
                    conditions TEXT NOT NULL DEFAULT '',
                    actions TEXT NOT NULL DEFAULT '',
                    last_run_at DATETIME,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS task_links (
                    id VARCHAR(32) NOT NULL PRIMARY KEY,
                    parent_id VARCHAR(32) NOT NULL,
                    child_id VARCHAR(32) NOT NULL,
                    link_type VARCHAR(24) NOT NULL DEFAULT 'blocks',
                    created_at DATETIME NOT NULL,
                    FOREIGN KEY(parent_id) REFERENCES tasks (id) ON DELETE CASCADE,
                    FOREIGN KEY(child_id) REFERENCES tasks (id) ON DELETE CASCADE
                )
                """
            )
        )
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_task_links_parent_id ON task_links (parent_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_task_links_child_id ON task_links (child_id)"))
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS task_handoffs (
                    id VARCHAR(32) NOT NULL PRIMARY KEY,
                    task_id VARCHAR(32) NOT NULL,
                    author VARCHAR(120) NOT NULL,
                    author_key_id VARCHAR(64),
                    summary TEXT NOT NULL,
                    changed_files TEXT NOT NULL DEFAULT '',
                    commands_run TEXT NOT NULL DEFAULT '',
                    tests_run TEXT NOT NULL DEFAULT '',
                    artifacts TEXT NOT NULL DEFAULT '',
                    attempted_but_failed TEXT NOT NULL DEFAULT '',
                    remaining_work TEXT NOT NULL DEFAULT '',
                    outcome VARCHAR(24) NOT NULL DEFAULT 'success',
                    next_recommended_agent VARCHAR(120),
                    capabilities TEXT NOT NULL DEFAULT '',
                    created_at DATETIME NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES tasks (id) ON DELETE CASCADE
                )
                """
            )
        )
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_task_handoffs_task_id ON task_handoffs (task_id)"))
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS workspace_configs (
                    id VARCHAR(32) NOT NULL PRIMARY KEY,
                    name VARCHAR(180) NOT NULL UNIQUE,
                    strategy VARCHAR(24) NOT NULL DEFAULT 'git_worktree',
                    base_branch VARCHAR(240) NOT NULL DEFAULT 'main',
                    branch_prefix VARCHAR(120) NOT NULL DEFAULT 'task-',
                    root_dir VARCHAR(500) NOT NULL DEFAULT '',
                    scratch_root VARCHAR(500) NOT NULL DEFAULT '/tmp/flow-scratch',
                    description TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS webhook_configs (
                    id VARCHAR(32) NOT NULL PRIMARY KEY,
                    name VARCHAR(180) NOT NULL,
                    url VARCHAR(500) NOT NULL,
                    secret VARCHAR(512) NOT NULL,
                    secret_encrypted INTEGER NOT NULL DEFAULT 0,
                    events TEXT NOT NULL DEFAULT '',
                    active INTEGER NOT NULL DEFAULT 1,
                    max_retries INTEGER NOT NULL DEFAULT 3,
                    retry_backoff_seconds INTEGER NOT NULL DEFAULT 60,
                    project VARCHAR(120) NOT NULL DEFAULT '*',
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS webhook_deliveries (
                    id VARCHAR(32) NOT NULL PRIMARY KEY,
                    webhook_id VARCHAR(32) NOT NULL,
                    event VARCHAR(64) NOT NULL,
                    payload TEXT NOT NULL,
                    status VARCHAR(24) NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at DATETIME,
                    last_response_code INTEGER,
                    last_response_body TEXT,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    FOREIGN KEY(webhook_id) REFERENCES webhook_configs (id)
                )
                """
            )
        )
        connection.execute(
            text("CREATE INDEX IF NOT EXISTS ix_webhook_deliveries_webhook_id ON webhook_deliveries (webhook_id)")
        )
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS notification_deliveries (
                    id VARCHAR(32) NOT NULL PRIMARY KEY,
                    provider VARCHAR(24) NOT NULL,
                    event VARCHAR(64) NOT NULL,
                    task_id VARCHAR(32) NOT NULL,
                    payload TEXT NOT NULL,
                    status VARCHAR(24) NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_retries INTEGER NOT NULL DEFAULT 3,
                    next_attempt_at DATETIME,
                    last_response_code INTEGER,
                    last_response_body TEXT,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
                """
            )
        )
        connection.execute(
            text("CREATE INDEX IF NOT EXISTS ix_notification_deliveries_task_id ON notification_deliveries (task_id)")
        )
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS recurring_task_templates (
                    id VARCHAR(32) PRIMARY KEY,
                    name VARCHAR(200) NOT NULL,
                    project VARCHAR(120) NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    acceptance_criteria TEXT DEFAULT '',
                    priority INTEGER DEFAULT 500,
                    status VARCHAR(24) DEFAULT 'todo',
                    complexity VARCHAR(24) DEFAULT 'small',
                    impact VARCHAR(24) DEFAULT 'medium',
                    effort VARCHAR(24) DEFAULT 'medium',
                    risk VARCHAR(24) DEFAULT 'low',
                    cadence VARCHAR(60) NOT NULL,
                    next_run_at DATETIME NOT NULL,
                    enabled INTEGER DEFAULT 1,
                    metadata TEXT DEFAULT '{}',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        connection.execute(text("INSERT OR IGNORE INTO flow_counters (name, value) VALUES ('webhook', 0)"))
        connection.execute(text("INSERT OR IGNORE INTO flow_counters (name, value) VALUES ('delivery', 0)"))
        connection.execute(
            text("INSERT OR IGNORE INTO flow_counters (name, value) VALUES ('notification_delivery', 0)")
        )
        connection.execute(text("INSERT OR IGNORE INTO flow_counters (name, value) VALUES ('handoff', 0)"))
        connection.execute(text("INSERT OR IGNORE INTO flow_counters (name, value) VALUES ('recurring_task_template', 0)"))

    inspector = inspect(engine)
    table_names = inspector.get_table_names()
    if "agents" in table_names:
        existing_agent_columns = {column["name"] for column in inspector.get_columns("agents")}
        if "dispatch_statuses" not in existing_agent_columns:
            with engine.begin() as connection:
                connection.execute(
                    text("ALTER TABLE agents ADD COLUMN dispatch_statuses TEXT NOT NULL DEFAULT 'backlog,todo'")
                )
        if "command_allowlist" not in existing_agent_columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE agents ADD COLUMN command_allowlist TEXT NOT NULL DEFAULT ''"))

    if "tasks" not in table_names:
        return

    if "task_notes" in table_names:
        existing_task_note_columns = {column["name"] for column in inspector.get_columns("task_notes")}
        if "author_key_id" not in existing_task_note_columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE task_notes ADD COLUMN author_key_id VARCHAR(64)"))

    if "task_handoffs" in table_names:
        existing_task_handoff_columns = {column["name"] for column in inspector.get_columns("task_handoffs")}
        if "author_key_id" not in existing_task_handoff_columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE task_handoffs ADD COLUMN author_key_id VARCHAR(64)"))

    existing = {column["name"] for column in inspector.get_columns("tasks")}
    additions = {
        "source_filename": "VARCHAR(500)",
        "source_line": "INTEGER",
        "import_batch_id": "VARCHAR(64)",
        "source_title": "VARCHAR(240)",
        "claimer_key_id": "VARCHAR(64)",
        "version": "INTEGER NOT NULL DEFAULT 1",
        "human_required": "INTEGER NOT NULL DEFAULT 0",
        "assignee_type": "VARCHAR(24) NOT NULL DEFAULT 'agent'",
        "blocker_reason": "TEXT NOT NULL DEFAULT ''",
        "complexity": "VARCHAR(24) NOT NULL DEFAULT 'small'",
        "impact": "VARCHAR(24) NOT NULL DEFAULT 'medium'",
        "effort": "VARCHAR(24) NOT NULL DEFAULT 'medium'",
        "risk": "VARCHAR(24) NOT NULL DEFAULT 'low'",
    }

    with engine.begin() as connection:
        for column_name, column_type in additions.items():
            if column_name not in existing:
                connection.execute(text(f"ALTER TABLE tasks ADD COLUMN {column_name} {column_type}"))

    if "agent_runs" in inspector.get_table_names():
        existing_agent_run_columns = {column["name"] for column in inspector.get_columns("agent_runs")}
        if "workspace_state" not in existing_agent_run_columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE agent_runs ADD COLUMN workspace_state TEXT NOT NULL DEFAULT ''"))

    if "webhook_configs" in inspector.get_table_names():
        existing_webhook_config_columns = {column["name"] for column in inspector.get_columns("webhook_configs")}
        if "secret_encrypted" not in existing_webhook_config_columns:
            with engine.begin() as connection:
                connection.execute(
                    text("ALTER TABLE webhook_configs ADD COLUMN secret_encrypted INTEGER NOT NULL DEFAULT 0")
                )

    if "api_keys" not in inspector.get_table_names():
        return

    existing_api_key_columns = {column["name"] for column in inspector.get_columns("api_keys")}
    if "role" not in existing_api_key_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE api_keys ADD COLUMN role VARCHAR(32) NOT NULL DEFAULT 'read_only'"))
