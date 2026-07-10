from pipecat.adapters.schemas.tools_schema import ToolsSchema

from .cloudwatch import query_cloudwatch_logs
from .display import show_code_suggestion
from .lambda_tools import describe_lambda_function
from .local_files import list_files, read_file
from .xray import get_xray_trace_summaries

TOOL_FUNCTIONS = [
    query_cloudwatch_logs,
    describe_lambda_function,
    get_xray_trace_summaries,
    read_file,
    list_files,
    show_code_suggestion,
]


def get_tools_schema() -> ToolsSchema:
    """Build a ToolsSchema from all registered direct functions."""
    return ToolsSchema(standard_tools=TOOL_FUNCTIONS)


def register_tools(llm) -> None:
    """Register all tool functions with the LLM service."""
    for fn in TOOL_FUNCTIONS:
        llm.register_direct_function(fn)
