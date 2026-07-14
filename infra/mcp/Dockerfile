# Airlock MCP server image. One image serves both the read and the write Function
# Compute functions; each is constrained by its own --visible-tools allowlist and RAM
# credential at deploy time. Pins upstream alibaba-cloud-ops-mcp-server 0.9.27 and applies
# a single read-only overlay (a windowed CloudMonitor metric tool) the upstream lacks.
FROM python:3.13-slim

RUN pip install --no-cache-dir alibaba-cloud-ops-mcp-server==0.9.27

# Append the read-only windowed-metric tool to the installed cms_tools module. The append
# runs in cms_tools' own namespace, so the fragment reuses its imports. Registration is
# still gated by --visible-tools per server, so this is inert on the write server.
COPY fork/cms_get_metriclist.py.in /tmp/cms_get_metriclist.py.in
RUN target="$(python -c 'import alibaba_cloud_ops_mcp_server.tools.cms_tools as m; print(m.__file__)')" \
    && cat /tmp/cms_get_metriclist.py.in >> "$target" \
    && python -c 'import alibaba_cloud_ops_mcp_server.tools.cms_tools as m; assert any(t.__name__ == "CMS_GetMetricList" for t in m.tools), "overlay not registered"'

ENTRYPOINT ["alibaba-cloud-ops-mcp-server"]
