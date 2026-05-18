"""
数字示波器 — 入口转发

用法:
  python main.py         连接 ART 硬件 (默认)
  python main.py --mock  使用模拟数据 (无硬件也运行)
"""

import sys
import os

# 确保项目根目录在模块搜索路径中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# DLL 目录
DLL_DIR = r"C:\Program Files (x86)\ART Technology\ArtDAQ\Lib\x64"
if os.path.isdir(DLL_DIR):
    os.add_dll_directory(DLL_DIR)

from scope.main import main

if __name__ == "__main__":
    main()
