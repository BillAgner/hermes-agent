"""source-credibility-mcp: domain-tier + signal-based source credibility scoring.

Implements the credibility framework described for Hermes research output:
every cited source gets a 0-1 score with a transparent breakdown by component
(domain class, citation provenance, corroboration, recency, author
transparency, methodology). Tools are namespaced with a ``cred_`` prefix to
keep them distinct from generic verbs used by other MCPs.

The tier table lives in ``data/domains.json`` and is reloaded on every
request, so Bill can edit it without redeploying.

NOTE: Do not add ``from __future__ import annotations`` to this package.
Future annotations become strings and break FastMCP's ``Context`` typing
in tool decorators. Annotations below use bare types (X = None, not
``Optional[X]``) for the same reason.
"""

from credibility_mcp.__about__ import __version__

__all__ = ["__version__"]
