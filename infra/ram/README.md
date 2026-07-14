# RAM credential separation

Attach `read-policy.json` only to the RAM identity used by the read MCP server. Attach
`write-policy.json` only to the separate RAM identity used by the write MCP server. The
planner receives only the read MCP URL and read identity, so its credential cannot mutate
cloud resources even if a write tool name is exposed accidentally.

`ecs:DescribeInstances` and `cms:DescribeMetricLast` do not support a useful instance-level
resource restriction for these list and metric queries. Their `Resource` value therefore
must be `*`. The read policy has no mutate action. The write policy is restricted to ECS and
RDS instance ARNs in `ap-southeast-1`. The account segment remains `*` because this reusable
document does not know the deployment account ID. The RAM identity itself belongs to one
account, and Airlock separately enforces `BOUND_ACCOUNT_ID`.

The Airlock tool `StopDBInstances` maps to the Alibaba Cloud RAM action
`rds:StopDBInstance`. No describe permission is present in the write policy because all
pre-write and post-write reads go through the read MCP server.
