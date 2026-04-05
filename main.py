import os
from typing import Any

from requests import RequestException
from backend.agent_runtime import (
    get_agent,
    looks_like_placeholder,
    recover_final_answer,
)
from dotenv import load_dotenv

load_dotenv()
agent = get_agent()
DEBUG_TRACE = os.getenv("DEBUG_TRACE", "0") == "1"


def print_tool_trace(messages: list[Any]) -> None:
    """Print a readable trace of tool calls so delegation is visible."""
    print("\n=== Tool Trace ===")
    found = False
    for message in messages:
        tool_calls = getattr(message, "tool_calls", None)
        if not tool_calls:
            continue
        for call in tool_calls:
            found = True
            print(f"- tool: {call.get('name')} | args: {call.get('args')}")
    if not found:
        print("- No tool calls were made in this run.")


def print_message_flow(messages: list[Any]) -> None:
    """Print detailed message flow for debugging tool orchestration."""
    print("\n=== Message Flow ===")
    for idx, message in enumerate(messages):
        msg_type = type(message).__name__
        name = getattr(message, "name", None)
        content = str(getattr(message, "content", ""))
        preview = content.replace("\n", " ")[:180]
        print(f"{idx}. {msg_type}" + (f" (name={name})" if name else ""))
        if preview:
            print(f"   content: {preview}")
        tool_calls = getattr(message, "tool_calls", None)
        if tool_calls:
            for call in tool_calls:
                print(f"   tool_call -> {call.get('name')} args={call.get('args')}")


def run_prompt(prompt: str, thread_id: str = "demo-thread") -> None:
    """Run one prompt and print both trace + final answer."""
    try:
        result = agent.invoke(
            {"messages": [{"role": "user", "content": prompt}]},
            config={"configurable": {"thread_id": thread_id}},
        )
    except RequestException as exc:
        print(f"Network/API error while calling the model: {exc}")
        return
    except Exception as exc:
        print(f"Run failed: {exc}")
        return

    messages = result.get("messages", [])
    print_tool_trace(messages)
    if DEBUG_TRACE:
        print_message_flow(messages)

    final_answer = str(messages[-1].content) if messages else "(No response)"
    if looks_like_placeholder(final_answer):
        recovered = recover_final_answer(thread_id)
        if recovered:
            final_answer = recovered

    print("\n=== Final Answer ===")
    print(final_answer)


if __name__ == "__main__":
    print("Subagent Test Menu")
    print("1) Force researcher subagent")
    print("2) Let agent decide")
    print("3) Custom prompt")
    choice = input("Choose 1/2/3: ").strip()

    if choice == "1":
        test_prompt = (
            "Use the researcher subagent to find the latest Kerala election timeline. "
            "Return 3 bullet points and include source URLs."
        )
    elif choice == "2":
        test_prompt = "When is the upcoming Kerala election? Include source URLs."
    else:
        test_prompt = input("Enter your prompt: ").strip()

    run_prompt(test_prompt)
