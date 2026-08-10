"""
EventBus 订阅/退订单元测试
"""

import pytest

from scope.runtime.event_bus import DropStrategy, EventBus


@pytest.fixture
def bus():
    eb = EventBus()
    eb.register_topic("frame.fitted", maxsize=2, on_drop=DropStrategy.DROP_OLDEST)
    return eb


class TestSubscribe:
    def test_subscribe_returns_queue(self, bus):
        q = bus.subscribe("frame.fitted")
        assert q is not None
        assert q.maxsize == 2

    def test_subscribe_unknown_topic_raises(self, bus):
        with pytest.raises(KeyError):
            bus.subscribe("no.such.topic")

    def test_two_subscribers_get_independent_queues(self, bus):
        q1 = bus.subscribe("frame.fitted")
        q2 = bus.subscribe("frame.fitted")
        bus.publish("frame.fitted", object())
        assert q1.get_nowait() is not None
        assert q2.get_nowait() is not None


class TestUnsubscribe:
    def test_unsubscribe_removes_queue(self, bus):
        q = bus.subscribe("frame.fitted")
        bus.unsubscribe("frame.fitted", q)
        # 退订后 publish 不再写入该队列: 队列应保持为空
        bus.publish("frame.fitted", object())
        assert q.get_nowait() is None

    def test_unsubscribe_keeps_other_subscribers(self, bus):
        q1 = bus.subscribe("frame.fitted")
        q2 = bus.subscribe("frame.fitted")
        bus.unsubscribe("frame.fitted", q1)
        bus.publish("frame.fitted", object())
        assert q1.get_nowait() is None
        assert q2.get_nowait() is not None

    def test_unsubscribe_unknown_queue_is_safe(self, bus):
        q1 = bus.subscribe("frame.fitted")
        other = bus.subscribe("frame.fitted")
        # 用一个从未注册过的 queue 对象退订 → 静默无副作用
        import types
        fake = types.SimpleNamespace()
        bus.unsubscribe("frame.fitted", fake)
        bus.publish("frame.fitted", object())
        assert q1.get_nowait() is not None
        assert other.get_nowait() is not None

    def test_unsubscribe_unknown_topic_is_safe(self, bus):
        bus.unsubscribe("no.such.topic", None)
        # 未抛异常即通过

    def test_metrics_reflect_unsubscribe(self, bus):
        q = bus.subscribe("frame.fitted")
        bus.publish("frame.fitted", object())
        assert bus.metrics()["frame.fitted"][0].qsize == 1
        bus.unsubscribe("frame.fitted", q)
        assert len(bus.metrics()["frame.fitted"]) == 0
