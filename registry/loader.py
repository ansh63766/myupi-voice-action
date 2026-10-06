"""
registry/loader.py — Loads and validates registry.yaml.
Registry is the single source of truth for all actions.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, PrivateAttr, field_validator, model_validator

ROOT = Path(__file__).parent.parent

RiskTier = Literal[0, 1, 2, "2M"]
ExecutionMode = Literal[
    "API_DIRECT",
    "API_WITH_CONFIRMATION",
    "DEEP_LINK",
    "DEEP_LINK_THEN_PIN",
]


class ActionEntry(BaseModel):
    action_id: str
    intent_labels: list[str]
    required_slots: list[str]
    optional_slots: list[str] = Field(default_factory=list)
    disambiguation_source: str | None
    risk_tier: RiskTier
    execution_mode: ExecutionMode
    deep_link_template: str | None
    confirmation_template_id: str | None
    voice_safe: bool
    version: str
    enabled: bool = True

    @model_validator(mode="after")
    def check_consistency(self) -> "ActionEntry":
        # Tier 2/2M must have a deep link or confirmation
        if self.risk_tier in (2, "2M"):
            if self.execution_mode not in ("DEEP_LINK", "DEEP_LINK_THEN_PIN"):
                raise ValueError(
                    f"Action {self.action_id}: Tier 2/2M must use DEEP_LINK or DEEP_LINK_THEN_PIN"
                )
        # Tier 1 must have a confirmation template
        if self.risk_tier == 1 and not self.confirmation_template_id:
            raise ValueError(
                f"Action {self.action_id}: Tier 1 must have a confirmation_template_id"
            )
        return self


class Registry(BaseModel):
    schema_version: str
    actions: list[ActionEntry]

    # Cached lookup maps (built after validation)
    _intent_map: dict[str, ActionEntry] = PrivateAttr(default_factory=dict)
    _action_map: dict[str, ActionEntry] = PrivateAttr(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        self._intent_map = {}
        self._action_map = {}
        for action in self.actions:
            if not action.enabled:
                continue
            self._action_map[action.action_id] = action
            for label in action.intent_labels:
                if label in self._intent_map:
                    raise ValueError(
                        f"Duplicate intent label '{label}' in actions "
                        f"'{self._intent_map[label].action_id}' and '{action.action_id}'"
                    )
                self._intent_map[label] = action

    def lookup_by_intent(self, intent_label: str) -> ActionEntry | None:
        """Intent label → ActionEntry. Returns None if not found or disabled."""
        return self._intent_map.get(intent_label)

    def lookup_by_action_id(self, action_id: str) -> ActionEntry | None:
        return self._action_map.get(action_id)

    def all_intent_labels(self) -> list[str]:
        return list(self._intent_map.keys())

    def enabled_actions(self) -> list[ActionEntry]:
        return [a for a in self.actions if a.enabled]


_registry: Registry | None = None


def load_registry(registry_path: Path | None = None) -> Registry:
    if registry_path is None:
        registry_path = ROOT / "registry" / "registry.yaml"
    with open(registry_path) as f:
        raw = yaml.safe_load(f)
    return Registry.model_validate(raw)


def get_registry() -> Registry:
    global _registry
    if _registry is None:
        _registry = load_registry()
    return _registry


def reset_registry() -> None:
    global _registry
    _registry = None
