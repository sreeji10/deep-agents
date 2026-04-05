import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from deepagents import SubAgent, create_deep_agent
from deepagents.backends import LocalShellBackend
from dotenv import load_dotenv
from langchain_nvidia_ai_endpoints import ChatNVIDIA
from langgraph.checkpoint.memory import InMemorySaver

from tools import internet_search

load_dotenv()
MODEL_NAME = os.getenv("MODEL_NAME", "nvidia/nemotron-3-nano-30b-a3b")
API_KEY = os.getenv("API_KEY")
ROOT_DIR = Path(__file__).resolve().parent.parent

SYSTEM_PROMPT = """
You are a helpful research assistant.
Use available tools for factual and web-related requests.
Cite URLs when you used web tools.
Be clear, practical, and concise.
Never output placeholder tags such as <task_...> or internal orchestration text.
After tools/subagents return, always provide the final user-facing answer directly.
Never answer with progress updates like "started", "initiated", or "I will provide later".
If the user asks for source URLs, you must include clickable http(s) URLs in the final answer.
"""

SUBAGENTS: list[SubAgent] = [
    {
        "name": "researcher",
        "description": "Best for web research, fact collection, and compiling cited findings from online sources.",
        "system_prompt": (
            "You are a specialist web researcher. Use web tools to gather reliable facts, "
            "prefer recent information for time-sensitive topics, and return concise findings "
            "with source URLs."
        ),
        "tools": [internet_search],
    }
]


@lru_cache(maxsize=1)
def get_agent() -> Any:
    llm = ChatNVIDIA(model=MODEL_NAME, nvidia_api_key=API_KEY)
    checkpointer = InMemorySaver()
    backend = LocalShellBackend(
        root_dir=ROOT_DIR,
        virtual_mode=True,
        inherit_env=True,
    )
    return create_deep_agent(
        model=llm,
        tools=[internet_search],
        system_prompt=SYSTEM_PROMPT,
        subagents=SUBAGENTS,
        backend=backend,
        memory=["/memory/AGENTS.md"],
        skills=["/skills/project/"],
        interrupt_on={"execute": True, "edit_file": True},
        checkpointer=checkpointer,
    )


def looks_like_placeholder(content: str) -> bool:
    lowered = content.lower()
    markers = [
        "<task_",
        "we need to wait for subagent result",
        "it's asynchronous",
        "let's call and see",
        "has been initiated",
        "i'll provide",
        "i will provide",
        "once the researcher",
        "once complete",
        "will return shortly",
        "working on it",
        "still processing",
    ]
    return any(marker in lowered for marker in markers)


def prompt_requires_sources(prompt: str) -> bool:
    lowered = prompt.lower()
    markers = [
        "source url",
        "source urls",
        "include url",
        "include urls",
        "with urls",
        "cite",
        "citations",
        "sources",
    ]
    return any(marker in lowered for marker in markers)


def needs_recovery(prompt: str, answer: str) -> bool:
    if not answer.strip():
        return True
    if looks_like_placeholder(answer):
        return True
    if prompt_requires_sources(prompt) and not extract_citations(answer):
        return True
    return False


def recover_final_answer(thread_id: str) -> str | None:
    recovery_prompt = (
        "Now provide the final user-facing answer using results already in this conversation. "
        "Do not include internal tags, tool placeholders, or orchestration commentary. "
        "Do not ask to wait. Give the final answer directly with citations if available."
    )
    try:
        recovery_result = get_agent().invoke(
            {"messages": [{"role": "user", "content": recovery_prompt}]},
            config={"configurable": {"thread_id": thread_id}},
        )
    except Exception:
        return None

    recovery_messages = recovery_result.get("messages", [])
    if not recovery_messages:
        return None
    return str(recovery_messages[-1].content)


def recover_final_answer_with_sources(
    thread_id: str, original_prompt: str
) -> str | None:
    recovery_prompt = (
        "The prior answer was incomplete. Produce the final user-facing answer now for this request: "
        f"{original_prompt}\n"
        "You must include source URLs as full http(s) links. "
        "Do not output progress/status text. Return the completed answer directly."
    )
    try:
        recovery_result = get_agent().invoke(
            {"messages": [{"role": "user", "content": recovery_prompt}]},
            config={"configurable": {"thread_id": thread_id}},
        )
    except Exception:
        return None

    recovery_messages = recovery_result.get("messages", [])
    if not recovery_messages:
        return None
    return str(recovery_messages[-1].content)


def extract_citations(text: str) -> list[str]:
    found = re.findall(r"https?://[^\s)\]>\"']+", text)
    unique: list[str] = []
    seen: set[str] = set()
    for item in found:
        cleaned = item.rstrip(".,;")
        if cleaned not in seen:
            unique.append(cleaned)
            seen.add(cleaned)
    return unique


def direct_search_fallback_answer(prompt: str, max_results: int = 5) -> str | None:
    try:
        backend = (
            "news"
            if any(token in prompt.lower() for token in ("latest", "recent"))
            else "text"
        )
        results = internet_search.invoke(
            {"query": prompt, "max_results": max_results, "backend": backend}
        )
    except Exception:
        return None

    if not isinstance(results, list) or not results:
        return None

    usable = [item for item in results if isinstance(item, dict) and item.get("link")]
    if not usable:
        return None

    lines = ["Here are recent findings with sources:"]
    for item in usable[:3]:
        title = str(item.get("title", "")).strip() or "Untitled"
        date = str(item.get("date", "")).strip()
        link = str(item.get("link", "")).strip()
        snippet = str(item.get("snippet", "")).strip()

        prefix = f"- {title}"
        if date:
            prefix += f" ({date[:10]})"
        lines.append(prefix)
        if snippet:
            lines.append(f"  {snippet}")
        lines.append(f"  Source: {link}")

    return "\n".join(lines)
