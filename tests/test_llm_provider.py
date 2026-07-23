"""Tests for direct LLM provider routing."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from arkiv.core.llm import _detect_provider, completion


def test_detect_provider_recognizes_huggingface_prefix() -> None:
    assert _detect_provider("huggingface:openai/gpt-oss-20b", None) == "huggingface"
    assert _detect_provider("huggingface/openai/gpt-oss-20b:fastest", None) == "huggingface"


def test_huggingface_uses_router_default_and_hf_token(monkeypatch) -> None:
    monkeypatch.setenv("HF_TOKEN", "hf-test-token")
    response = MagicMock()
    response.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
    response.raise_for_status.return_value = None

    with patch("arkiv.core.llm.httpx.post", return_value=response) as post:
        result = completion(
            model="huggingface/openai/gpt-oss-20b:fastest",
            messages=[{"role": "user", "content": "Hello"}],
        )

    assert result.choices[0].message.content == "ok"
    url = post.call_args.args[0]
    headers = post.call_args.kwargs["headers"]
    body = post.call_args.kwargs["json"]
    assert url == "https://router.huggingface.co/v1/chat/completions"
    assert headers["Authorization"] == "Bearer hf-test-token"
    assert body["model"] == "openai/gpt-oss-20b:fastest"


def _mock_response(payload: dict) -> MagicMock:
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


def test_detect_provider_prefixes_beat_ollama_url() -> None:
    assert (
        _detect_provider("huggingface/openai/gpt-oss-20b", "http://localhost:11434")
        == "huggingface"
    )
    assert _detect_provider("anthropic/claude-sonnet-4-5", "http://localhost:11434") == "anthropic"
    assert _detect_provider("claude-sonnet-4-5", None) == "anthropic"


def test_detect_provider_ollama_only_for_local_endpoint() -> None:
    assert _detect_provider("qwen2.5:7b", "http://localhost:11434") == "ollama"
    assert _detect_provider("qwen2.5:7b", "http://127.0.0.1:11434") == "ollama"
    # Fremder Server auf Port 11434 ist KEIN Beleg für Ollama
    assert _detect_provider("gpt-4o-mini", "https://llm.example.com:11434/v1") == "openai"
    assert _detect_provider("gpt-4o-mini", None) == "openai"


def test_anthropic_strips_prefix_and_reads_env_key(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env")
    response = _mock_response({"content": [{"text": "ok"}]})

    with patch("arkiv.core.llm.httpx.post", return_value=response) as post:
        completion(
            model="anthropic/claude-sonnet-4-5",
            messages=[{"role": "user", "content": "Hello"}],
        )

    assert post.call_args.kwargs["json"]["model"] == "claude-sonnet-4-5"
    assert post.call_args.kwargs["headers"]["x-api-key"] == "sk-ant-env"


def test_anthropic_explicit_key_beats_env(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env")
    response = _mock_response({"content": [{"text": "ok"}]})

    with patch("arkiv.core.llm.httpx.post", return_value=response) as post:
        completion(
            model="claude-sonnet-4-5",
            messages=[{"role": "user", "content": "Hello"}],
            api_key="sk-ant-explicit",
        )

    assert post.call_args.kwargs["headers"]["x-api-key"] == "sk-ant-explicit"


def test_openai_reads_env_key_without_api_base(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    response = _mock_response({"choices": [{"message": {"content": "ok"}}]})

    with patch("arkiv.core.llm.httpx.post", return_value=response) as post:
        completion(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "Hello"}],
        )

    assert post.call_args.args[0] == "https://api.openai.com/v1/chat/completions"
    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer sk-env"


def test_openai_env_key_not_leaked_to_custom_api_base(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    response = _mock_response({"choices": [{"message": {"content": "ok"}}]})

    with patch("arkiv.core.llm.httpx.post", return_value=response) as post:
        completion(
            model="local-model",
            messages=[{"role": "user", "content": "Hello"}],
            api_base="http://localhost:1234/v1",
        )

    assert "Authorization" not in post.call_args.kwargs["headers"]


def test_openai_explicit_key_beats_env(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    response = _mock_response({"choices": [{"message": {"content": "ok"}}]})

    with patch("arkiv.core.llm.httpx.post", return_value=response) as post:
        completion(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "Hello"}],
            api_key="sk-explicit",
        )

    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer sk-explicit"


def test_llm_config_default_base_url_is_none() -> None:
    from arkiv.core.config import LLMConfig

    assert LLMConfig().base_url is None


def _write_cloud_config(tmp_path, provider: str, model: str):
    # Wizard-artiges Cloud-TOML: absichtlich OHNE base_url-Zeile
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        f'[llm]\nprovider = "{provider}"\nmodel = "{model}"\n\n'
        f'[database]\npath = "{tmp_path / "test.db"}"\n'
        f'inbox_dir = "{tmp_path / "inbox"}"\n'
        f'review_dir = "{tmp_path / "review"}"\n'
    )
    return config_file


def test_openai_wizard_config_reaches_openai_not_ollama(tmp_path, monkeypatch) -> None:
    from arkiv.core.classifier import Classifier
    from arkiv.core.config import ArkivConfig

    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    cfg = ArkivConfig.load(_write_cloud_config(tmp_path, "openai", "gpt-4o-mini"))
    classification = (
        '{"category": "notiz", "confidence": 0.9, "summary": "s", "tags": [], "language": "de"}'
    )
    response = _mock_response({"choices": [{"message": {"content": classification}}]})

    with patch("arkiv.core.llm.httpx.post", return_value=response) as post:
        result = Classifier(cfg.llm, cfg).classify("Ein Notiztext")

    assert post.call_args.args[0] == "https://api.openai.com/v1/chat/completions"
    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer sk-env"
    assert result.category == "notiz"


def test_anthropic_wizard_config_sends_unprefixed_model(tmp_path, monkeypatch) -> None:
    from arkiv.core.classifier import Classifier
    from arkiv.core.config import ArkivConfig

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env")
    cfg = ArkivConfig.load(_write_cloud_config(tmp_path, "anthropic", "claude-sonnet-4-5"))
    classification = (
        '{"category": "notiz", "confidence": 0.9, "summary": "s", "tags": [], "language": "de"}'
    )
    response = _mock_response({"content": [{"text": classification}]})

    with patch("arkiv.core.llm.httpx.post", return_value=response) as post:
        result = Classifier(cfg.llm, cfg).classify("Ein Notiztext")

    assert post.call_args.args[0] == "https://api.anthropic.com/v1/messages"
    assert post.call_args.kwargs["json"]["model"] == "claude-sonnet-4-5"
    assert post.call_args.kwargs["headers"]["x-api-key"] == "sk-ant-env"
    assert result.category == "notiz"
