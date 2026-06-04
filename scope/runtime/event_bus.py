"""
有界队列 + 背压策略 + EventBus 数据路由 (v0.4)

提供:
  - BoundedQueue[T]: 有界 FIFO 队列, 支持 drop_oldest / drop_newest / block
  - DropStrategy 枚举
  - EventBus: 多 topic 数据分发路由器, 每个 subscriber 独立队列

用法:
    # 有界队列
    q = BoundedQueue(maxsize=2, on_drop=DropStrategy.DROP_OLDEST)
    q.put(item)
    item = q.get()  # 或 q.get_nowait()

    # EventBus 路由
    bus = EventBus()
    bus.register_topic("frame.measured", maxsize=2)
    sub_q = bus.subscribe("frame.measured")
    bus.publish("frame.measured", data)
    data = sub_q.get_nowait()
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Generic, TypeVar, Optional, Callable, Any

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


# ═══════════════════════════════════════════════════════════════
# EventBus — 多 topic 数据分发路由器
# ═══════════════════════════════════════════════════════════════


@dataclass
class TopicConfig:
    """Topic 队列参数"""
    maxsize: int = 2
    on_drop: DropStrategy = DropStrategy.DROP_OLDEST
    name: str = ""


class EventBus:
    """
    多 topic 数据分发路由器。

    每个 topic 下维护多个 subscriber 队列（独立消费进度）。
    publish() 遍历所有 subscriber 队列依次写入。

    用法:
        bus = EventBus()
        bus.register_topic("frame.measured", maxsize=2)

        # Worker A
        q_a = bus.subscribe("frame.measured")
        # Worker B
        q_b = bus.subscribe("frame.measured")

        # 生产者
        bus.publish("frame.measured", some_data)

        # 各 worker 独立消费
        data_a = q_a.get_nowait()
        data_b = q_b.get_nowait()
    """

    def __init__(self):
        self._lock = threading.Lock()
        # topic → list[(BoundedQueue, TopicConfig)]
        self._topics: dict[str, list[tuple[BoundedQueue, TopicConfig]]] = {}
        self._topic_configs: dict[str, TopicConfig] = {}

    def register_topic(
        self,
        topic: str,
        maxsize: int = 2,
        on_drop: DropStrategy = DropStrategy.DROP_OLDEST,
    ):
        """
        注册一个 topic（幂等：重复注册用同一配置）。
        创建 topic 的有界队列参数模板，首次 subscribe 时创建实际队列。
        """
        with self._lock:
            if topic not in self._topic_configs:
                self._topic_configs[topic] = TopicConfig(
                    maxsize=maxsize, on_drop=on_drop, name=topic
                )
                self._topics[topic] = []

    def subscribe(self, topic: str) -> BoundedQueue:
        """
        订阅一个 topic。
        返回一个独立的新 BoundedQueue，调用方自行轮询。

        Raises:
            KeyError: topic 未注册
        """
        with self._lock:
            if topic not in self._topic_configs:
                raise KeyError(
                    f'topic "{topic}" 未注册, 请先调用 register_topic()'
                )
            cfg = self._topic_configs[topic]
            q = BoundedQueue(maxsize=cfg.maxsize, on_drop=cfg.on_drop, name=f"{topic}-sub#{len(self._topics[topic])}")
            self._topics[topic].append((q, cfg))
            return q

    def publish(self, topic: str, item: Any):
        """
        向 topic 的所有 subscriber 队列写入数据。

        线程安全。满队列时按各队列自身策略处理。
        单个队列的丢弃不影响其他队列。
        """
        with self._lock:
            subscribers = self._topics.get(topic)
            if not subscribers:
                return  # 无 subscriber → 静默丢弃
            # 在锁外依次写入 (put 内部有自己的锁)
            queues = [q for q, _ in subscribers]

        for q in queues:
            q.put(item)

    @property
    def topics(self) -> list[str]:
        """返回已注册的所有 topic 名称。"""
        with self._lock:
            return list(self._topic_configs.keys())

    def metrics(self) -> dict[str, list[QueueStats]]:
        """
        所有 topic 的运行时指标快照。

        Returns:
            {"frame.measured": [QueueStats(sub0), QueueStats(sub1), ...], ...}
        """
        result: dict[str, list[QueueStats]] = {}
        with self._lock:
            for topic, subscribers in self._topics.items():
                result[topic] = [q.stats for q, _ in subscribers]
        return result

    def topic_metrics_text(self, topic: str) -> str:
        """单个 topic 的指标文本（用于日志/UI）。"""
        lines: list[str] = []
        with self._lock:
            subscribers = self._topics.get(topic, [])
            for i, (q, _) in enumerate(subscribers):
                lines.append(f"  [#{i}] {q.stats_text()}")
        if not lines:
            return f"Topic[{topic}]: (无 subscriber)"
        return f"Topic[{topic}]:\n" + "\n".join(lines)
