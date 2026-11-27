"""What the judges see of the agent's tool calls: each action_service call
with the result the tool returned. Without the results, a judge checking a
date or amount the agent quoted ("deadline December 19") had nothing to check
it against and called it fabricated (PC-002, 2026-11-27)."""
import json


def actions_with_results(agent_response: dict) -> str:
    actions = [dict(a) for a in agent_response.get("actions_taken") or [] if isinstance(a, dict)]
    results = [r for r in agent_response.get("action_results") or [] if isinstance(r, dict)]
    tool_calls = [a for a in actions if a.get("service") == "action_service"]
    if results and len(results) == len(tool_calls):
        for action, result in zip(tool_calls, results):
            action["result"] = result
    elif results:
        # Can't pair them one-to-one: still show what the tools returned.
        actions.append({"tool_results": results})
    return json.dumps(actions)
