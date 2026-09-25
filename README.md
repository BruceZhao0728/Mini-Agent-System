# Mini-Agent-System

Author: Zichen Zhao

## Introduction

This is a series of mini Agent systems evolving from the simplest one to more complicated ones. It demonstrates how to construct an agent system step by step, and how to implement different types of agents and their interactions. The goal is to provide a clear understanding of the principles behind agent-based modeling and simulation.

## Versions

### 01 Bare Agent

[01-bare-agent](01-bare-agent/) demonstrates the simplest agent system, where agents have basic properties and behaviors. It serves as a foundation for understanding how agents operate in isolation.

Generally, a bare LLM follows the following structure:

```python
response = llm.generate_response(input)
```

However, a simple bare agent follows the following structure:

```mermaid
flowchart TD
    U["User Task"] --> LLM["LLM"]

    LLM --> D{"Tool call?"}

    D -- "No" --> F["Final Response"]

    D -- "Yes" --> A["Tool Call"]
    A --> T["Execute Tool"]
    T --> O["Tool Result / Observation"]
    O --> LLM
```

In pseudocode, the bare agent can be represented as follows:

```python
while not finished:

    response = llm(messages)

    if response.tool_call:
        observation = execute(response.tool_call)
        messages.append(observation)
    else:
        return response
```

### 02 ReAct Agent

[02-react-agent](02-react-agent/) introduces a ReAct (Reasoning + Acting) agent that can perceive its environment and make decisions based on its observations. This version showcases how agents can interact with their surroundings and adapt their behavior accordingly.

Here's the structure of a ReAct agent:

```mermaid
flowchart TD
    U["User Task"] --> LLM["LLM"]

    LLM --> R["Reason"]
    R --> A["Action"]

    A --> T["Execute Tool"]
    T --> O["Observation"]

    O --> LLM

    LLM --> F{"Final Answer?"}
    F -- "Yes" --> FA["Final Answer"]
    F -- "No / Tool Call" --> R
```

The core difference between a bare agent and a ReAct agent is that the ReAct agent can reason about its observations and decide whether to take an action or provide a final answer. This allows for more complex interactions and decision-making processes.

### 03 ReAct Agent with Error Handling

[03-react-agent-with-error-handling](03-react-agent-with-error-handling/) adds error handling to the ReAct agent. It is the same three-tool loop as 02; what changes is that every failure now has a route, decided by who can actually fix it — the model, the code, or nobody.

Here's the structure of an error-handling ReAct agent:

```mermaid
flowchart TD
    U["User Task"] --> LLM["LLM"]

    LLM -- "BadRequest · connection · 5xx" --> AE["Return the error message<br/>no retry — the SDK already did"]
    LLM --> R["Reason"]
    R --> A["Action"]

    A --> P{"Usable call?"}

    P -- "Bad JSON · past the per-round cap" --> E["Error: ... observation"]
    P -- "Yes" --> T["Execute Tool"]

    T --> C{"Failure class"}
    C -- "Deterministic<br/>FileNotFound · unknown tool · bad args" --> E
    C -- "Transient<br/>timeout · connection" --> RB["Retry with backoff"]
    RB --> T
    RB -. "exhausted" .-> E
    C -- "None" --> O["Observation"]

    E --> CL["Clip to MAX_OBS_CHARS"]
    O --> CL
    CL --> M["Append to history"]
    M --> LLM

    LLM --> F{"Final Answer?"}
    F -- "Yes" --> FA["Final Answer"]
    F -- "No / Tool Call" --> R
```

In pseudocode, the error-handling ReAct agent can be represented as follows:

```python
while not finished:

    try:
        response = llm(messages)
    except BadRequestError as e:              # our request is invalid — retrying cannot help
        return f"Error: {e}"

    if response.tool_call:
        args, error = parse(response.tool_call)   # syntax and type, one choke point
        if error:
            observation = error                   # deterministic — the model can fix this
        else:
            observation = call_tool(args)         # retries transient failures inside;
                                                  # returns "Error: ..." for the rest
        messages.append(clip(observation))        # bounded before it enters the history
    else:
        return response
```

The core difference between the 02 ReAct agent and the error-handling one is not that it fails less often, but that every failure is routed to whoever can fix it. Deterministic failures go straight back to the model as an observation, transient ones are absorbed by the code with bounded backoff, and a request the API rejects ends the run instead of being retried — so the agent turns failures into feedback instead of stopping on them.