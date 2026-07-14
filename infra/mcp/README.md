# MCP tool allowlists

Pass the complete contents of `read-visible-tools.txt` to `--visible-tools` on the read
server, and `write-visible-tools.txt` to the same flag on the write server.

- **Read** (`CommonAPICaller,CMS_GetMetricList`): the read server also needs
  `--services ecs,cms` so `CommonAPICaller` registers. `src/agent/monitor.py` calls
  `ecs:DescribeInstances` through `CommonAPICaller` (enumeration + instance state) and the
  forked `CMS_GetMetricList` for windowed CPU/mem/disk. See `fork/` and the `Dockerfile`.
- **Write** (`ECS_StopInstances,ECS_DeleteInstances,RDS_StopDBInstance`): dynamic API tools
  derived from the allowlist names. `src/executor/alibaba_write.py` maps each `ActionName`
  to these exact tool names. `CommonAPICaller` is NOT whitelisted here, so the generic
  passthrough is never registered on the write server.

The allowlists are defense in depth; RAM credential separation is the enforcement boundary.
The image (`Dockerfile`) pins upstream `0.9.27` and appends one read-only windowed-metric
tool (`fork/cms_get_metriclist.py.in`) that upstream lacks. Response shapes, CMS metric
names, and the `DescribeMetricListRequest` field names are marked for live verification in
the design implementation notes before the first live run.
