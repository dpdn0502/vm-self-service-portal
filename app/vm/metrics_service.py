"""
Azure Monitor metrics service.

Fetches CPU, Memory, Disk I/O and Network metrics for a VM
via the Azure Monitor REST API (azure-mgmt-monitor 6.x).

Notes:
  - CPU, Disk, Network always available (no agent required).
  - Memory (Available Memory Bytes) requires Azure Monitor Agent
    (AMA) on the VM; gracefully returns available=False if absent.
  - Granularity: PT5M for ≤ 6 h windows; PT1H for 24 h.
"""

from datetime import datetime, timedelta, timezone

from azure.identity import ClientSecretCredential
from azure.mgmt.monitor import MonitorManagementClient

from config import Config

# ── Credential + client ───────────────────────────────────────

def _get_monitor_client():
    cred = ClientSecretCredential(
        tenant_id     = Config.TENANT_ID,
        client_id     = Config.CLIENT_ID,
        client_secret = Config.CLIENT_SECRET,
    )
    return MonitorManagementClient(cred, Config.SUBSCRIPTION_ID)


# ── Metric helpers ────────────────────────────────────────────

def _vm_resource_id(resource_group: str, vm_name: str) -> str:
    return (
        f"/subscriptions/{Config.SUBSCRIPTION_ID}"
        f"/resourceGroups/{resource_group}"
        f"/providers/Microsoft.Compute/virtualMachines/{vm_name}"
    )


def _fetch_metric(client, resource_id, metric_name, start, end, interval):
    """
    Return (timestamps, values) lists or raise.
    timestamps are ISO-8601 strings; values are floats (None → 0.0).
    """
    result = client.metrics.list(
        resource_uri       = resource_id,
        timespan           = f"{start.isoformat()}/{end.isoformat()}",
        interval           = interval,
        metricnames        = metric_name,
        aggregation        = 'Average',
    )

    timestamps = []
    values     = []

    for metric in result.value:
        for ts in metric.timeseries:
            for dp in ts.data:
                ts_str = dp.time_stamp.strftime('%H:%M')
                timestamps.append(ts_str)
                values.append(
                    round(dp.average, 2)
                    if dp.average is not None
                    else 0.0
                )
        break  # only one timeseries expected

    return timestamps, values


def _empty_metric(reason='No data'):
    return {
        'timestamps': [],
        'values':     [],
        'available':  False,
        'reason':     reason,
    }


def _ok_metric(timestamps, values, unit):
    return {
        'timestamps': timestamps,
        'values':     values,
        'unit':       unit,
        'available':  bool(values),
        'reason':     None,
    }


# ── Public API ────────────────────────────────────────────────

def get_vm_metrics(resource_group: str, vm_name: str,
                   hours: int = 1) -> dict:
    """
    Return metrics dict for the given VM.

    Keys: cpu, memory, disk_read, disk_write,
          net_in, net_out, hours, vm_name, error.
    """
    hours     = max(1, min(hours, 24))
    interval  = 'PT5M' if hours <= 6 else 'PT1H'
    now       = datetime.now(timezone.utc)
    start     = now - timedelta(hours=hours)
    resource_id = _vm_resource_id(resource_group, vm_name)

    try:
        client = _get_monitor_client()
    except Exception as e:
        return _error_response(vm_name, hours, str(e))

    def fetch(metric_name, unit):
        try:
            ts, vals = _fetch_metric(
                client, resource_id, metric_name,
                start, now, interval
            )
            if not vals:
                return _empty_metric('No data in time window')
            return _ok_metric(ts, vals, unit)
        except Exception as exc:
            err = str(exc)
            # Memory raises ResourceNotFound / no data when AMA absent
            if metric_name == 'Available Memory Bytes':
                return _empty_metric(
                    'Azure Monitor Agent (AMA) not installed on this VM'
                )
            return _empty_metric(f'Error: {err}')

    cpu       = fetch('Percentage CPU',       'Percent')
    memory    = fetch('Available Memory Bytes', 'Bytes')
    disk_read = fetch('Disk Read Bytes',      'Bytes')
    disk_write= fetch('Disk Write Bytes',     'Bytes')
    net_in    = fetch('Network In Total',     'Bytes')
    net_out   = fetch('Network Out Total',    'Bytes')

    # Convert memory bytes → GB for display
    if memory['available']:
        memory['values'] = [
            round(v / (1024 ** 3), 2) for v in memory['values']
        ]
        memory['unit'] = 'GB'

    # Convert disk/network bytes → MB for display
    for m in (disk_read, disk_write, net_in, net_out):
        if m['available']:
            m['values'] = [
                round(v / (1024 ** 2), 3) for v in m['values']
            ]
            m['unit'] = 'MB'

    return {
        'vm_name':    vm_name,
        'hours':      hours,
        'interval':   interval,
        'cpu':        cpu,
        'memory':     memory,
        'disk_read':  disk_read,
        'disk_write': disk_write,
        'net_in':     net_in,
        'net_out':    net_out,
        'error':      None,
    }


def _error_response(vm_name, hours, error_msg):
    empty = _empty_metric('Service error')
    return {
        'vm_name':    vm_name,
        'hours':      hours,
        'interval':   'PT5M',
        'cpu':        empty,
        'memory':     empty,
        'disk_read':  empty,
        'disk_write': empty,
        'net_in':     empty,
        'net_out':    empty,
        'error':      error_msg,
    }
