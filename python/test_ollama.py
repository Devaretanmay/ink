import requests
import json
import uuid
import time

PROXY_URL = "http://127.0.0.1:8080/v1/chat/completions"
MODEL = "ornith:9b"

tools = [
    {
        "type": "function",
        "function": {
            "name": "delete_line",
            "description": "Delete a specific line from a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "line": {"type": "integer"}
                },
                "required": ["line"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "remove_line",
            "description": "Remove a specific line from a file (alternative to delete_line).",
            "parameters": {
                "type": "object",
                "properties": {
                    "line": {"type": "integer"}
                },
                "required": ["line"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "erase_line",
            "description": "Erase a specific line from a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "line": {"type": "integer"}
                },
                "required": ["line"]
            }
        }
    }
]

def run_agent():
    session_id = str(uuid.uuid4())
    print(f"Starting Agent with Session: {session_id}")
    
    headers = {
        "x-session-id": session_id,
        "Content-Type": "application/json"
    }

    messages = [
        {"role": "system", "content": "You are a helpful coding assistant. If a tool fails, try a different tool."},
        {"role": "user", "content": "Delete line 5 from the file. You have multiple tools. If one fails, try another."}
    ]

    for step in range(5):
        print(f"\n--- Step {step + 1} ---")
        payload = {
            "model": MODEL,
            "messages": messages,
            "tools": tools,
            "temperature": 0.7
        }

        try:
            resp = requests.post(PROXY_URL, json=payload, headers=headers)
            resp_json = resp.json()
        except Exception as e:
            print("Failed to reach proxy:", e)
            break

        if "choices" not in resp_json:
            print("Unexpected response:", json.dumps(resp_json, indent=2))
            break

        msg = resp_json["choices"][0]["message"]
        print("Agent Content:", msg.get("content"))

        # Check if proxy blocked it!
        if msg.get("content") and "SYSTEM INTERCEPT" in msg.get("content"):
            print("\nSUCCESS! Loop was detected and blocked by the Proxy!")
            break
            
        if "tool_calls" in msg and msg["tool_calls"]:
            tool_call = msg["tool_calls"][0]
            func_name = tool_call["function"]["name"]
            func_args = tool_call["function"]["arguments"]
            print(f"Agent called tool: {func_name}({func_args})")
            
            # Record assistant's message in history
            messages.append(msg)
            
            # Mock execution error
            error_msg = f"Line 5 is already empty or protected."
            print(f"Mocking error: {error_msg}")
            
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call["id"],
                "name": func_name,
                "content": error_msg
            })
            
            # Small delay before next step
            time.sleep(1)
        else:
            print("Agent stopped using tools.")
            break

if __name__ == "__main__":
    run_agent()
