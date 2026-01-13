import sqlite3

DB_PATH = "app/storage/repo_context.db"


class DependencyGraph:
    def __init__(self):
        self.conn = sqlite3.connect(DB_PATH)

    def files_calling_function(self, function_name: str) -> list[str]:
        cur = self.conn.cursor()
        cur.execute("""
        SELECT DISTINCT files.path
        FROM calls
        JOIN functions ON calls.caller_id = functions.id
        JOIN files ON functions.file_id = files.id
        WHERE calls.callee_name = ?
        """, (function_name,))
        return [row[0] for row in cur.fetchall()]

    def impact_count_for_function(self, function_name: str) -> int:
        """
        How many distinct files call this function (blast radius proxy).
        """
        return len(self.files_calling_function(function_name))
