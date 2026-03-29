import os

from deepagents import create_deep_agent
from dotenv import load_dotenv
from langchain_nvidia_ai_endpoints import ChatNVIDIA
from tools import internet_search

load_dotenv()
MODEL_NAME = os.getenv("MODEL_NAME", "nvidia/nemotron-3-nano-30b-a3b")
API_KEY = os.getenv("API_KEY")

llm = ChatNVIDIA(model=MODEL_NAME, nvidia_api_key=API_KEY)


system_prompt = """
You are a helpful research assistant.
Use available tools for factual and web-related requests.
Cite URLs when you used web tools.
Be clear, practical, and concise.
"""

agent = create_deep_agent(
    model=llm,
    tools=[internet_search],
    system_prompt=system_prompt,
)

if __name__ == "__main__":
    result = agent.invoke(
        {"messages": [{"role": "user", "content": "When is upcoming kerala election?"}]}
    )
    print(result["messages"][-1].content)
