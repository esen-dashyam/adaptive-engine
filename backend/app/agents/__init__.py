"""
Adaptive Assessment Agents
===========================
All specialized LangGraph agents live here.

Agents:
  assessment_agent    – Phase A: standard selection → RAG → question generation (IRT-aware)
  evaluation_agent    – Phase B: score answers → Rasch update → misconception detection
  gap_agent           – KST: propagate knowledge state across full KG after assessment
  remediation_agent   – Generate targeted exercises for each identified gap
  recommendation_agent – Build next-step learning path from KST frontier
  orchestrator        – Master LangGraph tying all agents into one adaptive loop

Algorithms (pure-math, no LLM):
  rasch               – Rasch 1PL IRT (θ estimation, Fisher information)
  irt_selector        – Maximum Information Gain question selector
  kst                 – Knowledge Space Theory (success/failure propagation)

LLM:
  vertex_llm          – Vertex AI (ADC) primary + google-generativeai (API key) fallback
"""

from backend.app.agents.orchestrator import get_orchestrator

__all__ = ["get_orchestrator"]
