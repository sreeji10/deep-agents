import type { Node, Edge } from "@xyflow/react";
import type { RunEvent } from "./types";

export type GraphNodeData = {
  label: string;
  type: string;
  level: string;
};

export function eventsToGraph(events: RunEvent[]): { nodes: Node<GraphNodeData>[]; edges: Edge[] } {
  const nodes: Node<GraphNodeData>[] = [];
  const edges: Edge[] = [];
  const added = new Set<string>();

  if (!events.length) return { nodes, edges };

  const rootId = "run";
  nodes.push({
    id: rootId,
    type: "default",
    position: { x: 0, y: 0 },
    data: { label: "Run", type: "system", level: "info" },
  });
  added.add(rootId);

  let lastSubagent: string | null = null;

  for (const event of events) {
    if (event.type === "subagent_update") {
      const subId = event.actor;
      if (!added.has(subId)) {
        nodes.push({
          id: subId,
          type: "default",
          position: { x: 0, y: 0 },
          data: { label: subId, type: "subagent", level: event.level },
        });
        added.add(subId);
        edges.push({ id: `${rootId}->${subId}`, source: rootId, target: subId });
      }
      lastSubagent = subId;
    }

    if (event.type === "model_started") {
      const modelId = "model";
      if (!added.has(modelId)) {
        nodes.push({
          id: modelId,
          type: "default",
          position: { x: 0, y: 0 },
          data: { label: "Model", type: "model", level: event.level },
        });
        added.add(modelId);
        edges.push({ id: `${rootId}->${modelId}`, source: rootId, target: modelId });
      }
    }

    if (event.type === "tool_called") {
      const toolName = (event.payload.name as string) ?? "tool";
      const toolId = `tool:${toolName}`;
      if (!added.has(toolId)) {
        nodes.push({
          id: toolId,
          type: "default",
          position: { x: 0, y: 0 },
          data: { label: toolName, type: "tool", level: event.level },
        });
        added.add(toolId);
        const parent = lastSubagent ?? rootId;
        edges.push({ id: `${parent}->${toolId}`, source: parent, target: toolId });
      }
    }

    if (event.type === "final_answer") {
      const answerId = "answer";
      if (!added.has(answerId)) {
        nodes.push({
          id: answerId,
          type: "default",
          position: { x: 0, y: 0 },
          data: { label: "Answer", type: "completion", level: event.level },
        });
        added.add(answerId);
        edges.push({ id: `${rootId}->${answerId}`, source: rootId, target: answerId });
      }
    }
  }

  return { nodes, edges };
}
