from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from uuid import NAMESPACE_DNS, UUID, uuid5

import httpx
from pydantic import BaseModel, Field


@dataclass(frozen=True, slots=True)
class PrometheusMetric:
    query: str
    dimensions: tuple[str, ...]
    per_vps: bool = True
    instant: bool = False


# Use stable filter IDs without creating synthetic entries in the client VPS registry.
PLATFORM_VPS = {
    uuid5(NAMESPACE_DNS, f"monitoring.{host}.yleum.ru"): host
    for host in ("core", "runtime", "commerce")
}


_CPU = (
    "100 * (1 - avg by (vps) (sum without (mode) "
    '(rate(node_cpu_seconds_total{mode=~"idle|steal"<scope>}[5m]))))'
)
_DISK = (
    'node_filesystem_avail_bytes{fstype=~"ext[234]|xfs|btrfs|zfs",'
    'mountpoint!~"/(var/lib/kubelet|var/lib/rancher|run)(/.*)?"<scope>}'
)
_DISK_SIZE = _DISK.replace("_avail_", "_size_")
_MEMORY = "node_memory_MemTotal_bytes{<labels>} - node_memory_MemAvailable_bytes{<labels>}"
_USED = f"({_DISK_SIZE} - {_DISK})"
_PREVIOUS_USED = f"({_DISK_SIZE} offset 1d - {_DISK} offset 1d)"
_NETWORK = 'node_network_<direction>_bytes_total{device!~"lo|veth.*|docker.*|cni.*"<scope>}'
_INTERFACE = "yleum_external_interface{<labels>}"
_API_REQUESTS = "yleum_http_request_duration_seconds_count"
_API_BUCKETS = "yleum_http_request_duration_seconds_bucket"
_SQL_BUCKETS = "yleum_db_query_duration_seconds_bucket"
_PROBE = (
    'label_replace(100 * avg_over_time(yleum_generation_external_probe_success'
    '{<probe_labels>}[<period>]), "vps", "$1", "target_vps", "(.+)")'
)
_API_FRESH = (
    ' and on (job, instance) (time() - node_textfile_mtime_seconds'
    '{file=~".*/yleum_platform_api[.]prom"} < 90)'
)

_REQUIRED_VPS_SERIES = (
    "node_cpu_seconds_total", "node_memory_MemTotal_bytes", "node_vmstat_oom_kill",
    "node_filesystem_avail_bytes", "node_network_receive_bytes_total",
    "node_network_transmit_bytes_total", "yleum_external_interface", "probe_success",
    "yleum_unplanned_boots_total", "yleum_crash_restarts_total",
    "yleum_block_io_duration_seconds_bucket", "yleum_backup_results_total",
    "yleum_backup_last_verified_timestamp_seconds", "yleum_restore_tests_total",
)
_COVERAGE = " and on (deploy_target_id) ".join(
    f'max by (deploy_target_id) (present_over_time({name}'
    '{deploy_target_id!=""<scope>}[1d]))'
    for name in _REQUIRED_VPS_SERIES
)

PROMETHEUS_METRICS = {
    2: PrometheusMetric(
        _PROBE + ' or 100 * avg_over_time(probe_success'
        '{job="yleum-generation-external"<scope>}[<period>])',
        ("vps",),
    ),
    5: PrometheusMetric("yleum_incident_first_delivery_seconds{<labels>} / 60", ("vps",)),
    6: PrometheusMetric("yleum_incident_recovery_after_delivery_seconds{<labels>} / 60", ("vps",)),
    7: PrometheusMetric(
        "increase(yleum_unplanned_boots_total{<labels>}[<period>])",
        ("vps",),
    ),
    8: PrometheusMetric(
        "increase(yleum_crash_restarts_total{<labels>}[<period>])",
        ("vps", "service"),
    ),
    9: PrometheusMetric(
        f"label_replace(avg_over_time(({_CPU})[<period>:1m]), "
        '"stat", "Среднее", "vps", ".*") or '
        f"label_replace(max_over_time(({_CPU})[<period>:1m]), "
        '"stat", "Пик", "vps", ".*")',
        ("vps", "stat"),
    ),
    10: PrometheusMetric(
        '100 * avg by (vps) (rate(node_cpu_seconds_total{mode="steal"<scope>}[5m]))',
        ("vps",),
    ),
    11: PrometheusMetric(
        f'label_replace(({_MEMORY}) / 1e9, "stat", "ГБ", "vps", ".*") or '
        f"label_replace(100 * ({_MEMORY}) / node_memory_MemTotal_bytes{{<labels>}}, "
        '"stat", "%", "vps", ".*")',
        ("vps", "stat"),
    ),
    12: PrometheusMetric("increase(node_vmstat_oom_kill{<labels>}[<period>])", ("vps",)),
    13: PrometheusMetric(
        f'label_replace({_DISK} / 1e9, "stat", "ГБ", "vps", ".*") or '
        f'label_replace(100 * {_DISK} / {_DISK_SIZE}, "stat", "%", "vps", ".*")',
        ("vps", "mountpoint", "stat"),
    ),
    14: PrometheusMetric(f"({_USED} - {_PREVIOUS_USED}) / 1e9", ("vps", "mountpoint")),
    15: PrometheusMetric(
        "1000 * histogram_quantile(0.95, sum by (le, vps, device, direction) "
        "(rate(yleum_block_io_duration_seconds_bucket{<labels>}[<period>]) "
        'and on (job, instance) (time() - node_textfile_mtime_seconds'
        '{file=~".*/yleum_block_io[.]prom"} < 90)))',
        ("vps", "device", "direction"),
    ),
    16: PrometheusMetric(
        "label_replace((increase("
        + _NETWORK.replace("<direction>", "receive")
        + "[1d]) and on(vps, device) "
        + _INTERFACE
        + ") / 1e9, "
        '"direction", "Входящий", "vps", ".*") or '
        "label_replace((increase("
        + _NETWORK.replace("<direction>", "transmit")
        + "[1d]) and on(vps, device) "
        + _INTERFACE
        + ") / 1e9, "
        '"direction", "Исходящий", "vps", ".*")',
        ("vps", "device", "direction"),
    ),
    17: PrometheusMetric(
        "label_replace((max_over_time((rate("
        + _NETWORK.replace("<direction>", "receive")
        + "[1m]))[<period>:1m]) and on(vps, device) "
        + _INTERFACE
        + ") * 8 / 1e6, "
        '"direction", "Входящий", "vps", ".*") or '
        "label_replace((max_over_time((rate("
        + _NETWORK.replace("<direction>", "transmit")
        + "[1m]))[<period>:1m]) and on(vps, device) "
        + _INTERFACE
        + ") * 8 / 1e6, "
        '"direction", "Исходящий", "vps", ".*")',
        ("vps", "device", "direction"),
    ),
    19: PrometheusMetric(
        'yleum_backup_window_results{window="<period>"<scope>} or '
        'increase(yleum_backup_results_total{result=~"failed|missed"<scope>}[<period>])',
        ("vps", "result"),
    ),
    20: PrometheusMetric(
        "(time() - yleum_backup_last_verified_timestamp_seconds{<labels>}) / 3600",
        ("vps",),
    ),
    21: PrometheusMetric(
        "100 * sum by (vps) (increase("
        'yleum_restore_tests_total{result="verified"<scope>}[<period>]))'
        " / sum by (vps) (increase(yleum_restore_tests_total{<labels>}[<period>]))",
        ("vps",),
    ),
    91: PrometheusMetric(
        f"sum by (route, method, status) (increase({_API_REQUESTS}[<period>]){_API_FRESH})",
        ("method", "route", "status"),
        False,
    ),
    92: PrometheusMetric(
        f'100 * (sum by (route, method) (increase({_API_REQUESTS}{{status=~"5.."}}[<period>])'
        f"{_API_FRESH}) or 0 * sum by (route, method) (increase({_API_REQUESTS}[<period>])"
        f"{_API_FRESH})) / sum by (route, method) (increase({_API_REQUESTS}[<period>])"
        f"{_API_FRESH})",
        ("method", "route"),
        False,
    ),
    93: PrometheusMetric(
        f"label_replace(1000 * histogram_quantile(0.5, sum by (le, route, method) "
        f'(rate({_API_BUCKETS}[<period>]){_API_FRESH})), "stat", "Медиана", "route", ".*") or '
        f"label_replace(1000 * histogram_quantile(0.95, sum by (le, route, method) "
        f'(rate({_API_BUCKETS}[<period>]){_API_FRESH})), "stat", "p95", "route", ".*")',
        ("method", "route", "stat"),
        False,
    ),
    95: PrometheusMetric(
        f"label_replace(1000 * histogram_quantile(0.5, sum by (le) "
        f'(rate({_SQL_BUCKETS}[<period>]){_API_FRESH})), "stat", "Медиана", "__name__", ".*") or '
        f"label_replace(1000 * histogram_quantile(0.95, sum by (le) "
        f'(rate({_SQL_BUCKETS}[<period>]){_API_FRESH})), "stat", "p95", "__name__", ".*")',
        ("stat",),
        False,
    ),
    99: PrometheusMetric(
        "time() - max by (vps) (max_over_time(timestamp(node_boot_time_seconds{<labels>})[7d:1m]))",
        ("vps",),
        instant=True,
    ),
    100: PrometheusMetric(_COVERAGE, ("deploy_target_id",), instant=True),
}


class Sample(BaseModel):
    series: str
    at: datetime | None = None
    value: float
    p95: float | None = None
    mean: float | None = None
    samples: int | None = None


class PrometheusSeries(BaseModel):
    metric: dict[str, str]
    values: list[tuple[float, str]] = Field(default_factory=list)
    value: tuple[float, str] | None = None


class PrometheusData(BaseModel):
    result: list[PrometheusSeries]


class PrometheusReply(BaseModel):
    status: str
    data: PrometheusData


def metric_query(
    metric: PrometheusMetric,
    *,
    period_hours: int,
    client: UUID | None,
    vps: UUID | None,
) -> str:
    labels = []
    if client is not None:
        labels.append(f'client_id="{client}"')
    if vps is not None:
        if vps in PLATFORM_VPS:
            labels.append(f'vps="{PLATFORM_VPS[vps]}"')
        else:
            labels.append(f'deploy_target_id="{vps}"')
    selector = ",".join(labels)
    return (
        metric.query.replace("<scope>", f",{selector}" if selector else "")
        .replace("<labels>", selector)
        .replace("<probe_labels>", selector.replace('vps="', 'target_vps="'))
        .replace("<period>", f"{period_hours}h")
    )


async def prometheus_samples(
    url: str,
    metric: PrometheusMetric,
    *,
    start: datetime,
    end: datetime,
    client: UUID | None,
    vps: UUID | None,
) -> list[Sample]:
    period_hours = int((end - start).total_seconds() / 3600)
    params = {"query": metric_query(metric, period_hours=period_hours, client=client, vps=vps)}
    if metric.instant:
        path = "api/v1/query"
        params["time"] = str(end.timestamp())
    else:
        path = "api/v1/query_range"
        # At most 120 evaluation points per series.
        params.update(
            start=str(start.timestamp()),
            end=str(end.timestamp()),
            step=str(max(60, period_hours * 30)),
        )
    async with httpx.AsyncClient(timeout=4, trust_env=False) as http:
        response = await http.get(f"{url.rstrip('/')}/{path}", params=params)
        response.raise_for_status()
    reply = PrometheusReply.model_validate(response.json())
    if reply.status != "success":
        raise ValueError("Prometheus query failed")
    samples = []
    for series in reply.data.result[:120]:
        label = " / ".join(series.metric[k] for k in metric.dimensions if k in series.metric)
        for timestamp, raw in series.values or ([series.value] if series.value else []):
            value = float(raw)
            if isfinite(value):
                samples.append(
                    Sample(
                        series=label or "Всего",
                        at=datetime.fromtimestamp(timestamp, tz=end.tzinfo),
                        value=value,
                    )
                )
    return sorted(samples, key=lambda sample: sample.at or end, reverse=True)[:750]
