from openai import OpenAI
from dotenv import load_dotenv
import os
import json
import re
import logging

load_dotenv()

logger = logging.getLogger(__name__)

# OpenRouter API Key
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

def get_openrouter_client():
    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY mancante nel .env")
    return OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=key,
    )

# Fallback client instance for compatibility
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY or "missing-openrouter-key",
)

SYSTEM_RULES = """You are an expert crypto trading agent.
Analyze the provided portfolio status and market indicators, then decide on a trading action.
You MUST output ONLY a valid, raw JSON object (no extra commentary) adhering strictly to this schema:
{
    "operation": "open" | "close" | "hold",
    "symbol": "BTC" | "ETH" | "SOL",
    "direction": "long" | "short",
    "target_portion_of_balance": float (0.0 to 1.0),
    "leverage": integer (1 to 10),
    "stop_loss_percent": float (1.0 to 3.0),
    "reason": "Brief explanation of the decision (max 300 chars)"
}
"""

def _clean_and_parse_json(text: str) -> dict:
    """Extract and parse JSON safely from model response."""
    text = text.strip()
    
    # Check for markdown code fence
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if fence_match:
        text = fence_match.group(1).strip()
        
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Try finding the first '{' and last '}'
        start = text.find('{')
        end = text.rfind('}')
        if start != -1 and end != -1 and end > start:
            data = json.loads(text[start:end+1])
        else:
            raise ValueError(f"Could not parse valid JSON from response: {text}")
            
    # Validate and normalize essential fields
    if "operation" not in data:
        data["operation"] = "hold"
    if "symbol" not in data:
        data["symbol"] = "BTC"
    if "direction" not in data:
        data["direction"] = "long"
    if "target_portion_of_balance" not in data:
        data["target_portion_of_balance"] = 0.0
    if "leverage" not in data:
        data["leverage"] = 1
    if "stop_loss_percent" not in data:
        data["stop_loss_percent"] = 2.0
    if "reason" not in data:
        data["reason"] = "Default signal"

    # Clamp bounds
    try:
        data["target_portion_of_balance"] = max(0.0, min(1.0, float(data["target_portion_of_balance"])))
        data["leverage"] = max(1, min(10, int(data["leverage"])))
        data["stop_loss_percent"] = max(1.0, min(3.0, float(data["stop_loss_percent"])))
    except (ValueError, TypeError):
        pass

    return data

def previsione_trading_agent(prompt: str) -> dict:
    """
    Invia il prompt all'LLM tramite OpenRouter utilizzando il modello openrouter/free
    e restituisce il segnale di trading in formato dizionario/JSON.
    """
    cl = get_openrouter_client()

    response = cl.chat.completions.create(
        model="openrouter/free",
        messages=[
            {"role": "system", "content": SYSTEM_RULES},
            {"role": "user", "content": prompt}
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
    )

    output_text = response.choices[0].message.content
    return _clean_and_parse_json(output_text)
