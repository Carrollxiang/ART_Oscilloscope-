"""
配置管理模块
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class ConfigManager:
    """配置管理器 — 保存/加载用户配置"""

    _default_dir = Path.home() / ".digital_scope"

    @staticmethod
    def default_filepath() -> str:
        """返回默认配置文件路径"""
        ConfigManager._default_dir.mkdir(parents=True, exist_ok=True)
        return str(ConfigManager._default_dir / "config.json")

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
                config['device'] = main_window.device_panel.get_config()

            # 保存测量配置
            if hasattr(main_window, 'measure_panel'):
                config['measurements'] = main_window.measure_panel.get_measurement_specs()

            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)

            logger.info(f"配置已保存到 {filepath}")
            return True

        except Exception as e:
            logger.error(f"保存配置失败: {e}")
            return False

    @staticmethod
    def load_from_file(main_window, filepath: str) -> bool:
        """
        从 JSON 文件加载配置到主窗口。
        
        Args:
            main_window: MainWindow 实例
            filepath: 配置文件路径
        """
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                config = json.load(f)

            # 加载通道配置
            if 'channels' in config and hasattr(main_window, 'channel_panel'):
                main_window.channel_panel.set_config(config['channels'])

            # 加载设备配置
            if 'device' in config and hasattr(main_window, 'device_panel'):
                main_window.device_panel.set_config(config['device'])

            logger.info(f"配置已从 {filepath} 加载")
            return True

        except FileNotFoundError:
            logger.warning(f"配置文件不存在: {filepath}")
            return False
        except Exception as e:
            logger.error(f"加载配置失败: {e}")
            return False
