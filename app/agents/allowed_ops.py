# app/agents/allowed_ops.py

ALLOWED_OPS = {
    # analysis
    "ANALYZE_REPO",

    # formatting (keeps your previous refactor depth)
    "FORMAT_BLACK",

    # STEP 7: file mutation (safe, deterministic)
    "CREATE_FILE",
    "EDIT_FILE",
    "APPLY_PATCH",
    "DELETE_FILE",

    # STEP 8: safe deterministic append (needed for responsive CSS injection)
    "APPEND_FILE",

    # build
    "SCAFFOLD_NODE_BACKEND",
    "ADD_ENV_EXAMPLE",
    "UPDATE_README",

    # execution
    "RUN_TESTS_SAFE",
    "CAPTURE_DIFF",

    # verification (STEP 4)
    "VERIFY_CMD",
    "VERIFY_FILE_EXISTS",
    "VERIFY_HTTP_ENDPOINT",

    # control
    "SET_STATUS",
    "WAIT_FOR_APPROVAL",
    "COMMIT_PUSH_PR",
}
