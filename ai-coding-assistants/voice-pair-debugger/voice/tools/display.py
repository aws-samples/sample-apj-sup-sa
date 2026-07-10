import sys


async def show_code_suggestion(params, file_path: str, snippet: str, note: str = ""):
    """Display a code snippet or suggested fix in the developer's terminal.

    Call this when you have a concrete code change to show. The snippet is
    printed to the terminal where the bot is running, so the developer can read
    and copy it. Reading code aloud is awkward, so use this instead and then say
    a short spoken pointer such as "I have put the fix in your terminal".

    Args:
        file_path: The file the change applies to (e.g. get_users.mjs).
        snippet: The code to display. Include only the relevant lines.
        note: Optional one-line explanation shown above the snippet.
    """
    try:
        border = "=" * 64
        lines = [f"\n{border}", f"  SUGGESTED FIX: {file_path}", border]
        if note:
            lines.extend([note, ""])
        lines.append(snippet)
        lines.append(border + "\n")
        print("\n".join(lines), file=sys.stdout, flush=True)
        await params.result_callback({"status": "displayed", "file_path": file_path})
    except Exception as e:
        await params.result_callback({"error": str(e)})
