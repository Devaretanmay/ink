use serde_json::{Value, json};

pub fn microloop_expand_tool_openai() -> Value {
    json!({
        "type": "function",
        "function": {
            "name": "microloop_expand",
            "description": "Retrieve a specific item from a compressed JSON array by its index. Use this when output contains a marker like '[N more items. Use microloop_expand(...) to view specific rows]' — call this tool with the hash and index from the marker to retrieve one item at a time.",
            "strict": true,
            "parameters": {
                "type": "object",
                "properties": {
                    "hash": {
                        "type": "string",
                        "description": "The hex hash from the marker text in the compressed output."
                    },
                    "index": {
                        "type": "integer",
                        "description": "Zero-based index of the row to retrieve."
                    }
                },
                "required": ["hash", "index"],
                "additionalProperties": false
            }
        }
    })
}

pub fn microloop_expand_tool_anthropic() -> Value {
    json!({
        "name": "microloop_expand",
        "description": "Retrieve a specific item from a compressed JSON array by its index. Use this when output contains a marker like '[N more items. Use microloop_expand(...) to view specific rows]' — call this tool with the hash and index from the marker to retrieve one item at a time.",
        "input_schema": {
            "type": "object",
            "properties": {
                "hash": {
                    "type": "string",
                    "description": "The hex hash from the marker text in the compressed output."
                },
                "index": {
                    "type": "integer",
                    "description": "Zero-based index of the row to retrieve."
                }
            },
            "required": ["hash", "index"]
        }
    })
}

pub fn get_tool_definitions() -> Value {
    json!({
        "openai": microloop_expand_tool_openai(),
        "anthropic": microloop_expand_tool_anthropic(),
    })
}
