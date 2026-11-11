"""EvalSentry `python` provider: runs the real agent (real model, real MCP server) on one question.

Configure the model with environment variables:
  EVAL_PROVIDER=anthropic (default)  ANTHROPIC_API_KEY=...  SALES_AGENT_MODEL=claude-sonnet-5-5
  EVAL_PROVIDER=openai  EVAL_MODEL=llama3.1  EVAL_BASE_URL=http://localhost:11434/v1   (Ollama, vLLM, OpenAI...)
"""

from __future__ import annotations

import os
from pathlib import Path

from mcp import Client

from sales_agent.agent import AnthropicModel, Model, OpenAICompatibleModel, run_agent
from sales_agent.seed import build
from sales_agent.server import create_server

DB = Path(__file__).resolve().parent.parent / "data" / "shop.db"


def _model() -> Model:
    if os.environ.get("EVAL_PROVIDER", "anthropic") == "openai":
        return OpenAICompatibleModel(os.environ["EVAL_MODEL"], base_url=os.environ.get("EVAL_BASE_URL", "https://api.openai.com/v1"))
    return AnthropicModel()


async def answer(prompt: str, vars: dict) -> dict:
    if not DB.exists():
        build(DB)
    async with Client(create_server(DB)) as client:
        res = await run_agent(prompt, client, _model())
    return {
        "output": {"answer": res.answer, "tools_used": res.tools_used, "stopped": res.stopped, "turns": res.turns},
        "input_tokens": res.input_tokens,
        "output_tokens": res.output_tokens,
        "model": res.model,
    }
