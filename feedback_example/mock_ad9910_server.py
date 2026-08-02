#!/usr/bin/env python3
"""
本地 mock AD9910 rpyc server — 用于反馈链路的端到端验证。

与 ad9910_rpyc_server.py 的协议结构一致 (ThreadedServer +
exposed_get_ad9910_service + exposed_adjust_amplitude), 但不依赖
真实 AD9910 硬件: 每次调用打印 [SERVER] CALL 日志。

用法:
    MOCK_PORT=33251 python mock_ad9910_server.py
    MOCK_SLOW=3  python mock_ad9910_server.py   # 模拟慢响应 (秒)

验证客户端:
    python -c "import rpyc; c=rpyc.connect('127.0.0.1',33251,config={'allow_public_attrs':True,'allow_pickle':True,'allow_all_attrs':True}); print(c.root.get_ad9910_service().adjust_amplitude(0x0D11,0,0.05)); c.close()"
"""

import logging
import os
import time

import rpyc
from rpyc.utils.server import ThreadedServer

logging.basicConfig(level=logging.INFO, format="%(message)s")


class MockAD9910Service:
    """mock AD9910 设备服务 — 与 AD9910RPyCService 的 exposed_ 接口一致"""

    def exposed_adjust_amplitude(self, device_id: int, profile: int, delta: float):
        slow = float(os.environ.get("MOCK_SLOW", "0"))
        if slow:
            time.sleep(slow)
        print(f"[SERVER] CALL adjust_amplitude(device_id={device_id}, profile={profile}, delta={delta})", flush=True)
        return True

    def exposed_set_frequency(self, device_id: int, profile: int, freq: float):
        print(f"[SERVER] CALL set_frequency(device_id={device_id}, profile={profile}, freq={freq})", flush=True)
        return True

    def exposed_set_amplitude(self, device_id: int, profile: int, amp: float):
        print(f"[SERVER] CALL set_amplitude(device_id={device_id}, profile={profile}, amp={amp})", flush=True)
        return True


class MockServer(rpyc.Service):
    """与 AD9910RPyCServer 结构一致: on_connect/on_disconnect + exposed_get_ad9910_service"""

    def on_connect(self, conn):
        print("[SERVER] on_connect", flush=True)

    def on_disconnect(self, conn):
        print("[SERVER] on_disconnect", flush=True)

    def exposed_get_ad9910_service(self):
        return MockAD9910Service()


if __name__ == "__main__":
    port = int(os.environ.get("MOCK_PORT", "33251"))
    server = ThreadedServer(MockServer, hostname="0.0.0.0", port=port, protocol_config={
        'allow_public_attrs': True,
        'allow_pickle': True,
        'allow_all_attrs': True,
    })
    print(f"[SERVER] listening on 0.0.0.0:{port}", flush=True)
    server.start()
