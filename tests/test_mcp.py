from __future__ import annotations

from dataclasses import replace
import json
import logging

from flow_app.mcp import dispatch
from flow_app.ratelimit import mutation_limiter


def rpc(client, method, params=None, request_id=1):
    return client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}},
    )


def create_task(client, **overrides):
    payload = {
        "title": "Expose Flow through MCP",
        "status": "todo",
        "priority": 75,
        "project": "default",
        "description": "Wire JSON-RPC dispatcher.",
        "acceptance_criteria": "MCP tool returns task data.",
    }
    payload.update(overrides)
    response = client.post("/api/tasks", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def bearer_headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


def _set_mutation_limit(client, limit: int) -> None:
    client.app.state.settings = replace(client.app.state.settings, rate_limit_mutations=limit)
    mutation_limiter.reset()


def create_mcp_webhook(client, **overrides):
    payload = {
        "name": "MCP webhook",
        "url": "https://example.com/mcp-webhook",
        "events": ["task_created"],
        "project": "*",
    }
    payload.update(overrides)
    response = rpc(client, "tools/call", {"name": "flow_create_webhook", "arguments": payload})
    assert response.status_code == 200, response.text
    return response.json()["result"]["structuredContent"]["webhook"]


def test_mcp_initialize_returns_server_info_and_capabilities(client):
    response = rpc(client, "initialize")

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["protocolVersion"] == "2025-11-25"
    assert result["serverInfo"]["name"] == "flow"
    assert "tools" in result["capabilities"]


def test_mcp_ping_returns_empty_object(client):
    response = rpc(client, "ping")

    assert response.status_code == 200
    assert response.json()["result"] == {}


def test_mcp_tools_list_returns_flow_tool_definitions(client):
    response = rpc(client, "tools/list")

    assert response.status_code == 200
    tools = response.json()["result"]["tools"]
    tool_names = [tool["name"] for tool in tools]
    assert "flow_list_tasks" in tool_names
    assert "flow_get_task" in tool_names
    assert "flow_create_task" in tool_names
    assert "flow_update_task" in tool_names
    assert "flow_move_task" in tool_names
    assert "flow_task_handoff" in tool_names
    assert "flow_get_task_handoffs" in tool_names
    assert "flow_set_human_required" in tool_names
    assert "flow_board_summary" in tool_names
    assert "flow_list_ideas" in tool_names
    assert "flow_create_idea" in tool_names
    assert "flow_promote_idea" in tool_names
    assert "flow_get_webhook" in tool_names
    assert "flow_update_webhook" in tool_names
    assert "flow_disable_webhook" in tool_names
    assert "flow_delete_webhook" in tool_names
    assert "flow_list_webhook_deliveries" in tool_names
    assert "flow_get_webhook_delivery" in tool_names
    assert tools[0]["inputSchema"]["properties"]["status"]["enum"] == [
        "backlog",
        "todo",
        "doing",
        "review",
        "done",
    ]


def test_mcp_flow_list_tasks_returns_tasks(client):
    task = create_task(client)

    response = rpc(
        client,
        "tools/call",
        {"name": "flow_list_tasks", "arguments": {"project": "default", "status": "todo"}},
    )

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["structuredContent"]["count"] == 1
    assert result["structuredContent"]["total"] == 1
    assert result["structuredContent"]["limit"] == 100
    assert result["structuredContent"]["offset"] == 0
    assert result["structuredContent"]["tasks"][0]["id"] == task["id"]
    assert "Found 1 Flow task" in result["content"][0]["text"]


def test_mcp_flow_list_tasks_paginates(client):
    created = [create_task(client, title=f"MCP task {index}", priority=index) for index in range(4)]

    response = rpc(
        client,
        "tools/call",
        {"name": "flow_list_tasks", "arguments": {"limit": 2, "offset": 1}},
    )

    assert response.status_code == 200
    content = response.json()["result"]["structuredContent"]
    assert content["count"] == 2
    assert content["total"] == 4
    assert content["limit"] == 2
    assert content["offset"] == 1
    assert [task["id"] for task in content["tasks"]] == [created[2]["id"], created[1]["id"]]


def test_mcp_flow_get_task_returns_task(client):
    task = create_task(client, title="Read this task")

    response = rpc(
        client,
        "tools/call",
        {"name": "flow_get_task", "arguments": {"task_id": task["id"]}},
    )

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["structuredContent"]["task"]["id"] == task["id"]
    assert result["structuredContent"]["task"]["title"] == "Read this task"
    assert "Found Flow task" in result["content"][0]["text"]


def test_mcp_task_handoff_tools_create_and_list_handoffs(client):
    task = create_task(client)

    created = rpc(
        client,
        "tools/call",
        {
            "name": "flow_task_handoff",
            "arguments": {
                "task_id": task["id"],
                "summary": "MCP handoff recorded.",
                "changed_files": ["flow_app/mcp/dispatch.py"],
                "commands_run": ["pytest tests/test_mcp.py"],
                "tests_run": ["tests/test_mcp.py"],
                "outcome": "success",
                "remaining_work": "",
                "next_recommended_agent": "reviewer",
            },
        },
    )
    assert created.status_code == 200
    handoff = created.json()["result"]["structuredContent"]["handoff"]
    assert handoff["id"] == "handoff_000001"
    assert handoff["changed_files"] == ["flow_app/mcp/dispatch.py"]

    listed = rpc(
        client,
        "tools/call",
        {"name": "flow_get_task_handoffs", "arguments": {"task_id": task["id"]}},
    )
    assert listed.status_code == 200
    result = listed.json()["result"]
    assert result["structuredContent"]["count"] == 1
    assert result["structuredContent"]["handoffs"][0]["id"] == handoff["id"]


def test_mcp_flow_create_task_creates_task(client):
    response = rpc(
        client,
        "tools/call",
        {
            "name": "flow_create_task",
            "arguments": {
                "title": "Create task through MCP",
                "status": "backlog",
                "priority": 80,
                "project": "default",
                "description": "Created over JSON-RPC.",
                "acceptance_criteria": "Task is persisted.",
            },
        },
    )

    assert response.status_code == 200
    result = response.json()["result"]
    task = result["structuredContent"]["task"]
    assert task["id"] == "flow_000001"
    assert task["title"] == "Create task through MCP"
    assert task["acceptance_criteria"] == "Task is persisted."
    assert client.get(f"/api/tasks/{task['id']}").json()["title"] == "Create task through MCP"


def test_mcp_unexpected_error_logged(client, monkeypatch, caplog):
    def fail_commit(_db):
        raise Exception("unexpected db failure")

    monkeypatch.setattr(dispatch, "_commit", fail_commit)

    with caplog.at_level(logging.ERROR):
        response = rpc(
            client,
            "tools/call",
            {
                "name": "flow_create_task",
                "arguments": {
                    "title": "Create task through MCP",
                    "status": "backlog",
                    "priority": 80,
                    "project": "default",
                    "description": "Created over JSON-RPC.",
                    "acceptance_criteria": "Task is persisted.",
                },
            },
        )

    assert response.status_code == 200
    error = response.json()["error"]
    assert error["code"] == -32603
    assert error["message"] == "Internal server error."
    assert "unexpected db failure" not in error["message"]
    assert "Unexpected error while handling MCP request" in caplog.text
    assert "unexpected db failure" in caplog.text


def test_mcp_flow_create_and_update_agent_command_allowlist(client):
    created = rpc(
        client,
        "tools/call",
        {
            "name": "flow_create_agent",
            "arguments": {
                "name": "mcp-agent",
                "command": "python -m flow_app.hermes_wrapper",
                "command_allowlist": "python -m flow_app.hermes_wrapper",
            },
        },
    )

    assert created.status_code == 200
    agent = created.json()["result"]["structuredContent"]["agent"]
    assert agent["command_allowlist"] == "python -m flow_app.hermes_wrapper"

    updated = rpc(
        client,
        "tools/call",
        {
            "name": "flow_update_agent",
            "arguments": {"agent_id": agent["id"], "command_allowlist": "python3,codex"},
        },
    )

    assert updated.status_code == 200
    assert updated.json()["result"]["structuredContent"]["agent"]["command_allowlist"] == "python3,codex"


def test_mcp_webhook_management_tools_get_update_disable_and_delete(client):
    webhook = create_mcp_webhook(client, project="default")

    fetched = rpc(
        client,
        "tools/call",
        {"name": "flow_get_webhook", "arguments": {"webhook_id": webhook["id"]}},
    )
    assert fetched.status_code == 200
    assert fetched.json()["result"]["structuredContent"]["webhook"]["id"] == webhook["id"]

    updated = rpc(
        client,
        "tools/call",
        {
            "name": "flow_update_webhook",
            "arguments": {
                "webhook_id": webhook["id"],
                "url": "https://example.com/updated",
                "events": ["task_created", "task_completed"],
                "active": True,
                "project": "*",
            },
        },
    )
    assert updated.status_code == 200
    updated_webhook = updated.json()["result"]["structuredContent"]["webhook"]
    assert updated_webhook["url"] == "https://example.com/updated"
    assert updated_webhook["events"] == ["task_created", "task_completed"]
    assert updated_webhook["active"] == 1
    assert updated_webhook["project"] == "*"

    disabled = rpc(
        client,
        "tools/call",
        {"name": "flow_disable_webhook", "arguments": {"webhook_id": webhook["id"]}},
    )
    assert disabled.status_code == 200
    assert disabled.json()["result"]["structuredContent"]["webhook"]["active"] == 0

    deleted = rpc(
        client,
        "tools/call",
        {"name": "flow_delete_webhook", "arguments": {"webhook_id": webhook["id"]}},
    )
    assert deleted.status_code == 200
    assert deleted.json()["result"]["structuredContent"] == {"deleted": True, "webhook_id": webhook["id"]}
    assert client.get(f"/api/webhooks/{webhook['id']}").status_code == 404


def test_mcp_webhook_delivery_log_tools_list_and_get_detail(client):
    webhook = create_mcp_webhook(client, events=["task_created"])
    task = create_task(client, title="MCP delivery event")

    listed = rpc(
        client,
        "tools/call",
        {"name": "flow_list_webhook_deliveries", "arguments": {"webhook_id": webhook["id"], "limit": 1}},
    )
    assert listed.status_code == 200
    result = listed.json()["result"]["structuredContent"]
    assert result["count"] == 1
    assert result["total"] == 1
    assert result["limit"] == 1
    assert result["offset"] == 0
    delivery = result["deliveries"][0]
    assert delivery["webhook_id"] == webhook["id"]
    assert delivery["event"] == "task_created"
    assert delivery["status"] == "pending"
    assert delivery["attempts"] == 0
    assert "last_response_code" in delivery
    assert "last_response_body" in delivery

    detail = rpc(
        client,
        "tools/call",
        {"name": "flow_get_webhook_delivery", "arguments": {"delivery_id": delivery["id"]}},
    )
    assert detail.status_code == 200
    delivery_detail = detail.json()["result"]["structuredContent"]["delivery"]
    assert delivery_detail["id"] == delivery["id"]
    assert json.loads(delivery_detail["payload"])["task_id"] == task["id"]


def test_mcp_tool_call_without_auth_returns_error(no_auth_client):
    response = rpc(
        no_auth_client,
        "tools/call",
        {"name": "flow_list_tasks", "arguments": {}},
    )

    assert response.status_code == 200
    error = response.json()["error"]
    assert error["code"] == -32603
    assert "Insufficient permission" in error["message"]


def test_mcp_read_only_call_cannot_create_task(client, no_auth_client):
    created_key = client.post("/api/api-keys", json={"name": "reader", "role": "read_only"}).json()

    response = no_auth_client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "flow_create_task", "arguments": {"title": "Denied"}},
        },
        headers=bearer_headers(created_key["api_key"]),
    )

    assert response.status_code == 200
    error = response.json()["error"]
    assert error["code"] == -32603
    assert "tasks:create" in error["message"]


def test_mcp_mutating_tool_calls_are_rate_limited_per_actor_key(client):
    key = client.post("/api/api-keys", json={"name": "mcp-admin", "role": "admin"}).json()["api_key"]
    headers = bearer_headers(key)
    _set_mutation_limit(client, 2)

    for index in range(2):
        response = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": index + 1,
                "method": "tools/call",
                "params": {"name": "flow_create_task", "arguments": {"title": f"Limited MCP task {index}"}},
            },
            headers=headers,
        )
        assert response.status_code == 200, response.text

    blocked = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "flow_create_task", "arguments": {"title": "Blocked MCP task"}},
        },
        headers=headers,
    )

    assert blocked.status_code == 429
    body = blocked.json()
    assert body["error"]["code"] == -32600
    assert body["error"]["message"] == "Rate limit exceeded. Try again later."
    assert "result" not in body


def test_mcp_read_only_tool_calls_bypass_mutation_limit(client):
    create_task(client, title="Readable MCP task")
    _set_mutation_limit(client, 1)

    for request_id in range(1, 4):
        response = rpc(
            client,
            "tools/call",
            {"name": "flow_list_tasks", "arguments": {"project": "default"}},
            request_id=request_id,
        )
        assert response.status_code == 200, response.text
        assert response.json()["result"]["structuredContent"]["total"] == 1


def test_mcp_flow_update_task_updates_task(client):
    task = create_task(client, title="Original title")

    response = rpc(
        client,
        "tools/call",
        {"name": "flow_update_task", "arguments": {"task_id": task["id"], "title": "Updated title"}},
    )

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["structuredContent"]["task"]["title"] == "Updated title"
    assert "Updated Flow task" in result["content"][0]["text"]


def test_mcp_flow_update_task_denied_for_read_only(client, no_auth_client):
    task = create_task(client)
    created_key = client.post("/api/api-keys", json={"name": "reader-update", "role": "read_only"}).json()
    headers = bearer_headers(created_key["api_key"])

    response = no_auth_client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "flow_update_task", "arguments": {"task_id": task["id"], "title": "Denied"}},
        },
        headers=headers,
    )

    assert response.status_code == 200
    error = response.json()["error"]
    assert error["code"] == -32603
    assert "Insufficient permission" in error["message"]


def test_mcp_flow_move_task_moves_to_review(client):
    task = create_task(client, status="todo")

    response = rpc(
        client,
        "tools/call",
        {"name": "flow_move_task", "arguments": {"task_id": task["id"], "status": "review"}},
    )

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["structuredContent"]["task"]["status"] == "review"
    assert "Moved Flow task" in result["content"][0]["text"]


def test_mcp_flow_move_task_denied_for_read_only(client, no_auth_client):
    task = create_task(client, status="todo")
    created_key = client.post("/api/api-keys", json={"name": "reader-move", "role": "read_only"}).json()
    headers = bearer_headers(created_key["api_key"])

    response = no_auth_client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "flow_move_task", "arguments": {"task_id": task["id"], "status": "doing"}},
        },
        headers=headers,
    )

    assert response.status_code == 200
    error = response.json()["error"]
    assert error["code"] == -32603
    assert "Insufficient permission" in error["message"]


def test_mcp_read_only_cannot_claim_or_mutate_tasks(client, no_auth_client):
    task = create_task(client, status="todo")
    created_key = client.post("/api/api-keys", json={"name": "reader-strict", "role": "read_only"}).json()
    headers = bearer_headers(created_key["api_key"])

    # Claim is denied — read_only has no tasks:claim permission
    claim_response = no_auth_client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "flow_move_task", "arguments": {"task_id": task["id"], "status": "doing"}},
        },
        headers=headers,
    )
    assert claim_response.status_code == 200
    assert claim_response.json()["error"]["code"] == -32603


def test_mcp_flow_set_human_required_marks_task(client):
    task = create_task(client)

    response = rpc(
        client,
        "tools/call",
        {
            "name": "flow_set_human_required",
            "arguments": {"task_id": task["id"], "human_required": True},
        },
    )

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["structuredContent"]["task"]["human_required"] is True
    assert result["structuredContent"]["task"]["assignee_type"] == "agent"


def test_mcp_flow_set_human_required_denied_for_read_only(client, no_auth_client):
    task = create_task(client)
    created_key = client.post("/api/api-keys", json={"name": "reader-human", "role": "read_only"}).json()
    headers = bearer_headers(created_key["api_key"])

    response = no_auth_client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "flow_set_human_required", "arguments": {"task_id": task["id"], "human_required": True}},
        },
        headers=headers,
    )

    assert response.status_code == 200
    error = response.json()["error"]
    assert error["code"] == -32603
    assert "Insufficient permission" in error["message"]


def test_mcp_flow_board_summary_returns_counts(client):
    create_task(client, title="Task one", status="todo")
    create_task(client, title="Task two", status="doing")

    response = rpc(
        client,
        "tools/call",
        {"name": "flow_board_summary", "arguments": {"project": "default"}},
    )

    assert response.status_code == 200
    result = response.json()["result"]
    counts = result["structuredContent"]["counts_by_status"]
    assert counts["todo"] == 1
    assert counts["doing"] == 1
    assert result["structuredContent"]["project"] == "default"


def test_mcp_flow_create_idea_creates_idea(client):
    response = rpc(
        client,
        "tools/call",
        {"name": "flow_create_idea", "arguments": {"title": "New idea", "description": "A great idea", "project": "default"}},
    )

    assert response.status_code == 200
    result = response.json()["result"]
    idea = result["structuredContent"]["idea"]
    assert idea["title"] == "New idea"
    assert idea["description"] == "A great idea"
    assert "Created Flow idea" in result["content"][0]["text"]


def test_mcp_flow_list_ideas_returns_ideas(client):
    rpc(
        client,
        "tools/call",
        {"name": "flow_create_idea", "arguments": {"title": "Listable idea", "project": "default"}},
    )

    response = rpc(
        client,
        "tools/call",
        {"name": "flow_list_ideas", "arguments": {"project": "default"}},
    )

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["structuredContent"]["count"] >= 1
    assert result["structuredContent"]["total"] >= 1
    assert result["structuredContent"]["limit"] == 100
    assert result["structuredContent"]["offset"] == 0
    titles = [idea["title"] for idea in result["structuredContent"]["ideas"]]
    assert "Listable idea" in titles


def test_mcp_flow_archive_idea_archives(client):
    create_response = rpc(
        client,
        "tools/call",
        {"name": "flow_create_idea", "arguments": {"title": "Archive me", "project": "default"}},
    )
    idea_id = create_response.json()["result"]["structuredContent"]["idea"]["id"]

    response = rpc(
        client,
        "tools/call",
        {"name": "flow_archive_idea", "arguments": {"idea_id": idea_id}},
    )

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["structuredContent"]["idea"]["id"] == idea_id
    assert "Archived Flow idea" in result["content"][0]["text"]


def test_mcp_flow_promote_idea_creates_tasks(client):
    create_response = rpc(
        client,
        "tools/call",
        {"name": "flow_create_idea", "arguments": {"title": "Promotable idea", "project": "default"}},
    )
    idea_id = create_response.json()["result"]["structuredContent"]["idea"]["id"]

    response = rpc(
        client,
        "tools/call",
        {
            "name": "flow_promote_idea",
            "arguments": {
                "idea_id": idea_id,
                "tasks": [
                    {"title": "Subtask one", "status": "backlog", "priority": 50},
                    {"title": "Subtask two", "status": "todo", "priority": 60},
                ],
            },
        },
    )

    assert response.status_code == 200
    result = response.json()["result"]
    promoted_ids = result["structuredContent"]["idea"]["promoted_task_ids"]
    assert len(promoted_ids) == 2


def test_mcp_flow_promote_idea_denied_for_read_only(client, no_auth_client):
    create_response = rpc(
        client,
        "tools/call",
        {"name": "flow_create_idea", "arguments": {"title": "Protected idea", "project": "default"}},
    )
    idea_id = create_response.json()["result"]["structuredContent"]["idea"]["id"]
    created_key = client.post("/api/api-keys", json={"name": "reader-promote", "role": "read_only"}).json()
    headers = bearer_headers(created_key["api_key"])

    response = no_auth_client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "flow_promote_idea",
                "arguments": {"idea_id": idea_id, "tasks": [{"title": "Denied task"}]},
            },
        },
        headers=headers,
    )

    assert response.status_code == 200
    error = response.json()["error"]
    assert error["code"] == -32603
    assert "Insufficient permission" in error["message"]
