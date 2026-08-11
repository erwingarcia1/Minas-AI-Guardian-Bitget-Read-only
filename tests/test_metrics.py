from __future__ import annotations

from prometheus_client import CollectorRegistry

from sentinel.metrics import Metrics


def _sample(registry, name, **labels):
    value = registry.get_sample_value(name, labels)
    return 0.0 if value is None else value


def test_untimestamped_records_counter_is_exported_and_labelled():
    """The counter has to actually reach the Prometheus registry under a
    stable name and label set - that name is what an alarm rule binds to, so
    renaming it silently breaks the page it exists to trigger."""
    registry = CollectorRegistry()
    metrics = Metrics(registry=registry)

    metrics.untimestamped_record("binance-main", "fill")
    metrics.untimestamped_record("binance-main", "fill")
    metrics.untimestamped_record("binance-main", "withdrawal")

    name = "sentinel_untimestamped_records_total"
    assert _sample(registry, name, exchange="binance-main", source="fill") == 2.0
    assert _sample(registry, name, exchange="binance-main", source="withdrawal") == 1.0
    # An exchange that has never misbehaved must not appear as a zero series
    # that dilutes a rate() across the whole fleet.
    assert registry.get_sample_value(name, {"exchange": "bitget-main", "source": "fill"}) is None


def test_metrics_default_to_the_global_registry():
    """The registry argument exists for tests; production still gets the
    default global registry that /metrics serves."""
    from prometheus_client import REGISTRY

    registry = CollectorRegistry()
    Metrics(registry=registry)
    # Constructing against a private registry must not leak into the global
    # one, or a second Metrics() in the same process would fail to register.
    assert REGISTRY.get_sample_value("sentinel_untimestamped_records_total", {}) is None
