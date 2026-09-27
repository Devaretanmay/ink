"""Provider integrations for Microloop.

Provider code lives here, never in ``microloop`` core. The core stays
provider-neutral: it recommends a direction (``escalate_model``, ``replan``,
``compact_context``) and an adapter in this tree knows how to perform it for a
particular runtime.
"""
