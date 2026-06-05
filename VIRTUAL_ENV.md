# 虚拟环境使用说明

## 环境信息
- Python 版本: 3.10.20
- 位置: `.venv/`
- 包管理: conda + pip

## 激活环境

### Windows (PowerShell)
```powershell
conda activate D:\ART_Oscilloscope-\.venv
```

### Windows (CMD)
```cmd
activate_env.bat
```

## 已安装的核心包
- numpy>=2.2.6
- PyQt6>=6.11.0
- pyqtgraph>=0.14.0
- qasync>=0.28.0
- scipy>=1.15.3
- pytest>=9.0.3
- pytest-asyncio>=1.4.0

## 运行测试
```bash
python -m pytest tests/test_phase0.py -v
```

## 运行 Mock 模式
```bash
python -m scope.main --mock
```

## 安装额外依赖
```bash
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple <package_name>
```
