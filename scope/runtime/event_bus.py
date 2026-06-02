"""
有界队列 + 背压策略 + 发布订阅事件总线 (v0.5)

提供:
  - BoundedQueue[T]: 有界 FIFO 队列, 支持 drop_oldest / drop_newest / block
  - DropStrategy 枚举
  - EventBus: 1:N 发布-订阅, 各 subscriber 独立 BoundedQueue 背压隔离

用法:
    q = BoundedQueue(maxsize=2, on_drop=DropStrategy.DROP_OLDEST)
    q.put(item)
    item = q.get()  # 或 q.get_nowait()

    bus = EventBus()
    sub_q = bus.subscribe("frame.measured", maxsize=2, name="fit")
    bus.publish("frame.measured", snapshot)
    item = sub_q.get()
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Generic, TypeVar, Optional, Callable

T = TypeVar("T")


class DropStrategy(Enum):
    DROP_OLDEST = "drop_oldest"    # 队列满时丢弃最旧项
    DROP_NEWEST = "drop_newest"    # 队列满时拒绝新项
    BLOCK = "block"                 # 队列满时阻塞等待 (有超时)


@dataclass
class QueueStats:
    """队列运行时指标"""
    qsize: int = 0
    total_puts: int = 0
    total_drops: int = 0
    total_gets: int = 0
    max_size_reached: int = 0
    avg_latency_ms: float = 0.0  # put → get 平均延迟


class BoundedQueue(Generic[T]):
    """
    线程安全有界队列。

    用法:
        q = BoundedQueue(maxsize=1, on_drop=DropStrategy.DROP_OLDEST)
        q.put(data)            # 线程安全, 满时按策略处理
        item = q.get_nowait()  # 非阻塞获取, 空返回 None
        item = q.get(timeout=1.0)  # 阻塞获取 (仅 BLOCK 策略有用)
    """

    def __init__(
        self,
        maxsize: int = 1,
        on_drop: DropStrategy = DropStrategy.DROP_OLDEST,
        name: str = "",
    ):
        self._maxsize = max(maxsize, 1)
        self._on_drop = on_drop
        self._name = name
        self._deque: deque[tuple[T, float]] = deque()
        self._lock = threading.Lock()
        self._not_full = threading.Condition(self._lock)
        self._not_empty = threading.Condition(self._lock)
        self._stats = QueueStats()
        self._on_drop_callback: Optional[Callable[[T], None]] = None

    # ── 属性 ───────────────────────────────────────────────────

    @property
    def stats(self) -> QueueStats:
        with self._lock:
            s = QueueStats(
                qsize=len(self._deque),
                total_puts=self._stats.total_puts,
                total_drops=self._stats.total_drops,
                total_gets=self._stats.total_gets,
                max_size_reached=self._stats.max_size_reached,
                avg_latency_ms=self._stats.avg_latency_ms,
            )
            return s

    @property
    def qsize(self) -> int:
        return len(self._deque)

    @property
    def maxsize(self) -> int:
        return self._maxsize

    def set_drop_callback(self, cb: Callable[[T], None]):
        """设置丢弃回调 (用于日志/指标上报)。"""
        self._on_drop_callback = cb

    # ── 写入 ───────────────────────────────────────────────────

    def put(self, item: T, timeout: float = 0.0) -> bool:
        """
        入队。

        Returns:
            True 如果成功入队, False 如果被丢弃 (DROP_NEWEST) 或超时 (BLOCK)。
        """
        import time
        t0 = time.monotonic()

        with self._lock:
            self._stats.total_puts += 1

            if len(self._deque) < self._maxsize:
                self._deque.append((item, t0))
                self._stats.max_size_reached = max(
                    self._stats.max_size_reached, len(self._deque)
                )
                self._not_empty.notify()
                return True

            # 队列满 → 按策略处理
            if self._on_drop == DropStrategy.DROP_OLDEST:
                old_item, _ = self._deque.popleft()
                self._stats.total_drops += 1
                if self._on_drop_callback:
                    self._on_drop_callback(old_item)
                self._deque.append((item, t0))
                self._not_empty.notify()
                return True

            elif self._on_drop == DropStrategy.DROP_NEWEST:
                self._stats.total_drops += 1
                if self._on_drop_callback:
                    self._on_drop_callback(item)
                return False

            elif self._on_drop == DropStrategy.BLOCK:
                deadline = time.monotonic() + timeout
                while len(self._deque) >= self._maxsize:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        self._stats.total_drops += 1
                        return False
                    self._not_full.wait(timeout=remaining)
                self._deque.append((item, time.monotonic()))
                self._not_empty.notify()
                return True

        return False

    # ── 读取 ───────────────────────────────────────────────────

    def get_nowait(self) -> Optional[T]:
        """非阻塞获取, 空时返回 None。"""
        import time
        with self._lock:
            if not self._deque:
                return None
            item, put_time = self._deque.popleft()
            self._stats.total_gets += 1
            self._not_full.notify()
            # 更新平均延迟
            latency = (time.monotonic() - put_time) * 1000
            n = self._stats.total_gets
            self._stats.avg_latency_ms = (
                (self._stats.avg_latency_ms * (n - 1) + latency) / n
            )
            return item

    def get(self, timeout: float = 1.0) -> Optional[T]:
        """阻塞获取, 超时返回 None。"""
        import time
        deadline = time.monotonic() + timeout
        with self._lock:
            while not self._deque:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._not_empty.wait(timeout=remaining)
            # 这里不能调用 get_nowait()（会再次尝试获取同一把非重入锁）
            item, put_time = self._deque.popleft()
            self._stats.total_gets += 1
            self._not_full.notify()
            latency = (time.monotonic() - put_time) * 1000
            n = self._stats.total_gets
            self._stats.avg_latency_ms = (
                (self._stats.avg_latency_ms * (n - 1) + latency) / n
            )
            return item

    # ── 快照 ───────────────────────────────────────────────────

    def snapshot(self) -> Optional[T]:
        """获取最新项但不移除 (用于 UI 只读)。"""
        with self._lock:
            if not self._deque:
                return None
            return self._deque[-1][0]

    def dequeue_all(self) -> list[T]:
        """一次性取出所有项 (用于批处理)。"""
        with self._lock:
            items = [item for item, _ in self._deque]
            self._deque.clear()
            self._not_full.notify_all()
            self._stats.total_gets += len(items)
            return items

    # ── 诊断 ───────────────────────────────────────────────────

    def stats_text(self) -> str:
        s = self.stats
        return (
            f"Queue[{self._name}] qsize={s.qsize}/{self._maxsize} "
            f"puts={s.total_puts} drops={s.total_drops} "
            f"gets={s.total_gets} latency={s.avg_latency_ms:.1f}ms"
        )


# ──────────────────────────────────────────────────────────────
# EventBus — 发布-订阅事件总线
# ──────────────────────────────────────────────────────────────

_evbus_logger = logging.getLogger(f"{__name__}.EventBus")


class EventBus:
    """
    发布-订阅事件总线。

    两种订阅模式:
      - subscribe():       队列订阅, 返回 BoundedQueue, 供 worker 线程消费
      - subscribe_callback(): 回调订阅, publish 时直接调用回调 (无队列延迟)

    用法:
        bus = EventBus()
        q = bus.subscribe("frame.measured", maxsize=2, name="fit")
        bus.subscribe_callback("frame.measured", my_handler, name="ui")
        bus.publish("frame.measured", snapshot)
        item = q.get()
    """

    def __init__(self):
        self._subs: dict[str, list[BoundedQueue]] = {}
        self._cbs: dict[str, list[tuple[Callable, str]]] = {}   # topic → [(callback, name)]
        self._lock = threading.Lock()

    def subscribe(
        self,
        topic: str,
        maxsize: int = 2,
        on_drop: DropStrategy = DropStrategy.DROP_OLDEST,
        name: str = "",
    ) -> BoundedQueue:
        """
        队列订阅: 返回 subscriber 专用的 BoundedQueue。
        subscriber 在自己的线程中从此 queue 消费。
        """
        q = BoundedQueue(maxsize=maxsize, on_drop=on_drop, name=name or topic)
        with self._lock:
            if topic not in self._subs:
                self._subs[topic] = []
            self._subs[topic].append(q)
        _evbus_logger.debug(f"subscribe: topic={topic!r} name={name!r} total={len(self._subs[topic])}")
        return q

    def subscribe_callback(
        self,
        topic: str,
        callback: Callable[[Any], None],
        name: str = "",
    ) -> None:
        """
        回调订阅: publish 时直接调用 callback(item), 无队列延迟。
        适用于 Qt signal emit 等线程安全桥接场景。
        """
        with self._lock:
            if topic not in self._cbs:
                self._cbs[topic] = []
            self._cbs[topic].append((callback, name or topic))
        _evbus_logger.debug(f"subscribe_callback: topic={topic!r} name={name!r}")

    def publish(self, topic: str, item: Any) -> None:
        """
        向 topic 所有 subscriber 发布数据。
        - 队列 subscriber: put 到各自 BoundedQueue
        - 回调 subscriber: 直接调用 callback(item)
        """
        with self._lock:
            subscribers = list(self._subs.get(topic, []))
            callbacks = list(self._cbs.get(topic, []))

        for q in subscribers:
            q.put(item)
        for cb, cb_name in callbacks:
            try:
                cb(item)
            except Exception as e:
                _evbus_logger.error(f"callback error: topic={topic!r} name={cb_name!r}: {e}")

        _evbus_logger.debug(
            f"publish: topic={topic!r} queues={len(subscribers)} callbacks={len(callbacks)}"
        )

    def unsubscribe(self, topic: str, queue: BoundedQueue) -> None:
        """取消订阅，移除对应 queue。"""
        with self._lock:
            subs = self._subs.get(topic)
            if subs is None:
                return
            try:
                subs.remove(queue)
            except ValueError:
                pass
            if not subs:
                del self._subs[topic]
        _evbus_logger.debug(f"unsubscribe: topic={topic!r}")

    def topic_stats(self, topic: str) -> list[dict]:
        """返回 topic 下所有 subscriber 的运行指标 (调试用)。"""
        with self._lock:
            subs = list(self._subs.get(topic, []))
        return [
            {"name": q._name, **q.stats.__dict__}
            for q in subs
        ]
