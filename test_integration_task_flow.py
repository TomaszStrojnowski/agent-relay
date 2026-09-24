"""Integration test for the first acceptance scenario in SPEC.md.

Two agents register, one sends a task, the other claims it and returns a
result.  The test asserts the status the *sender* observes at each stage:
``queued`` -> ``processing`` -> ``completed``.

Two modes, chosen by environment:

``RELAY_BASE_URL`` set
    Run against a live server over real HTTP -- the containerized API, the
    Compose stack, or a pod reached through ``kubectl port-forward``.  The
    database is whatever that deployment is using; the test does not reset it.

``RELAY_BASE_URL`` unset
    Run in-process against the real FastAPI app and the real database that
    ``RELAY_DATABASE_URL`` points at, resetting the schema around each test.

Both modes exercise real routes, real authentication and real storage.  Only
the transport differs, so the same test covers SQLite locally and PostgreSQL
in Compose.
"""

from __future__ import annotations

import os

# Match test_agent_relay.py: never touch the dev server's ./agent-relay.db.
os.environ.setdefault("RELAY_DATABASE_URL", "sqlite:///./integration-test.db")

from typing import Iterator

import pytest

BASE_URL = os.getenv("RELAY_BASE_URL")


@pytest.fixture
def client() -> Iterator[object]:
    if BASE_URL:
        import httpx

        with httpx.Client(base_url=BASE_URL.rstrip("/"), timeout=30.0) as live:
            yield live
        return

    from fastapi.testclient import TestClient

    import main
    from database import Base, engine

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with TestClient(main.app) as local:
        yield local
    Base.metadata.drop_all(engine)


def register(client, name: str) -> tuple[str, dict[str, str]]:
    response = client.post("/api/v1/agents", json={"name": name})
    assert response.status_code == 201, response.text
    body = response.json()
    return body["agent_id"], {"Authorization": f"Bearer {body['token']}"}


def status_seen_by(client, task_id: str, headers: dict[str, str]) -> str:
    response = client.get(f"/api/v1/tasks/{task_id}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["status"]


def test_two_agents_exchange_a_task_and_its_result(client):
    sender_id, sender = register(client, "alice")
    recipient_id, recipient = register(client, "uppercase")

    # The sender addresses the recipient by agent id, never by name.
    created = client.post(
        "/api/v1/tasks",
        json={"to": recipient_id, "input": "hello agent relay"},
        headers=sender,
    )
    assert created.status_code == 201, created.text
    task_id = created.json()["task_id"]
    assert created.json()["status"] == "queued"

    claimed = client.post(
        "/api/v1/tasks/claim",
        json={"worker_id": "integration-test", "wait_seconds": 5},
        headers=recipient,
    )
    assert claimed.status_code == 200, claimed.text
    claim = claimed.json()
    assert claim["task_id"] == task_id
    assert claim["from"] == sender_id
    assert claim["input"] == "hello agent relay"
    assert claim["attempt"] == 1

    # Claiming is what moves the task out of the queue, so the sender sees the
    # change before any result exists.
    assert status_seen_by(client, task_id, sender) == "processing"

    completed = client.post(
        f"/api/v1/tasks/{task_id}/complete",
        json={"claim_token": claim["claim_token"], "output": "HELLO AGENT RELAY"},
        headers=recipient,
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "completed"

    assert status_seen_by(client, task_id, sender) == "completed"

    final = client.get(f"/api/v1/tasks/{task_id}", headers=sender)
    assert final.json()["output"] == "HELLO AGENT RELAY"


def test_an_unrelated_agent_cannot_read_the_task(client):
    _, sender = register(client, "alice")
    recipient_id, _ = register(client, "uppercase")
    _, stranger = register(client, "mallory")

    created = client.post(
        "/api/v1/tasks",
        json={"to": recipient_id, "input": "private"},
        headers=sender,
    )
    task_id = created.json()["task_id"]

    # 404, not 403: an inaccessible task must not be distinguishable from one
    # that does not exist.
    assert client.get(f"/api/v1/tasks/{task_id}", headers=stranger).status_code == 404
