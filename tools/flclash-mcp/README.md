# FlClash MCP

Local stdio MCP server for FlClash on this Mac.

It reads:

- `~/Library/Application Support/com.follow.clash/database.sqlite`
- `~/Library/Application Support/com.follow.clash/profiles/*.yaml`
- `~/Library/Application Support/com.follow.clash/config.yaml`

Tools:

- `get_status`: read local FlClash data/core status
- `list_profiles`: list FlClash profiles from SQLite and summarize YAML files
- `validate_profile`: check missing `server`/`port` and bad group references
- `import_url`: download a subscription YAML and add it to FlClash profiles
- `test_profile_delays`: run TCP reachability latency tests against profile nodes

Registered Codex config:

```toml
[mcp_servers.flclash]
command = "/Users/wowsofine/Developer/GitHub/AutoMergePublicNodes/.venv/bin/python"
args = ["/Users/wowsofine/Developer/GitHub/AutoMergePublicNodes/tools/flclash-mcp/bin/flclash-mcp"]
```

Smoke test:

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' \
  | .venv/bin/python tools/flclash-mcp/bin/flclash-mcp
```
