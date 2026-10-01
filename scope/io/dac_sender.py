"""
DacSender — DAC 目标发送器。

通过 rpyc 连接远程 DAC 服务器，控制 B 通道输出电压（A 通道固定 0V）。

发送策略（每帧"读-改-写"）:
  1. conn.root.read()              → 取设备当前真实电压 b_voltage (单位 V)
  2. new = clamp(cur + delta*scale, min_volt, max_volt)   → 保证落在 0~5V
  3. conn.root.set_ab(0, new)      → 第一参数为 A 通道电压, 固定传 0

服务端接口（用户提供的 rpyc 用法）:
  - conn.root.read()          -> {'status': 'success', 'b_voltage': 1.0, ...}
  - conn.root.set_b(v)        单通道设置 (use_set_ab=False 时的回退路径)
  - conn.root.set_ab(a, b)    一次设置 A/B 两个通道电压
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

import rpyc

from scope.io.rpyc_pool import ConnectionPoolManager, PoolConfig

logger = logging.getLogger(__name__)

# DAC 电压允许范围 (V) — 超出会被 clamp
DEFAULT_VOLT_RANGE = (0.0, 5.0)

# A 通道电压: 本项目只用 B 通道做反馈, A 每次固定写 0V
A_VOLTAGE = 0.0


@dataclass
class DACMapping:
    """
    delta → 输出电压 映射配置。

    new_volt = clamp(cur_volt + delta * scale, min_volt, max_volt)
    """

    scale: float = 1.0            # delta → 电压(V) 缩放系数
    min_volt: float = 0.0         # 电压下限 (V)
    max_volt: float = 5.0         # 电压上限 (V)

    def __post_init__(self):
        if self.max_volt < self.min_volt:
            raise ValueError(
                f"max_volt ({self.max_volt}) must be >= min_volt ({self.min_volt})"
            )

    def clamp_volt(self, volt: float) -> float:
        """把电压限幅到 [min_volt, max_volt]。"""
        return max(self.min_volt, min(self.max_volt, volt))

    def apply_delta(self, current_volt: float, delta: float) -> float:
        """把 PID delta 叠加到当前电压, 并限幅到 [min_volt, max_volt]。"""
        return self.clamp_volt(current_volt + delta * self.scale)


class DACSender:
    """
    DAC 目标发送器。

    每个实例通过连接池管理器获取共享 rpyc 连接
    (同一 (ip, port) 的多个 worker 复用同一池)。
    使用方式:
        sender = DACSender("192.168.1.58", 18863)
        await sender.adjust_delta(0.05)   # PID delta → 电压调整
        await sender.close()
    """

    def __init__(
        self,
        ip: str,
        port: int = 18863,
        mapping: Optional[DACMapping] = None,
        use_set_ab: bool = True,
        pool_config: Optional[PoolConfig] = None,
    ):
        self._ip = ip
        self._port = port
        self._mapping = mapping or DACMapping()
        self._use_set_ab = use_set_ab
        self._pool_config = pool_config
        self._pool = ConnectionPoolManager.acquire_pool(
            ip, port, pool_config,
        )
        self._last_volt: Optional[float] = None

    # ── 属性 ────────────────────────────────────────────────────

    @property
    def ip(self) -> str:
        return self._ip

    @property
    def port(self) -> int:
        return self._port

    @property
    def mapping(self) -> DACMapping:
        return self._mapping

    @property
    def use_set_ab(self) -> bool:
        return self._use_set_ab

    @property
    def last_volt(self) -> Optional[float]:
        """最近一次成功写入的电压 (V)"""
        return self._last_volt

    async def close(self):
        """释放连接池引用。"""
        await ConnectionPoolManager.release_pool(self._ip, self._port)

    # ── 核心 API ────────────────────────────────────────────────

    async def read_voltage(self) -> Optional[float]:
        """
        读取设备当前 B 通道电压。

        Returns:
            当前电压 (V); 读取失败或响应异常时返回 None
        """
        try:
            conn = await self._pool.acquire()
            try:
                resp = await self._call_read(conn)
            finally:
                await self._pool.release(conn)
        except Exception as e:
            logger.error("DAC read 失败 %s:%d: %s", self._ip, self._port, e)
            return None
        return self._parse_read(resp)

    async def set_voltage(self, volt: float) -> bool:
        """
        设置 B 通道输出电压 (A 通道固定 0V)。

        Args:
            volt: 目标电压 (V), 自动限幅到 [min_volt, max_volt]

        Returns:
            True 发送成功, False 失败
        """
        volt = self._mapping.clamp_volt(volt)
        try:
            conn = await self._pool.acquire()
            try:
                await self._call_set(conn, volt)
                self._last_volt = volt
                logger.debug("DAC %s:%d 电压已设为 %.4f V",
                             self._ip, self._port, volt)
                return True
            finally:
                await self._pool.release(conn)
        except Exception as e:
            logger.error("DAC set_voltage 失败 %s:%d: %s",
                         self._ip, self._port, e)
            return False

    async def adjust_delta(self, delta: float) -> bool:
        """
        应用 PID 计算出的 delta 调整量。

        先读设备真实电压, 叠加 delta * scale 后限幅写回:
            cur    = read()['b_voltage']
            new    = clamp(cur + delta * scale, 0.0, 5.0)
            set_ab(0, new)

        Args:
            delta: PID 输出调整量 (有符号)

        Returns:
            True 发送成功, False 读取或发送失败 (由 worker 冷却接管)
        """
        current = await self.read_voltage()
        if current is None:
            return False

        target = self._mapping.apply_delta(current, delta)
        ok = await self.set_voltage(target)
        if ok:
            logger.debug(
                "DAC %s:%d %.4f V + delta=%+.6f (scale=%.3f) → %.4f V",
                self._ip, self._port, current, delta,
                self._mapping.scale, target,
            )
        return ok

    # ── 远程调用 ────────────────────────────────────────────────

    async def _call_read(self, conn: rpyc.Connection):
        """
        通过 rpyc 读取设备状态。

        服务端: conn.root.read() → dict (含 b_voltage, 单位 V)
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, conn.root.read)

    async def _call_set(self, conn: rpyc.Connection, volt: float):
        """
        通过 rpyc 写入 B 通道电压。

        服务端架构 (二选一):
          - conn.root.set_ab(a, b)  → 一次设置 A/B 两个通道 (默认, A 固定 0V)
          - conn.root.set_b(b)      → 仅设置 B 通道 (use_set_ab=False)
        """
        loop = asyncio.get_running_loop()
        if self._use_set_ab:
            await loop.run_in_executor(
                None, lambda: conn.root.set_ab(A_VOLTAGE, volt),
            )
        else:
            await loop.run_in_executor(
                None, lambda: conn.root.set_b(volt),
            )

    # ── 响应解析 ────────────────────────────────────────────────

    def _parse_read(self, resp) -> Optional[float]:
        """
        从 read() 响应中提取 b_voltage (单位 V)。

        异常响应 (非 dict / status != "success" / 缺字段 / 非数值) 返回 None。
        """
        try:
            status = resp.get("status")
            volt = resp.get("b_voltage")
        except Exception as e:
            logger.warning(
                "DAC read 响应无法解析 %s:%d: %r (%s)",
                self._ip, self._port, resp, e,
            )
            return None

        if status != "success":
            logger.warning(
                "DAC read 状态非 success %s:%d: %r",
                self._ip, self._port, resp,
            )
            return None

        if volt is None:
            logger.warning(
                "DAC read 响应缺少 b_voltage %s:%d: %r",
                self._ip, self._port, resp,
            )
            return None

        try:
            return float(volt)
        except (TypeError, ValueError):
            logger.warning(
                "DAC read b_voltage 非法 %s:%d: %r",
                self._ip, self._port, volt,
            )
            return None
