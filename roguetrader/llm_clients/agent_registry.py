"""Agent-level LLM and prompt profile registry."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

import yaml

from roguetrader.llm_clients.factory import create_llm_client


DEFAULT_AGENT_CONFIG_PATH = Path("configs/agents.yaml")
PACKAGE_AGENT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "configs" / "agents.yaml"


class AgentLLMRegistry:
    """Load agent YAML config and provide per-agent LLMs and prompt profiles."""

    def __init__(self, config: Dict[str, Any], llm_kwargs: Dict[str, Any] | None = None):
        self.config = config
        self.llm_kwargs = llm_kwargs or {}
        self.agent_config = self._load_agent_config()
        self._llm_cache: dict[tuple[Any, ...], Any] = {}

    def _load_agent_config(self) -> Dict[str, Any]:
        configured_path = self.config.get("agent_config_path") or os.getenv("ROGUETRADER_AGENT_CONFIG")
        if configured_path:
            path = Path(configured_path)
        else:
            path = DEFAULT_AGENT_CONFIG_PATH
            if not path.exists():
                path = PACKAGE_AGENT_CONFIG_PATH
        if not path.exists():
            return {"defaults": {}, "agents": {}}

        try:
            with path.open("r", encoding="utf-8") as f:
                loaded = yaml.safe_load(f) or {}
        except yaml.YAMLError as exc:
            raise ValueError(f"Invalid agent YAML config: {path}: {exc}") from exc

        if not isinstance(loaded, dict):
            raise ValueError(f"Agent YAML config must be a mapping: {path}")

        agents = loaded.get("agents", {})
        if agents is not None and not isinstance(agents, dict):
            raise ValueError(f"'agents' must be a mapping in agent YAML config: {path}")

        defaults = loaded.get("defaults", {})
        if defaults is not None and not isinstance(defaults, dict):
            raise ValueError(f"'defaults' must be a mapping in agent YAML config: {path}")

        return {"defaults": defaults or {}, "agents": agents or {}}

    def _agent_entry(self, agent_id: str) -> Dict[str, Any]:
        entry = self.agent_config.get("agents", {}).get(agent_id, {})
        return entry if isinstance(entry, dict) else {}

    def _agent_llm_config(self, agent_id: str) -> Dict[str, Any]:
        llm_config = self._agent_entry(agent_id).get("llm", {})
        return llm_config if isinstance(llm_config, dict) else {}

    def _agent_prompt_config(self, agent_id: str) -> Dict[str, Any]:
        prompt_config = self._agent_entry(agent_id).get("prompt", {})
        return prompt_config if isinstance(prompt_config, dict) else {}

    def get_llm(self, agent_id: str, default_tier: str = "quick"):
        defaults = self.agent_config.get("defaults", {})
        llm_config = self._agent_llm_config(agent_id)
        tier = str(llm_config.get("tier") or default_tier or "quick").lower()

        provider = (
            llm_config.get("provider")
            or self.config.get("llm_provider")
            or defaults.get("provider")
        )
        backend_url = (
            llm_config.get("backend_url")
            or self.config.get("backend_url")
            or defaults.get("backend_url")
        )
        if tier == "deep":
            model = (
                llm_config.get("model")
                or self.config.get("deep_think_llm")
                or defaults.get("deep_model")
            )
        else:
            model = (
                llm_config.get("model")
                or self.config.get("quick_think_llm")
                or defaults.get("quick_model")
            )

        key = (
            str(provider).lower(),
            str(model),
            str(backend_url or ""),
            tuple(sorted((k, repr(v)) for k, v in self.llm_kwargs.items())),
        )
        if key not in self._llm_cache:
            client = create_llm_client(
                provider=str(provider),
                model=str(model),
                base_url=backend_url,
                **self.llm_kwargs,
            )
            self._llm_cache[key] = client.get_llm()
        return self._llm_cache[key]

    def render_prompt(
        self,
        agent_id: str,
        default_identity: str,
        default_focus: str,
        default_style: str,
        body: str,
    ) -> str:
        prompt_config = self._agent_prompt_config(agent_id)
        identity = prompt_config.get("identity") or default_identity
        focus = prompt_config.get("focus") or default_focus
        style = prompt_config.get("style") or default_style
        return (
            f"# Agent Identity\n{str(identity).strip()}\n\n"
            f"# Focus\n{str(focus).strip()}\n\n"
            f"# Response Style\n{str(style).strip()}\n\n"
            f"{body.strip()}"
        )
