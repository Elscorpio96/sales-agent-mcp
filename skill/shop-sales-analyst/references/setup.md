# Setting up the sales-agent MCP server

Requires Python 3.11 or newer.

## 1. Install

```bash
pip install "sales-agent-mcp @ git+https://github.com/Elscorpio96/sales-agent-mcp"
```

## 2. Load data

**Try it with demo data** (a fictional shop, 120 days of generated sales):

```bash
sales-agent seed --db ~/shop-data/shop.db
```

**Or load the shop's own data** from CSV exports (see [csv-format.md](csv-format.md); templates are in `templates/`):

```bash
sales-agent import --db ~/shop-data/shop.db --shop-name "Mai Kanti Mini Mart" --city Bauchi \
  --products products.csv --sales sales.csv --customers customers.csv --payments payments.csv
```

Re-run the import with `--force` whenever there is new data. Check it worked:

```bash
sales-agent call shop_info --db ~/shop-data/shop.db
```

## 3. Connect it to the agent

`sales-agent config --db ~/shop-data/shop.db` prints the exact command and arguments for this machine. Use them below.

**Claude Code**

```bash
claude mcp add sales-agent -- python -m sales_agent.server --db /full/path/to/shop.db --log-level WARNING
```

**Claude Desktop** — add to `claude_desktop_config.json`, then restart the app:

```json
{ "mcpServers": { "sales-agent": { "command": "python", "args": ["-m", "sales_agent.server", "--db", "/full/path/to/shop.db"] } } }
```

**Cursor** — the same `mcpServers` block in `~/.cursor/mcp.json` (or `.cursor/mcp.json` in a project).

**VS Code (GitHub Copilot)** — in `.vscode/mcp.json`:

```json
{ "servers": { "sales-agent": { "type": "stdio", "command": "python", "args": ["-m", "sales_agent.server", "--db", "/full/path/to/shop.db"] } } }
```

**Other MCP clients** — any client that runs a stdio server works with the same command. For network clients:
`sales-agent-server --db /full/path/to/shop.db --http --port 8000` serves Streamable HTTP at
`http://127.0.0.1:8000/mcp` (this machine only, no authentication — do not expose it to the internet).

Use the full path to the Python that has the package installed (inside a virtual environment, e.g.
`/home/me/.venv/bin/python` or `C:\Users\me\.venv\Scripts\python.exe`) if `python` alone is not that one.

## 4. Troubleshooting

- *Tools do not appear:* restart the client after editing its config; check the path to the database exists.
- *"database not found":* the `--db` path is wrong or the data was never loaded (step 2).
- *Figures look old:* the server reports "today" as the last sale in the data; re-import newer exports.
- *Audit trail:* add `--audit-log /full/path/audit.jsonl` to the server arguments to record every tool call.
