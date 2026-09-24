"""Unit tests for configuration loading and validation."""

from pathlib import Path
from orchestrator.config import get_default_config, load_config, save_config


def test_default_config_structure():
    cfg = get_default_config()
    assert "strategic" in cfg.model_profiles
    assert "implementation" in cfg.model_profiles
    assert "adversarial" in cfg.model_profiles
    assert "lightweight" in cfg.model_profiles

    assert "decision_maker" in cfg.roles
    assert "developer" in cfg.roles
    assert "code_reviewer" in cfg.roles
    assert "tester" in cfg.roles

    assert cfg.roles["decision_maker"].agent == "codex"
    assert cfg.roles["developer"].agent == "agy"

    assert "default_review_dev_loop" in cfg.workflows
    wf = cfg.workflows["default_review_dev_loop"]
    assert wf.version == 1
    assert "strategy" in wf.stages
    assert "implementation" in wf.stages
    assert "review" in wf.stages
    assert "final_review" in wf.stages


def test_config_save_and_load(tmp_path: Path):
    cfg_file = tmp_path / "orchestrator.yaml"
    cfg = get_default_config()
    save_config(cfg, cfg_file)

    assert cfg_file.exists()
    loaded = load_config(cfg_file)

    assert loaded.active_workflow == cfg.active_workflow
    assert len(loaded.roles) == len(cfg.roles)
    assert len(loaded.model_profiles) == len(cfg.model_profiles)
