"""Offline question-bank pipeline (Feature 002 / Gate G2).

Operator-triggered, entirely off the live and async session paths: generate (T034) ->
review/approve (T036) -> Titan-embed (T037). Selection at session-prep is a pure DB +
pgvector query with zero live LLM (see backend/src/prep/). Adds no always-on infra.
"""
