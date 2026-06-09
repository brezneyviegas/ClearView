import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class ClassifierCfg(BaseModel):
    enabled: bool = True
    model: str
    prompt: str
    score_to_tier: dict[int, str]
    confidence_floor: float = 0.65


class EscalationCfg(BaseModel):
    on_error: bool = True
    on_empty_response: bool = True
    max_retries: int = 1


class BudgetCfg(BaseModel):
    daily_usd_cap: float = 50.0
    on_breach: str = "reject"


class StagesCfg(BaseModel):
    """Plan/execute workflow routing: plan turns go to a strong tier, execution
    turns (agent loops with tool results) go to a local/cheap tier."""
    enabled: bool = False
    plan: str = "frontier"
    execute: str = "local"
    auto_detect: bool = True


class Policy(BaseModel):
    tiers: dict[str, list[str]]
    rules: list[dict[str, Any]] = Field(default_factory=list)
    classifier: ClassifierCfg
    escalation: EscalationCfg = EscalationCfg()
    budget: BudgetCfg = BudgetCfg()
    stages: StagesCfg = StagesCfg()
    baseline_model: str


def load_policy(path: str | None = None) -> Policy:
    p = Path(path or os.environ.get("CLEARVIEW_POLICY_PATH", "./policy.yaml"))
    if not p.exists():
        raise FileNotFoundError(f"policy not found at {p}")
    data = yaml.safe_load(p.read_text())
    return Policy(**data)


def db_path() -> str:
    return os.environ.get("CLEARVIEW_DB_PATH", "./clearview.db")


def baseline_model_env() -> str | None:
    return os.environ.get("CLEARVIEW_BASELINE_MODEL")
