import type { JsonValue } from "@/lib/types";

export function JsonBlock({ title, value }: { title: string; value: JsonValue }) {
  return <details className="json-block"><summary>{title}</summary><pre>{JSON.stringify(value, null, 2)}</pre></details>;
}
