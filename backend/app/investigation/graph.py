"""LangGraph orchestration graph for autonomous SRE multi-agent investigation."""

import logging
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.investigation.agents.aggregator import run_evidence_aggregator
from app.investigation.agents.historical_agent import run_historical_agent
from app.investigation.agents.incident_analyzer import run_incident_analyzer
from app.investigation.agents.log_agent import run_log_agent
from app.investigation.agents.metrics_agent import run_metrics_agent
from app.investigation.agents.rca_agent import run_rca_agent
from app.investigation.agents.trace_agent import run_trace_agent
from app.investigation.agents.validator_agent import run_validator_agent
from app.investigation.state import InvestigationState

logger = logging.getLogger("opspilot.investigation.graph")


def build_investigation_graph() -> Any:
    """
    Construct the multi-agent investigation workflow using LangGraph.

    Graph Topology:
        START
          |
          v
        incident_analyzer
          |---> metrics_agent ----->|
          |---> log_agent --------->|
          |---> trace_agent ------->|  (Parallel Investigation Fan-out)
          |---> historical_agent -->|
          v
        aggregator  (Evidence & Facts Aggregator)
          |
          v
        rca_agent   (RCA Hypotheses Synthesizer)
          |
          v
        validator_agent  (Evidence Validator & Confidence Calibrator)
          |
          v
        END
    """
    workflow = StateGraph(InvestigationState)

    # 1. Add agent nodes
    workflow.add_node("incident_analyzer", run_incident_analyzer)
    workflow.add_node("metrics_agent", run_metrics_agent)
    workflow.add_node("log_agent", run_log_agent)
    workflow.add_node("trace_agent", run_trace_agent)
    workflow.add_node("historical_agent", run_historical_agent)
    workflow.add_node("aggregator", run_evidence_aggregator)
    workflow.add_node("rca_agent", run_rca_agent)
    workflow.add_node("validator_agent", run_validator_agent)

    # 2. Add sequence & parallel edges
    workflow.add_edge(START, "incident_analyzer")

    # Fan-out from analyzer to 4 parallel telemetry investigators
    workflow.add_edge("incident_analyzer", "metrics_agent")
    workflow.add_edge("incident_analyzer", "log_agent")
    workflow.add_edge("incident_analyzer", "trace_agent")
    workflow.add_edge("incident_analyzer", "historical_agent")

    # Fan-in: all 4 investigators feed into aggregator
    workflow.add_edge("metrics_agent", "aggregator")
    workflow.add_edge("log_agent", "aggregator")
    workflow.add_edge("trace_agent", "aggregator")
    workflow.add_edge("historical_agent", "aggregator")

    # Linear synthesis and validation path
    workflow.add_edge("aggregator", "rca_agent")
    workflow.add_edge("rca_agent", "validator_agent")
    workflow.add_edge("validator_agent", END)

    return workflow.compile()


# Singleton compiled graph
_compiled_graph = None


def get_investigation_graph() -> Any:
    """Return the cached compiled LangGraph investigation graph."""
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_investigation_graph()
    return _compiled_graph


async def run_investigation_workflow(initial_state: InvestigationState) -> InvestigationState:
    """Execute the full multi-agent investigation workflow asynchronously."""
    graph = get_investigation_graph()
    logger.info(
        "Executing LangGraph investigation for incident %s", initial_state.get("incident_id")
    )
    final_state = await graph.ainvoke(initial_state)
    logger.info(
        "LangGraph investigation finished with status: %s", final_state.get("investigation_status")
    )
    return final_state
