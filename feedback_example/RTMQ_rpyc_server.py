
import copy
import threading
import rpyc

from hardware_wrapper.rwg_wrapper import UnifiedRwgController
from rpyc.utils.classic import obtain
from rpyc.utils.server import ThreadedServer
import socket

MyRwgController = UnifiedRwgController(rwg_list=[1, 2, 3, 4])
SBG_PER_PORT = 32
SBG_HOLD_DURATION_US = 1000000
_RWG_INFO_LOCK = threading.RLock()
_APPLIED_RWG_INFO = {}

# # USB 3.0 interface
# intf_usb = ft601_intf("IONCV2PROT")
# # intf_usb.__enter__()  # self.intf_usb.__enter__()  # 持续占有这个端口
# intf_usb.nod_adr = 0
# intf_usb.loc_chn = 1
#
# rwgs = [1, 2]
#
# run_all = run_cfg(intf_usb, rwgs + [0])
# run_all(asm())
# asm.cfg = run_all

# 将全局变量移到类内或者保持全局
RWG_info = {
    1: {'init_freq': [180,  # MHz
                      194,
                      150,
                      140],
        'sbg_freq': {0x00: (34.6, 0.0126),  # 795 X+ GM Repumper (sbg_freq:MHz, amp: V) 0.0117
                     0x20: (20, 0.0129),  # 795 X+ SP Repumper  最外态态制备
                     0x40: (18, 0.064),  # 780 X- Detection Cooling
                     0x60: (20, 0.04)}  # 780 X- EIT Cooling
        },
    2: {'init_freq': [190,
                      100,
                      100,
                      100],
        'sbg_freq': {0x00: (10, 0.15),
                     0x20: (10, 0.01),  # 第一路
                     0x40: (10, 0.08),  # 第二路移频，  SLM透射路
                     0x41: (10, 0.),  # 第二路移频，  SLM透射路
                     0x42: (10, 0.),  # 第二路移频，  SLM透射路
                     0x43: (10, 0.),  # 第二路移频，  SLM透射路
                     0x44: (10, 0.),  # 第二路移频，  SLM透射路
                     0x60: (10, 0.03)}
        },
    3: {'init_freq': [200,
                      100,
                      200,
                      100],
        'sbg_freq': {0x00: (0, 0.02),
                     0x20: (10, 0.16),  # 795 Lattice temp
                     0x40: (0, 0.5),  # 795 X- Local Depump
                     0x60: (10, 0.049974187253615895)}  # 420 slow feedback
        },
    4: {'init_freq': [100,
                      100,
                      100,
                      100],
        'sbg_freq': {0x00: (0, 0.1),
                     0x20: (0, 0.1),  # AOD
                     0x40: (0, 0.1),  # AOD
                     0x60: (0, 0.1)}  # AOD
        },
    
}


class RWGService(rpyc.Service):
    def __init__(self):
        super(RWGService, self).__init__()
        # self.exposed_set_rwg_info(RWG_info)
    
    def exposed_change_rwg_info(self, card: int, sbg_ch: int, sbg_freq: float = None, amp: float = None,
                                init_freq: float = None):
        """
        通过RPC暴露的函数，允许远程修改RWG信息并重新加载
        """
        global RWG_info
        card = _as_int(card)
        sbg_ch = _as_int(sbg_ch)
        if card not in MyRwgController.rwg_list or not (0 <= sbg_ch < 128):
            raise ValueError('Invalid card number or sbg_ch number')
        
        with _RWG_INFO_LOCK:
            new_info = copy.deepcopy(RWG_info)
            if card not in new_info:
                raise ValueError(f'RWG card {card} is not configured')
            
            sbg_map = new_info[card].setdefault('sbg_freq', {})
            if sbg_ch not in sbg_map:
                if sbg_freq is None:
                    raise ValueError(f'SBG channel {sbg_ch} is not configured; sbg_freq is required')
                sbg_map[sbg_ch] = (float(sbg_freq), 0.0)
            
            old_tuple = tuple(sbg_map[sbg_ch])
            old_freq = float(old_tuple[0])
            old_amp = float(old_tuple[1])
            old_phase = old_tuple[2] if len(old_tuple) > 2 else None
            
            if sbg_freq is not None:
                old_freq = float(sbg_freq)
                if not (-50 < old_freq < 50):
                    raise ValueError('sbg_freq must be in (-50, 50) MHz')
            
            if amp is not None:
                old_amp = float(amp)
                if not (0 <= old_amp <= 1):
                    raise ValueError('amp must be in [0, 1]')
            
            if old_phase is None:
                sbg_map[sbg_ch] = (old_freq, old_amp)
            else:
                sbg_map[sbg_ch] = (old_freq, old_amp, old_phase)
            
            if init_freq is not None:
                mux = sbg_ch // SBG_PER_PORT
                if 0 <= mux < len(new_info[card]['init_freq']):
                    new_info[card]['init_freq'][mux] = float(init_freq)
            
            normalized_info = _normalize_rwg_info(new_info)
            _apply_changed_cards(normalized_info, cards=[card])
            RWG_info = normalized_info
        return True
    
    def exposed_get_rwg_info(self):
        """
        获取当前的RWG配置信息
        """
        with _RWG_INFO_LOCK:
            return copy.deepcopy(RWG_info)
    
    def exposed_set_rwg_info(self, rwg_info):
        """
        设置RWG信息并重新加载
        """
        global RWG_info
        with _RWG_INFO_LOCK:
            normalized_info = _normalize_rwg_info(rwg_info)
            changed_cards = _apply_changed_cards(normalized_info)
            RWG_info = normalized_info
            print(f"RWG Info now: {RWG_info}")
            print(f"Realtime updated cards: {changed_cards if changed_cards else 'none'}")
        
        return True


def _as_int(value):
    if isinstance(value, str):
        return int(value, 0)
    return int(value)


def _normalize_rwg_info(rwg_info):
    raw_info = obtain(rwg_info)
    normalized = {}
    
    for raw_card, raw_card_info in dict(raw_info).items():
        card = _as_int(raw_card)
        if card not in MyRwgController.rwg_list:
            raise ValueError(f'Invalid RWG card number: {card}')
        
        init_freq = [float(freq) for freq in list(raw_card_info['init_freq'])]
        if len(init_freq) != 4:
            raise ValueError(f'RWG card {card} init_freq must contain 4 carrier frequencies')
        
        sbg_freq = {}
        for raw_sbg_ch, raw_sbg_params in dict(raw_card_info['sbg_freq']).items():
            sbg_ch = _as_int(raw_sbg_ch)
            if not (0 <= sbg_ch < 128):
                raise ValueError(f'Invalid SBG channel on RWG card {card}: {sbg_ch}')
            
            params = tuple(raw_sbg_params)
            if len(params) < 2:
                raise ValueError(f'Invalid SBG params for RWG card {card}, channel {sbg_ch}: {params}')
            
            freq = float(params[0])
            amp = float(params[1])
            if len(params) > 2 and params[2] is not None:
                sbg_freq[sbg_ch] = (freq, amp, params[2])
            else:
                sbg_freq[sbg_ch] = (freq, amp)
        
        normalized[card] = {
            'init_freq': init_freq,
            'sbg_freq': sbg_freq,
        }
    
    missing_cards = [card for card in MyRwgController.rwg_list if card not in normalized]
    if missing_cards:
        raise ValueError(f'Missing RWG card configuration: {missing_cards}')
    
    return normalized


def _build_realtime_packets(card_info):
    init_freq = card_info['init_freq']
    sbg_abs_freq = {}
    
    for sbg_ch, params in card_info['sbg_freq'].items():
        mux = int(sbg_ch) // SBG_PER_PORT
        sbg_sideband_freq = float(params[0])
        amp = float(params[1])
        phase = params[2] if len(params) > 2 else None
        
        # UnifiedRwgController's optimizer expects absolute frequencies.
        # The DDS config stores legacy sideband frequencies, so convert here.
        abs_freq = float(init_freq[mux]) + sbg_sideband_freq
        sbg_abs_freq[int(sbg_ch)] = (abs_freq, amp, phase)
    
    return [['rwg_play', SBG_HOLD_DURATION_US, sbg_abs_freq, []]]


def _apply_changed_cards(rwg_info, cards=None):
    changed_cards = []
    target_cards = cards if cards is not None else MyRwgController.rwg_list
    
    for card in target_cards:
        card = _as_int(card)
        new_card_info = rwg_info[card]
        if _APPLIED_RWG_INFO.get(card) == new_card_info:
            continue
        
        print(f"Programing changed card {card}")
        MyRwgController.set_carriers(card, new_card_info['init_freq'])
        MyRwgController.send_realtime_command(
            card,
            _build_realtime_packets(new_card_info),
            use_fixed_carriers=True,
        )
        _APPLIED_RWG_INFO[card] = copy.deepcopy(new_card_info)
        changed_cards.append(card)
    
    return changed_cards


def initialize_default_rwg_info():
    """
    启动服务前先把默认 RWG_info 写入所有配置板卡。
    """
    global RWG_info
    with _RWG_INFO_LOCK:
        normalized_info = _normalize_rwg_info(RWG_info)
        initialized_cards = _apply_changed_cards(normalized_info)
        RWG_info = normalized_info
        print(f"Default RWG_info initialized on cards: {initialized_cards}")


def get_local_ip():
    """
    获取本机在局域网中的IP地址
    """
    try:
        # 创建一个UDP socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # 连接到一个远程地址（不会实际发送数据）
        s.connect(("8.8.8.8", 80))
        # 获取本地IP地址
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        # 如果无法获取，则返回默认值
        return "127.0.0.1"


if __name__ == '__main__':
    local_ip = get_local_ip()
    initialize_default_rwg_info()
    port = 18867
    
    # 启动rpyc服务器
    server = ThreadedServer(RWGService, hostname=f"0.0.0.0", port=port)
    print(f"RWG RPC Server started on {local_ip}, port {port}")
    server.start()
    
    # conn = rpyc.connect("localhost", 18861)  # 替换为实际的服务端IP地址
    # conn.root.set_rwg_info(RWG_info)
    # conn.close()
    
    # import rpyc
    # conn = rpyc.connect("localhost", 18861)  # 替换为实际的服务端IP地址
    # conn.root.change_rwg_info(card=2, sbg_ch=0x20, amp=0.15)
    # conn.close()
