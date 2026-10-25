"""Chat model factory for the AI BPMN assistant.

Ollama is the default. Other LangChain chat model integrations are loaded lazily, so only the
package for the configured provider needs to be installed.
"""

from __future__ import annotations

import importlib
import json
from dataclasses import dataclass
from dataclasses import field
from typing import Any

from flask import current_app


class AiBpmnConfigurationError(Exception):
    pass


@dataclass(frozen=True)
class ProviderSpec:
    module: str
    class_name: str
    package: str
    # keyword names used by the integration
    base_url_kwarg: str | None = "base_url"
    api_key_kwarg: str | None = "api_key"
    timeout_kwarg: str | None = "timeout"


PROVIDERS: dict[str, ProviderSpec] = {
    "ollama": ProviderSpec("langchain_ollama", "ChatOllama", "langchain-ollama", api_key_kwarg=None, timeout_kwarg=None),
    "openai": ProviderSpec("langchain_openai", "ChatOpenAI", "langchain-openai"),
    "anthropic": ProviderSpec("langchain_anthropic", "ChatAnthropic", "langchain-anthropic"),
    "google_genai": ProviderSpec("langchain_google_genai", "ChatGoogleGenerativeAI", "langchain-google-genai"),
    "mistralai": ProviderSpec("langchain_mistralai", "ChatMistralAI", "langchain-mistralai", base_url_kwarg="endpoint"),
    "groq": ProviderSpec("langchain_groq", "ChatGroq", "langchain-groq"),
}
PROVIDER_ALIASES = {"claude": "anthropic", "gemini": "google_genai", "google": "google_genai", "mistral": "mistralai"}


@dataclass(frozen=True)
class AiBpmnSettings:
    provider: str = "ollama"
    model: str = "gemma4:12b-mlx"
    base_url: str | None = None
    api_key: str | None = None
    temperature: float = 0.2
    timeout_seconds: int = 180
    max_repair_attempts: int = 2
    max_history_messages: int = 6
    model_kwargs: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_app_config(cls) -> AiBpmnSettings:
        config = current_app.config
        raw_kwargs = config.get("SPIFFWORKFLOW_BACKEND_AI_BPMN_MODEL_KWARGS") or "{}"
        try:
            model_kwargs = json.loads(raw_kwargs) if isinstance(raw_kwargs, str) else dict(raw_kwargs)
        except json.JSONDecodeError as exception:
            raise AiBpmnConfigurationError(
                f"SPIFFWORKFLOW_BACKEND_AI_BPMN_MODEL_KWARGS is not valid JSON: {exception}"
            ) from exception
        if not isinstance(model_kwargs, dict):
            raise AiBpmnConfigurationError("SPIFFWORKFLOW_BACKEND_AI_BPMN_MODEL_KWARGS must be a JSON object")
        return cls(
            provider=str(config.get("SPIFFWORKFLOW_BACKEND_AI_BPMN_PROVIDER") or "ollama"),
            model=str(config.get("SPIFFWORKFLOW_BACKEND_AI_BPMN_MODEL") or "gemma4:12b-mlx"),
            base_url=config.get("SPIFFWORKFLOW_BACKEND_AI_BPMN_BASE_URL"),
            api_key=config.get("SPIFFWORKFLOW_BACKEND_AI_BPMN_API_KEY"),
            temperature=float(config.get("SPIFFWORKFLOW_BACKEND_AI_BPMN_TEMPERATURE") or 0.2),
            timeout_seconds=int(config.get("SPIFFWORKFLOW_BACKEND_AI_BPMN_TIMEOUT_SECONDS") or 180),
            max_repair_attempts=int(config.get("SPIFFWORKFLOW_BACKEND_AI_BPMN_MAX_REPAIR_ATTEMPTS") or 0),
            max_history_messages=int(config.get("SPIFFWORKFLOW_BACKEND_AI_BPMN_MAX_HISTORY_MESSAGES") or 0),
            model_kwargs=model_kwargs,
        )


def _load_class(module_name: str, class_name: str, package: str) -> Any:
    try:
        module = importlib.import_module(module_name)
    except ImportError as exception:
        raise AiBpmnConfigurationError(
            f"The AI BPMN provider requires the '{package}' package. "
            f"Install it in the backend environment, e.g. `uv pip install {package}`."
        ) from exception
    model_class = getattr(module, class_name, None)
    if model_class is None:
        raise AiBpmnConfigurationError(f"'{module_name}' has no class '{class_name}'")
    return model_class


def create_chat_model(settings: AiBpmnSettings) -> Any:
    """Build a LangChain chat model for the configured provider."""
    provider = PROVIDER_ALIASES.get(settings.provider.lower(), settings.provider.lower())
    kwargs: dict[str, Any] = {"model": settings.model, "temperature": settings.temperature}

    if ":" in settings.provider and provider not in PROVIDERS:
        module_name, class_name = settings.provider.split(":", 1)
        model_class = _load_class(module_name, class_name, module_name.split(".")[0].replace("_", "-"))
        if settings.base_url:
            kwargs["base_url"] = settings.base_url
        if settings.api_key:
            kwargs["api_key"] = settings.api_key
        return model_class(**{**kwargs, **settings.model_kwargs})

    spec = PROVIDERS.get(provider)
    if spec is None:
        raise AiBpmnConfigurationError(
            f"Unknown AI BPMN provider '{settings.provider}'. Use one of {', '.join(PROVIDERS)} or 'module:ChatModelClass'."
        )
    model_class = _load_class(spec.module, spec.class_name, spec.package)
    if settings.base_url and spec.base_url_kwarg:
        kwargs[spec.base_url_kwarg] = settings.base_url
    if settings.api_key and spec.api_key_kwarg:
        kwargs[spec.api_key_kwarg] = settings.api_key
    if spec.timeout_kwarg:
        kwargs[spec.timeout_kwarg] = settings.timeout_seconds
    if provider == "ollama":
        kwargs["client_kwargs"] = {"timeout": settings.timeout_seconds}
        # "thinking" models are many times slower and the edit ops do not need long reasoning
        kwargs["reasoning"] = False
    return model_class(**{**kwargs, **settings.model_kwargs})
