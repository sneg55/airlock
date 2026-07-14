// Human-readable labels for the raw action and stage identifiers that come off the
// wire, so operators never have to read API enum names like "StopInstances".

const ACTION_LABELS: Record<string, string> = {
  StopInstances: "Stop instance",
  DeleteInstances: "Delete instance",
  StopDBInstances: "Stop database",
};

export function actionLabel(action: string | undefined): string {
  if (!action) return "Unknown action";
  return ACTION_LABELS[action] ?? action;
}

export function stageLabel(stage: string | undefined): string {
  if (!stage) return "Unknown";
  return stage.charAt(0).toUpperCase() + stage.slice(1);
}
