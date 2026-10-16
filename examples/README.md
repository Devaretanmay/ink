# Ink examples

Clean, copyable examples showing how to integrate Ink into production agent workflows.

---

## Examples

| File | Scenario | What it does |
| :--- | :--- | :--- |
| [`quickstart.py`](quickstart.py) | **First Fast Path** | Minimal 30-line script: wrap a site, observe decisions, compile local artifact, and run in 0.08ms. |
| [`tool_routing.py`](tool_routing.py) | **Agent tool dispatch** | Bypasses model calls for known tool selection (`search_kb`, `issue_refund`, `escalate_human`). |
| [`support_routing.py`](support_routing.py) | **Support triage** | Routes inbound customer inquiries into bounded support channels. |
| [`langgraph_agent.py`](langgraph_agent.py) | **LangGraph adapter** | Speeds up LangGraph conditional edges to avoid supervisor LLM calls on repeated graph paths. |
| [`pydantic_ai_agent.py`](pydantic_ai_agent.py) | **PydanticAI adapter** | Routes agent tool calls using qualified local Fast Paths. |

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
