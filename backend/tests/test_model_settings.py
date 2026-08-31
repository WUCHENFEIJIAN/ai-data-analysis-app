def test_model_preset_catalog_and_runtime_switch(client) -> None:
    presets = client.get("/api/settings/models")
    assert presets.status_code == 200
    assert {item["id"] for item in presets.json()["items"]} >= {
        "chatgpt",
        "claude",
        "deepseek",
        "qwen",
        "zhipu",
        "custom",
    }

    updated = client.put(
        "/api/settings/model",
        json={
            "preset_id": "deepseek",
            "api_key": "secret-test-key",
        },
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["preset_id"] == "deepseek"
    assert body["model"] == "deepseek-chat"
    assert body["api_key_configured"] is True
    assert body["api_key_hint"] == "••••-key"

    current = client.get("/api/settings/model")
    assert current.status_code == 200
    assert current.json()["api_key_hint"] == "••••-key"
    assert "secret-test-key" not in current.text


def test_model_connection_test_uses_form_values_without_saving(client, monkeypatch) -> None:
    from app.api.routes import settings as settings_route

    class FakeProvider:
        async def text_chat(self, messages):
            assert messages == [{"role": "user", "content": "Reply with OK only."}]
            return "OK"

    monkeypatch.setattr(settings_route, "provider_from_record", lambda record, settings: FakeProvider())

    response = client.post(
        "/api/settings/model/test",
        json={
            "preset_id": "deepseek",
            "api_key": "temporary-test-key",
            "model": "deepseek-chat",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["model"] == "deepseek-chat"
    assert body["latency_ms"] >= 0

    assert client.get("/api/settings/model").json() is None


def test_model_connection_test_requires_credentials(client) -> None:
    response = client.post("/api/settings/model/test", json={"preset_id": "deepseek"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "model_test_not_configured"
