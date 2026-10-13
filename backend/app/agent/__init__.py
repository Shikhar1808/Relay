"""The chat agent: a LangGraph graph that asks the model, runs the tools it names
through the MCP gateway as the signed-in user, and repeats until it has an answer.

    state.py    what is remembered between steps (saved) and what is handed in per request (not saved)
    nodes.py    the seven steps and the two decisions between them
    graph.py    wires them together; run() and read() are what the API uses
    gateway.py  the two calls to the gateway
"""
