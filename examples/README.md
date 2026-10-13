# Ink Examples

This directory contains clean, copyable examples showing how to integrate Ink into production agent and model workflows.

---

## Examples

| File | Scenario | Description |
| :--- | :--- | :--- |
| [`quickstart.py`](quickstart.py) | **First Fast Path** | Minimal end-to-end example: register site, observe decisions, compile local artifact, serve. |
| [`tool_routing.py`](tool_routing.py) | **Agent Tool Dispatch** | Accelerate bounded tool selection (`search_kb`, `issue_refund`, `escalate_human`). |
| [`support_routing.py`](support_routing.py) | **Support Triage** | Categorize inbound customer inquiries into bounded support channels. |
| [`langgraph_agent.py`](langgraph_agent.py) | **LangGraph Adapter** | Wrap LangGraph conditional edges to bypass supervisor LLM calls on repeated graph paths. |
| [`pydantic_ai_agent.py`](pydantic_ai_agent.py) | **PydanticAI Adapter** | Route agent tool calls using qualified local Fast Paths. |

---

## Running

Run any example directly with Python:

```bash
python examples/quickstart.py
python examples/tool_routing.py
python examples/support_routing.py
python examples/langgraph_agent.py
python examples/pydantic_ai_agent.py
```
