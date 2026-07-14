export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };

export type Precondition = {
  expected_status: string;
  observed_at: string;
};

export type Evidence = {
  idle_window_days: number;
  cpu_avg: number;
  cpu_max: number;
  mem_avg: number;
  samples: number;
  collected_at: string;
  monitor_provenance: Array<Record<string, JsonValue>>;
  disagreement?: Record<string, JsonValue> | null;
  memory_receipt?: Record<string, JsonValue> | null;
};

export type ProposedAction = {
  schema_version: string;
  cloud_account_id: string;
  region: string;
  resource_id: string;
  action: "StopInstances" | "DeleteInstances" | "StopDBInstances";
  precondition: Precondition;
  evidence: Evidence;
  policy_reason: string;
  stage: "stop" | "delete";
  created_at: string;
};

export type ProposalView = {
  approval_id: string;
  action_hash: string;
  status: "pending" | "issued" | "rejected" | "consumed" | "expired";
  action: ProposedAction | null;
  approver: string | null;
};
