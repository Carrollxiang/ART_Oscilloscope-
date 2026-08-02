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


def test_project_default_config_has_feedback_workers():
    """项目默认配置应包含合法的 feedback_workers 段 (启动自动加载依赖它)。"""
    config = ConfigManager.load_project_default_config()
    fw = config.get("feedback_workers")
    assert fw, "default_config.json 应包含 feedback_workers"
    assert isinstance(fw, list) and len(fw) >= 1
    w = fw[0]
    assert w.get("worker_id"), "worker_id 不能为空"
    assert w.get("measurement_key"), "measurement_key 不能为空"
    assert w.get("pid_config"), "pid_config 不能为空"
    # target 结构 (Ad9910Target 序列化)
    target = w.get("target")
    if target:
        assert target.get("type") in ("Ad9910Target", "RtmqTarget")
        assert target.get("ip") and target.get("port")


def test_load_project_default_config_fallback(tmp_path, monkeypatch):
    """用户配置不存在时, 应能回退到项目默认配置 (启动自动加载路径)。"""
    monkeypatch.setattr(
        ConfigManager,
        "_default_dir",
        tmp_path / "no_user_config",   # 目录不存在 → default_filepath 会创建但文件不存在
    )
    config_path = tmp_path / "default_config.json"
    config_path.write_text(
        json.dumps({"feedback_workers": [{"worker_id": "w0", "measurement_key": "m18"}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(ConfigManager, "_project_default_path", config_path)

    from pathlib import Path
    user_path = Path(ConfigManager.default_filepath())
    assert not user_path.exists(), "前置: 用户配置不存在"
    # 模拟 main.py 的回退逻辑
    if user_path.exists():
        config = ConfigManager.load_json(user_path)
    else:
        config = ConfigManager.load_project_default_config()
    fw = config.get("feedback_workers")
    if not fw:
        fw = ConfigManager.load_project_default_config().get("feedback_workers")
    assert len(fw) == 1
    assert fw[0]["worker_id"] == "w0"
