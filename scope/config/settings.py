"""
配置管理模块
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class ConfigManager:
    """配置管理器 — 保存/加载用户配置"""

    _default_dir = Path.home() / ".digital_scope"
    _project_default_path = (
        Path(__file__).resolve().parents[2] / "config" / "default_config.json"
    )

    @staticmethod
    def default_filepath() -> str:
        """返回默认配置文件路径"""
        ConfigManager._default_dir.mkdir(parents=True, exist_ok=True)
        return str(ConfigManager._default_dir / "config.json")

    @staticmethod
    def project_default_filepath() -> str:
        """返回项目内默认配置文件路径。"""
        return str(ConfigManager._project_default_path)

    @staticmethod
    def load_json(filepath: str | Path) -> dict:
        """读取 JSON 配置文件。"""
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)

    @staticmethod
    def load_project_default_config() -> dict:
        """读取项目默认配置；文件不存在时返回空 dict。"""
        path = ConfigManager._project_default_path
        if not path.exists():
            logger.info(f"项目默认配置不存在: {path}")
            return {}
        try:
            return ConfigManager.load_json(path)
        except Exception as e:
            logger.warning(f"读取项目默认配置失败: {e}")
            return {}

    @staticmethod
    def load_default_measurements() -> list[dict]:
        """读取项目默认测量项配置。"""
        config = ConfigManager.load_project_default_config()
        measurements = config.get("measurements", [])
        if not isinstance(measurements, list):
            logger.warning("项目默认配置 measurements 字段不是 list")
            return []
        return measurements

    @staticmethod
    def save_to_file(main_window, filepath: str) -> bool:
        """
        保存主窗口配置到 JSON 文件。
        
        Args:
            main_window: MainWindow 实例
            filepath: 保存路径
        """
        try:
            config = {}

            # 保存通道配置
            if hasattr(main_window, 'channel_panel'):
                config['channels'] = main_window.channel_panel.get_config()

            # 保存设备配置
            if hasattr(main_window, 'device_panel'):
                config['device'] = main_window.device_panel.get_state()

            # 保存测量配置
            if hasattr(main_window, 'measure_panel'):
                config['measurements'] = main_window.measure_panel.get_measurement_specs()

            # 保存反馈配置
            if hasattr(main_window, '_feedback_mgr'):
                config['feedback_workers'] = main_window._feedback_mgr.get_config()

            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)

            logger.info(f"配置已保存到 {filepath}")
            return True

        except Exception as e:
            logger.error(f"保存配置失败: {e}")
            return False

    @staticmethod
    def load_from_file(main_window, filepath: str) -> dict | bool:
        """
        从 JSON 文件加载配置到主窗口。

        回填 UI 后，返回设备参数和反馈配置列表，
        供调用方自行决定是否发布 EventBus 控制面命令。

        Args:
            main_window: MainWindow 实例
            filepath: 配置文件路径

        Returns:
            False — 加载失败
            dict — 加载成功，含可选字段:
                  {"device": {"params": dict, "config": DeviceConfig} | None,
                   "feedback_workers": list[dict] | None}
        """
        try:
            config = ConfigManager.load_json(filepath)
            payload: dict = {"device": None, "feedback_workers": None}

            # 加载通道配置
            if 'channels' in config and hasattr(main_window, 'channel_panel'):
                main_window.channel_panel.set_config(config['channels'])

            # 加载设备配置（回填 UI）
            if 'device' in config and hasattr(main_window, 'device_panel'):
                main_window.device_panel.set_config(config['device'])
                # 从面板读取参数，供调用方发布 config.change
                payload["device"] = {
                    "params": main_window.device_panel.get_params(),
                    "config": main_window.device_panel.get_config(),
                }

            # 加载测量配置
            if 'measurements' in config and hasattr(main_window, 'measure_panel'):
                main_window.measure_panel.set_config(config['measurements'])

            # 加载反馈配置（不再直接 call_threadsafe，交由调用方走 EventBus）
            if 'feedback_workers' in config:
                payload["feedback_workers"] = config["feedback_workers"]

            logger.info(f"配置已从 {filepath} 加载")
            return payload

        except FileNotFoundError:
            logger.warning(f"配置文件不存在: {filepath}")
            return False
        except Exception as e:
            logger.error(f"加载配置失败: {e}")
            return False
