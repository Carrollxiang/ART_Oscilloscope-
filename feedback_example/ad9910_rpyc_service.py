import logging
import sys
import os

# 添加项目根目录到路径中
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

try:
    from devices.generators.adiclockevals.ad9910_wrapper import inits
    from devices.generators.adiclockevals.ad9910 import AD9910
except ImportError as e:
    # 如果无法从指定路径导入，则尝试从ArrayController中导入
    try:
        sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../ArrayController")))
        from devices.ad9910 import AD9910
    except ImportError as e2:
        print(f"Failed to import device libraries: {e} and {e2}")
        sys.exit(1)

logger = logging.getLogger(__name__)


class AD9910RPyCService:
    def __init__(self):
        # 初始化时不需要启动worker
        pass

    def exposed_set_frequency(self, device_id: int, profile: int, freq: float):
        try:
            device = AD9910.get("AD9910", device_id)
            device[profile].frequency = freq
            logger.info(f"Device {device_id} Profile {profile} Freq set to {freq}")
            return True
        except Exception as e:
            logger.error(f"Failed to set frequency for device {device_id}: {e}")
            return False

    def exposed_set_amplitude(self, device_id: int, profile: int, amp: float):
        try:
            device = AD9910.get("AD9910", device_id)
            device[profile].amplitude = amp
            logger.info(f"Device {device_id} Profile {profile} Amp set to {amp}")
            return True
        except Exception as e:
            logger.error(f"Failed to set amplitude for device {device_id}: {e}")
            return False

    def exposed_adjust_amplitude(self, device_id: int, profile: int, delta: float):
        try:
            device = AD9910.get("AD9910", device_id)
            new_amp = device[profile].amplitude + delta
            device[profile].amplitude = max(0, min(new_amp, 0.4472))  # sqrt(0.2)
            logger.info(f"Device {device_id} Profile {profile} Amp adjusted to {device[profile].amplitude}")
            return True
        except Exception as e:
            logger.error(f"Failed to adjust amplitude for device {device_id}: {e}")
            return False

    def exposed_set_phase(self, device_id: int, profile: int, phase: float):
        try:
            device = AD9910.get("AD9910", device_id)
            device[profile].phase = phase
            logger.info(f"Device {device_id} Profile {profile} Phase set to {phase}")
            return True
        except Exception as e:
            logger.error(f"Failed to set phase for device {device_id}: {e}")
            return False

    def exposed_client_print(self, message:str):
    
        logger.info(f"client info : {message}")


    def exposed_close(self):
        # 不需要关闭worker
        pass