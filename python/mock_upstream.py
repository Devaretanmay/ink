from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import uvicorn

app = FastAPI()

@app.post("/v1/chat/completions")
async def completions(request: Request):
    data = await request.json()
    
    # Mock upstream simulates an LLM that responds with tool calls matching
    # the instruction in the user's last message.
    last_msg = data.get("messages", [])[-1].get("content", "")
    requested_tool = "unknown"
    args = "{}"
    if "delete_line" in last_msg:
        requested_tool = "delete_line"
        args = '{"line": 5}'
    elif "remove_line" in last_msg:
        requested_tool = "remove_line"
        args = '{"line": 5}'
    elif "erase_line" in last_msg:
        requested_tool = "erase_line"
        args = '{"line": 5}'
    elif "clear_line" in last_msg:
        requested_tool = "clear_line"
        args = '{"line": 5}'

    return JSONResponse(content={
        "id": "chatcmpl-mock",
        "object": "chat.completion",
        "created": 1677652288,
        "model": "gpt-4",
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "I encountered an error previously: 'Line 5 is already empty'. Let me try again.",
                "tool_calls": [{
                    "id": "call_mock",
                    "type": "function",
                    "function": {
                        "name": requested_tool,
                        "arguments": args
                    }
                }]
            },
            "finish_reason": "tool_calls"
        }]
    })

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8082)
