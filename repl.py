"""Interactive terminal preview for AgentUnreal without a real UE editor."""

import sys
from pathlib import Path

from agent import Agent, Config


def main():
    config = Config.from_yaml()
    agent = Agent(config)

    print("AgentUnreal preview REPL")
    print("Type a task (e.g. 'Add a stamina attribute') or 'quit' to exit.")
    print()

    while True:
        try:
            prompt = input("\u003e ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if prompt.lower() in {"quit", "exit", "q"}:
            break

        if not prompt:
            continue

        print("-" * 60)
        result = agent.run(prompt)
        print("-" * 60)
        print(result)
        print()


if __name__ == "__main__":
    main()
