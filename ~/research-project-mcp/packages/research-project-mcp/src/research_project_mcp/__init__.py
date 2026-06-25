"""research-project-mcp: structured epistemic state for multi-session research.

See ``schema.py`` for the data model and ``storage.py`` for the on-disk
format. The MCP server (``__main__.py``) exposes 16 tools for projects,
evidence, hypotheses, questions, contradictions, dead-ends, and reports.
"""

from .schema import (
    Contradiction,
    DeadEnd,
    Evidence,
    Hypothesis,
    Question,
    ResearchProject,
    TimelineEvent,
)
from .storage import (
    DEFAULT_STORAGE_ROOT,
    ProjectAlreadyExistsError,
    ProjectNotFoundError,
    archive_project,
    create_project,
    delete_project,
    list_projects,
    load_project,
    next_id,
    project_exists,
    save_project,
)

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "ResearchProject",
    "Hypothesis",
    "Question",
    "Evidence",
    "Contradiction",
    "DeadEnd",
    "TimelineEvent",
    "DEFAULT_STORAGE_ROOT",
    "ProjectNotFoundError",
    "ProjectAlreadyExistsError",
    "list_projects",
    "project_exists",
    "load_project",
    "save_project",
    "create_project",
    "delete_project",
    "archive_project",
    "next_id",
]