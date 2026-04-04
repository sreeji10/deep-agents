import os
from pathlib import Path

from deepagents import SubAgent, create_deep_agent
from deepagents.backends import LocalShellBackend
from dotenv import load_dotenv
from langgraph.checkpoint.memory import InMemorySaver
from langchain_nvidia_ai_endpoints import ChatNVIDIA
from tools import internet_search

load_dotenv()
MODEL_NAME = os.getenv("MODEL_NAME", "nvidia/nemotron-3-nano-30b-a3b")
API_KEY = os.getenv("API_KEY")
ROOT_DIR = Path(__file__).resolve().parent

llm = ChatNVIDIA(model=MODEL_NAME, nvidia_api_key=API_KEY)
checkpointer = InMemorySaver()
backend = LocalShellBackend(
    root_dir=ROOT_DIR,
    virtual_mode=True,
    inherit_env=True,
)


system_prompt = """
You are a helpful research assistant.
Use available tools for factual and web-related requests.
Cite URLs when you used web tools.
Be clear, practical, and concise.
"""

subagents: list[SubAgent] = [
    {
        "name": "researcher",
        "description": "Best for web research, fact collection, and compiling cited findings from online sources.",
        "system_prompt": (
            "You are a specialist web researcher. Use web tools to gather reliable facts, "
            "prefer recent information for time-sensitive topics, and return concise findings "
            "with source URLs."
        ),
        "tools": [internet_search],
    },
    {
        "name": "coder",
        "description": "Best for code changes, debugging, file analysis, and shell-driven development tasks.",
        "system_prompt": (
            "You are a specialist coding agent. Prioritize reading code carefully, making minimal "
            "safe edits, and validating changes with shell commands when needed."
        ),
        "tools": [],
    },
]

agent = create_deep_agent(
    model=llm,
    tools=[internet_search],
    system_prompt=system_prompt,
    subagents=subagents,
    backend=backend,
    memory=["/memory/AGENTS.md"],
    skills=["/skills/project/"],
    interrupt_on={"execute": True, "edit_file": True},
    checkpointer=checkpointer,
)

if __name__ == "__main__":
    result = agent.invoke(
        {"messages": [{"role": "user", "content": "When is upcoming kerala election?"}]},
        config={"configurable": {"thread_id": "demo-thread"}},
    )
    print(result["messages"][-1].content)
