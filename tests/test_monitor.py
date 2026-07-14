from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from src.agent.monitor import AlibabaReadMonitor

Responder = Callable[[str, dict[str, Any]], dict[str, Any]]

# Two fixed sample instants inside any test window (epoch millis).
_T0_MS = 1_719_792_000_000
_T1_MS = 1_719_795_600_000
_T0 = datetime.fromtimestamp(_T0_MS / 1000, tz=UTC)
_T1 = datetime.fromtimestamp(_T1_MS / 1000, tz=UTC)


class StubClient:
    """Record every MCP call and return a canned payload per (tool, args)."""

    def __init__(self, responder: Responder) -> None:
        self.responder = responder
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, arguments))
        return self.responder(name, arguments)


def _monitor(responder: Responder) -> tuple[AlibabaReadMonitor, StubClient]:
    client = StubClient(responder)
    return AlibabaReadMonitor(client, cloud_account_id="account-a"), client


def test_list_instances_uses_common_api_caller_and_parses_nested_body() -> None:
    def responder(name: str, args: dict[str, Any]) -> dict[str, Any]:
        assert name == "CommonAPICaller"
        assert args == {
            "service": "ecs",
            "api": "DescribeInstances",
            "parameters": {"RegionId": "ap-southeast-1"},
        }
        return {
            "body": {
                "Instances": {
                    "Instance": [
                        {
                            "InstanceId": "i-1",
                            "Status": "Running",
                            "Tags": {"Tag": [{"TagKey": "env", "TagValue": "dev"}]},
                        },
                        {"InstanceId": "i-2", "Status": "Stopped", "Tags": {}},
                    ]
                }
            }
        }

    monitor, _ = _monitor(responder)
    instances = monitor.list_instances("ap-southeast-1")

    assert [(i.resource_id, i.status) for i in instances] == [
        ("i-1", "Running"),
        ("i-2", "Stopped"),
    ]
    assert instances[0].tags == {"env": "dev"}
    assert instances[0].cloud_account_id == "account-a"


def test_describe_instance_sends_instance_id_list_and_tolerates_flat_body() -> None:
    def responder(name: str, args: dict[str, Any]) -> dict[str, Any]:
        assert args["parameters"] == {"RegionId": "ap-southeast-1", "InstanceIds": ["i-1"]}
        # Flat body, no OpenAPI "body" wrapper, list instead of Instances.Instance.
        return {"Instances": [{"InstanceId": "i-1", "Status": "Running"}]}

    monitor, _ = _monitor(responder)
    instance = monitor.describe_instance("ap-southeast-1", "i-1")

    assert instance.resource_id == "i-1"
    assert instance.status == "Running"
    assert instance.tags == {}


def test_describe_instance_rejects_non_singleton_result() -> None:
    monitor, _ = _monitor(lambda name, args: {"body": {"Instances": {"Instance": []}}})
    with pytest.raises(ValueError, match="exactly one instance"):
        monitor.describe_instance("ap-southeast-1", "i-1")


def test_read_state_reads_through_describe_instance() -> None:
    monitor, client = _monitor(
        lambda name, args: {
            "body": {"Instances": {"Instance": [{"InstanceId": "i-1", "Status": "Stopped"}]}}
        }
    )
    assert monitor.read_state("ap-southeast-1", "i-1") == "Stopped"
    assert client.calls[0][0] == "CommonAPICaller"


def test_monitoring_samples_parses_metriclist_datapoints() -> None:
    def datapoints(average_by_ts: dict[int, float]) -> str:
        return json.dumps(
            [
                {"timestamp": ts, "instanceId": "i-1", "Average": avg}
                for ts, avg in average_by_ts.items()
            ]
        )

    metric_values = {
        "cpu_total": {_T0_MS: 2.0, _T1_MS: 3.0},
        "memory_usedutilization": {_T0_MS: 10.0, _T1_MS: 11.0},
        "diskusage_utilization": {_T0_MS: 40.0},  # missing _T1 on purpose
    }

    def responder(name: str, args: dict[str, Any]) -> dict[str, Any]:
        assert name == "CMS_GetMetricList"
        assert args["InstanceIds"] == ["i-1"]
        assert args["RegionId"] == "ap-southeast-1"
        assert args["Period"] == "3600"
        # StartTime/EndTime are epoch-millis strings.
        assert args["StartTime"].isdigit() and args["EndTime"].isdigit()
        return {"Datapoints": datapoints(metric_values[args["MetricName"]])}

    monitor, _ = _monitor(responder)
    samples = monitor.monitoring_samples("ap-southeast-1", "i-1", start=_T0, end=_T1)

    # Only timestamps shared by cpu AND memory become samples; disk is optional.
    assert [(s.observed_at, s.cpu_percent, s.memory_percent, s.disk_percent) for s in samples] == [
        (_T0, 2.0, 10.0, 40.0),
        (_T1, 3.0, 11.0, None),
    ]


def test_monitoring_samples_tolerates_empty_datapoint_string() -> None:
    monitor, _ = _monitor(lambda name, args: {"Datapoints": ""})
    assert monitor.monitoring_samples("ap-southeast-1", "i-1", start=_T0, end=_T1) == []
