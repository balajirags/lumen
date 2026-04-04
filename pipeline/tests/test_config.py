from __future__ import annotations

from pathlib import Path

from codedoc.config import load_config


def test_load_config_defaults_and_cli_override(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    cfg = load_config({"max_turns": 25, "output_dir": "/tmp/out"})

    assert cfg.max_turns == 25
    assert cfg.output_dir == "/tmp/out"
    assert cfg.provider == "auto"
    assert cfg.repo_size_check == "warn"


def test_load_config_reads_toml(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path(".codedoc.toml").write_text(
        """
[pipeline]
max_turns = 45
provider = "openai"
repo_size_check = "strict"

[paths]
output_dir = "./custom-output"
""".strip()
    )

    cfg = load_config()

    assert cfg.max_turns == 45
    assert cfg.provider == "openai"
    assert cfg.output_dir == "./custom-output"
    assert cfg.repo_size_check == "strict"
