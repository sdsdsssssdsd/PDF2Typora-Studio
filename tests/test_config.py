"""Config loading tests."""

from config.config_manager import load_config, project_root


def test_project_root_exists():
    assert project_root().is_dir()


def test_load_config_has_required_keys():
    cfg = load_config()
    assert "workspace" in cfg
    assert "pdf" in cfg
    assert "ollama" in cfg
    assert "ai" in cfg
    assert cfg["pdf"]["dpi"] == 200
    assert cfg["pdf"]["max_render_workers"] == 1
