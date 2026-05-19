"""
配置管理器 — 保存/加载示波器配置 (JSON 格式)

管理以下状态:
  - ART 设备参数 (DevicePanel)
  - 通道面板状态 (ChannelPanel)
  - 测量行配置 (MeasurementPanel)
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, asdict
from typing import Optional

logger = logging.getLogger(__name__)

CONFIG_VERSION = 1


@dataclass
class ScopeConfig:
    """完整的示波器配置快照"""
    version: int = CONFIG_VERSION

    # 设备参数
    device: dict = field(default_factory=lambda: {
        "device_name": "Dev42",
        "ai_channels": "ai0:15",
        "terminal_config": "NRSE",
        "read_timeout": 5.0,
        "sample_rate": 30000,
        "duration": 0.5,
        "sample_mode": "FINITE",
        "trigger_source": "ai12",
        "trigger_slope": "rising",
        "trigger_level": 1.0,
    })

    # 通道状态
    channels: dict = field(default_factory=lambda: {
        "enabled": [True] * 16,
        "min_vals": [-10.0] * 16,
        "max_vals": [10.0] * 16,
    })

    # 测量行
    measurements: list[dict] = field(default_factory=lambda: [
        {"name": "CH1 幅值", "channel": "CH1", "meas_key": "Vpp",
         "start": 0.0, "end": 500.0},
        {"name": "CH1 频率", "channel": "CH1", "meas_key": "Freq",
         "start": 0.0, "end": 500.0},
        {"name": "CH2 幅值", "channel": "CH2", "meas_key": "Vpp",
         "start": 0.0, "end": 500.0},
    ])


class ConfigManager:
    """
    配置管理器。

    用法:
        mgr = ConfigManager()
        mgr.save_to_file(main_win, "config.json")
        mgr.load_from_file(main_win, "config.json")
    """

    @staticmethod
    def collect(main_win) -> ScopeConfig:
        """从 MainWindow 各面板收集当前状态。"""
        cfg = ScopeConfig()

        # 设备参数
        dp = main_win.device_panel
        # 尝试从 get_params() 读取, 兼容 DevicePanel
        try:
            p = dp.get_params()
            d = dp.get_config()
            cfg.device.update({
                "device_name": p.get("device_name", "Dev42"),
                "ai_channels": p.get("ai_channels", "ai0:15"),
                "terminal_config": p.get("terminal_config", "NRSE"),
                "read_timeout": p.get("read_timeout", 5.0),
                "sample_rate": d.sample_rate,
                "duration": d.record_length / d.sample_rate if d.sample_rate > 0 else 0.5,
                "sample_mode": "FINITE",
                "trigger_source": p.get("trigger_source", ""),
                "trigger_slope": p.get("trigger_slope", "rising"),
                "trigger_level": p.get("trigger_level", 0.0),
            })
        except Exception:
            logger.warning("读取设备参数失败", exc_info=True)

        # 通道状态
        try:
            cp = main_win.channel_panel
            n = len(cp._controls)
            cfg.channels["enabled"] = [cp.is_channel_enabled(i) for i in range(n)]
            cfg.channels["min_vals"] = [cp.get_channel_min_val(i) for i in range(n)]
            cfg.channels["max_vals"] = [cp.get_channel_max_val(i) for i in range(n)]
        except Exception:
            logger.warning("读取通道状态失败", exc_info=True)

        # 测量行
        try:
            mp = main_win.measure_panel
            cfg.measurements = mp.get_subscriptions()
        except Exception:
            logger.warning("读取测量行失败", exc_info=True)

        return cfg

    @staticmethod
    def apply(main_win, cfg: ScopeConfig):
        """将配置应用到 MainWindow 各面板。"""
        # 设备参数 (仅回填 DevicePanel, 不自动应用)
        try:
            dp = main_win.device_panel
            dp.editDeviceName.setText(cfg.device.get("device_name", "Dev42"))
            dp.editAiChannels.setText(cfg.device.get("ai_channels", "ai0:15"))

            term = cfg.device.get("terminal_config", "NRSE")
            for i in range(dp.cmbTerminal.count()):
                if dp.cmbTerminal.itemData(i) == term:
                    dp.cmbTerminal.setCurrentIndex(i)
                    break

            dp.spinTimeout.setValue(cfg.device.get("read_timeout", 5.0))
            dp.spinSampleRate.setValue(cfg.device.get("sample_rate", 10000))
            dp.spinDuration.setValue(cfg.device.get("duration", 0.5))
            dp._update_samples()

            src = cfg.device.get("trigger_source", "")
            if src:
                dp.chkTrig.setChecked(True)
                dp.editTrigSrc.setText(src)
                slope = cfg.device.get("trigger_slope", "rising")
                for i in range(dp.cmbTrigSlope.count()):
                    if dp.cmbTrigSlope.itemData(i) == slope:
                        dp.cmbTrigSlope.setCurrentIndex(i)
                        break
                dp.spinTrigLevel.setValue(cfg.device.get("trigger_level", 0.0))
            else:
                dp.chkTrig.setChecked(False)

        except Exception as e:
            logger.warning(f"应用设备参数失败: {e}")

        # 通道状态
        try:
            cp = main_win.channel_panel
            ch = cfg.channels
            for i in range(min(len(cp._controls), len(ch.get("enabled", [])))):
                ctrl = cp._controls[i]
                ctrl["enable"].setChecked(ch["enabled"][i])
            for i in range(min(len(cp._controls), len(ch.get("min_vals", [])))):
                cp._controls[i]["min_val"].setValue(ch["min_vals"][i])
            for i in range(min(len(cp._controls), len(ch.get("max_vals", [])))):
                cp._controls[i]["max_val"].setValue(ch["max_vals"][i])
        except Exception as e:
            logger.warning(f"应用通道状态失败: {e}")

        # 测量行 (清空后重建)
        try:
            mp = main_win.measure_panel
            mp.clear_all()
            for m in cfg.measurements:
                mp.add_row(
                    name=m.get("name", ""),
                    channel=m.get("channel", "CH1"),
                    meas_key=m.get("meas_key", "Vpp"),
                    start_time=m.get("start", 0.0),
                    end_time=m.get("end", 0.5),
                )
        except Exception as e:
            logger.warning(f"应用测量行失败: {e}")

    @staticmethod
    def save_to_file(main_win, filepath: str):
        """收集并保存配置到 JSON 文件。"""
        cfg = ConfigManager.collect(main_win)
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(asdict(cfg), f, indent=2, ensure_ascii=False)
            logger.info(f"配置已保存: {filepath}")
            return True
        except Exception as e:
            logger.error(f"保存配置失败: {e}")
            return False

    @staticmethod
    def load_from_file(main_win, filepath: str) -> bool:
        """从 JSON 文件加载并应用配置。"""
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            cfg = ScopeConfig(**{k: data.get(k, v)
                                 for k, v in asdict(ScopeConfig()).items()})
            if "measurements" in data:
                cfg.measurements = data["measurements"]
            if "device" in data:
                cfg.device.update(data["device"])
            if "channels" in data:
                cfg.channels.update(data["channels"])
            ConfigManager.apply(main_win, cfg)
            logger.info(f"配置已加载: {filepath}")
            return True
        except Exception as e:
            logger.error(f"加载配置失败: {e}")
            return False

    @staticmethod
    def default_filepath() -> str:
        """默认配置文件路径。"""
        return os.path.join(os.path.dirname(__file__), "..", "..", "scope_config.json")
