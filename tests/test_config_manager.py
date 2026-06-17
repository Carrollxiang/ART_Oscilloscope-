"""
ConfigManager 单元测试
"""

import json

from scope.config.settings import ConfigManager


def test_load_default_measurements_from_project_config(tmp_path, monkeypatch):
    config_path = tmp_path / "default_config.json"
    config_path.write_text(
        json.dumps({
            "measurements": [
                {
                    "tag": "m3",
                    "name": "default measurement",
                    "channel": 1,
                    "feature": "Mean",
                    "start_ms": 21.0,
                    "end_ms": 29.0,
                }
            ]
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(ConfigManager, "_project_default_path", config_path)

    measurements = ConfigManager.load_default_measurements()

    assert len(measurements) == 1
    assert measurements[0]["tag"] == "m3"
    assert measurements[0]["name"] == "default measurement"


def test_load_default_measurements_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(
        ConfigManager,
        "_project_default_path",
        tmp_path / "missing.json",
    )

    assert ConfigManager.load_default_measurements() == []
