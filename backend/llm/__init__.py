"""
RSE Intelligence — LLM client package.

Usage:
    from llm.client import generate

    text = generate(system_prompt, user_prompt)
    # Returns str on success, None if all providers fail.
"""
from .client import generate

__all__ = ["generate"]
