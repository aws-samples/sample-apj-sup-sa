"""Custom-resource handler code for HVI seed loads.

Each subpackage holds a self-contained ``handler.py`` that is staged into a
Lambda asset (with its vendored ``seed/`` JSON) and invoked by the CDK
``Provider`` framework. Handlers are idempotent and fail loud: a missing or
empty seed raises, which surfaces as a FAILED custom resource and rolls the
stack back.
"""
