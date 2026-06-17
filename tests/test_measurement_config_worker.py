"""
MeasurementConfigWorker 单元测试
"""

from scope.runtime import (
    DropStrategy,
    EventBus,
    MeasurementConfigWorker,
    MeasurementProcessor,
    MeasurementSpec,
    MeasurementSpecsChanged,
)


def make_worker():
    event_bus = EventBus()
    event_bus.register_topic("frame.raw", maxsize=2, on_drop=DropStrategy.DROP_OLDEST)
    event_bus.register_topic("frame.fitted", maxsize=2, on_drop=DropStrategy.DROP_OLDEST)
    event_bus.register_topic(
        "measurement.specs.changed",
        maxsize=4,
        on_drop=DropStrategy.DROP_OLDEST,
    )
    processor = MeasurementProcessor(event_bus, specs=[])
    worker = MeasurementConfigWorker(event_bus, processor)
    return worker, processor


def test_apply_specs_changed():
    worker, processor = make_worker()
    specs = [
        MeasurementSpec(tag="m0", channel=0, feature="Vpp"),
        MeasurementSpec(tag="m1", channel=1, feature="Mean"),
    ]

    worker._apply_change(MeasurementSpecsChanged(specs=specs, change_id=1))

    assert processor._specs == specs
    assert worker.metrics["changes_applied"] == 1


def test_skip_old_specs_changed():
    worker, processor = make_worker()
    first = [MeasurementSpec(tag="m0", channel=0, feature="Vpp")]
    stale = [MeasurementSpec(tag="stale", channel=1, feature="Mean")]

    worker._apply_change(MeasurementSpecsChanged(specs=first, change_id=2))
    worker._apply_change(MeasurementSpecsChanged(specs=stale, change_id=1))

    assert processor._specs == first
    assert worker.metrics["changes_applied"] == 1
    assert worker.metrics["changes_skipped"] == 1
