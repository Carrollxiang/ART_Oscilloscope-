#!/usr/bin/env python3
"""
AD9910 RPyC Server
基于RPyC协议的AD9910设备RPC服务端
"""

import logging
import sys
import os
import rpyc
import socket
from rpyc.utils.server import ThreadedServer

# 添加项目根目录到路径中
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

try:
    from devices.generators.adiclockevals.ad9910_wrapper import inits
except ImportError as e:
    # 如果无法从指定路径导入，则跳过初始化
    print(f"Warning: Failed to import device wrapper: {e}")
    
    
    def inits():
        pass

from ad9910_rpyc_service import AD9910RPyCService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


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


class AD9910RPyCServer(rpyc.Service):
    def on_connect(self, conn):
        """客户端连接时调用"""
        logger.info("Client connected")
    
    def on_disconnect(self, conn):
        """客户端断开连接时调用"""
        logger.info("Client disconnected")
    
    def exposed_get_ad9910_service(self):
        """暴露AD9910服务"""
        return AD9910RPyCService()


def start_rpyc_server(port=3251):
    """启动RPyC服务器"""
    # 在服务器启动时初始化设备，只执行一次
    logger.info("Initializing devices...")
    inits()

    # 获取本机IP地址
    local_ip = get_local_ip()
    
    server = ThreadedServer(AD9910RPyCServer, hostname="0.0.0.0", port=port, protocol_config={
        'allow_public_attrs': True,
        'allow_pickle': True,
        'allow_all_attrs': True
    })

    logger.info(f"Starting AD9910 RPyC server on {local_ip}:{port}...")
    logger.info(f"Server listening on all interfaces (0.0.0.0:{port})")
    
    try:
        server.start()
    except KeyboardInterrupt:
        logger.info("Shutting down server...")
    finally:
        server.close()


if __name__ == "__main__":
    start_rpyc_server()