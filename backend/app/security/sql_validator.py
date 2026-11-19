import re
from typing import Any, Dict, List, Optional, Tuple
import sqlglot
from sqlglot import exp


class SQLValidationError(ValueError):
    """Raised when SQL fails AST read-only validation or guardrail checks."""


# AST node types strictly prohibited anywhere in the query tree
FORBIDDEN_AST_NODES = tuple(
    node
    for node in (
        getattr(exp, "Insert", None),
        getattr(exp, "Update", None),
        getattr(exp, "Delete", None),
        getattr(exp, "Merge", None),
        getattr(exp, "Drop", None),
        getattr(exp, "Alter", None),
        getattr(exp, "Create", None),
        getattr(exp, "TruncateTable", None),
        getattr(exp, "Command", None),
        getattr(exp, "Grant", None),
        getattr(exp, "Copy", None),
        getattr(exp, "Call", None),
    )
    if node is not None
)

_SAFE_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*(\.[A-Za-z_][A-Za-z0-9_$]*){0,2}$")


def _validate_identifier(identifier: str, label: str) -> str:
    cleaned = identifier.strip()
    if not _SAFE_IDENT_RE.match(cleaned):
        raise SQLValidationError(f"Unsafe or invalid SQL identifier for {label}: {identifier!r}")
    return cleaned


def _check_recursive_depth_guardrail(ast: exp.Expression, max_hierarchy_depth: int) -> None:
    """Ensure any WITH RECURSIVE CTE enforces a depth <= max_hierarchy_depth bound."""
    with_clause = ast.args.get("with")
    if not with_clause or not with_clause.args.get("recursive"):
        return

    # Look for a LTE or LT comparison on a depth/level column in the recursive CTE
    cte_sql = with_clause.sql(dialect="snowflake")
    depth_pattern = re.compile(
        r"\b(?:depth|level|lvl|hierarchy_depth)\s*(<=|<)\s*(\d+)\b",
        re.IGNORECASE,
    )
    matches = depth_pattern.findall(cte_sql)
    if not matches:
        raise SQLValidationError(
            f"Recursive CTE must include an explicit depth bound (e.g., WHERE depth <= {max_hierarchy_depth})."
        )

    for op, num_str in matches:
        limit = int(num_str)
        effective_limit = limit if op == "<=" else limit - 1
        if effective_limit > max_hierarchy_depth:
            raise SQLValidationError(
                f"Recursive CTE depth bound ({effective_limit}) exceeds MAX_HIERARCHY_DEPTH ({max_hierarchy_depth})."
            )


def validate_readonly_sql(
    sql: str,
    max_hierarchy_depth: Optional[int] = None,
) -> exp.Expression:
    """
    Parse and validate Snowflake SQL using sqlglot AST.

    Rules:
    - Must parse into exactly 1 statement.
    - Root AST node must be Select or Union (WITH CTEs permitted).
    - No forbidden DML/DDL/Command nodes anywhere in the AST.
    - Legitimate column names like UPDATE_TS, DELETE_FLAG, CREATE_DATE are allowed.
    - If max_hierarchy_depth is provided and query uses WITH RECURSIVE, enforce depth bound.
    """
    if not sql or not sql.strip():
        raise SQLValidationError("Validation SQL cannot be empty.")

    try:
        statements = sqlglot.parse(sql, read="snowflake")
    except sqlglot.errors.ParseError as exc:
        raise SQLValidationError(f"SQL syntax parse error: {exc}") from exc

    statements = [stmt for stmt in statements if stmt is not None]
    if len(statements) != 1:
        raise SQLValidationError(
            f"Expected exactly 1 SQL statement, but found {len(statements)}."
        )

    root = statements[0]
    if not isinstance(root, (exp.Select, exp.Union)):
        raise SQLValidationError(
            f"Only SELECT or UNION queries are permitted; got {type(root).__name__}."
        )

    for node in root.walk():
        if isinstance(node, FORBIDDEN_AST_NODES):
            raise SQLValidationError(
                f"Prohibited SQL operation detected in AST: {type(node).__name__}."
            )

    if max_hierarchy_depth is not None:
        _check_recursive_depth_guardrail(root, max_hierarchy_depth)

    return root


def wrap_with_target_dataset_cte(
    sql: str,
    target_fqn: str,
    temporal_cycle_column: Optional[str] = None,
    snapshot_ts_column: Optional[str] = None,
    planning_cycle_id: Optional[str] = None,
    snapshot_ts: Optional[str] = None,
    max_hierarchy_depth: Optional[int] = None,
) -> Tuple[str, Dict[str, Any]]:
    """
    Validate SQL and dynamically wrap `__TARGET_DATASET__` as a filtered CTE
    using parameterized bind variables for planning_cycle_id and snapshot_ts.
    """
    validate_readonly_sql(sql, max_hierarchy_depth=max_hierarchy_depth)
    safe_target = _validate_identifier(target_fqn, "target_fqn")

    where_clauses: List[str] = []
    bind_params: Dict[str, Any] = {}

    if temporal_cycle_column and planning_cycle_id is not None:
        safe_cycle_col = _validate_identifier(temporal_cycle_column, "temporal_cycle_column")
        where_clauses.append(f"{safe_cycle_col} = %(planning_cycle_id)s")
        bind_params["planning_cycle_id"] = planning_cycle_id

    if snapshot_ts_column and snapshot_ts is not None:
        safe_snap_col = _validate_identifier(snapshot_ts_column, "snapshot_ts_column")
        where_clauses.append(f"{safe_snap_col} = %(snapshot_ts)s")
        bind_params["snapshot_ts"] = snapshot_ts

    # Also support explicit :planning_cycle_id / :snapshot_ts binds inside rule SQL
    normalized_sql = sql.strip().rstrip(";")
    if ":planning_cycle_id" in normalized_sql:
        normalized_sql = normalized_sql.replace(":planning_cycle_id", "%(planning_cycle_id)s")
        bind_params.setdefault("planning_cycle_id", planning_cycle_id)
    if ":snapshot_ts" in normalized_sql:
        normalized_sql = normalized_sql.replace(":snapshot_ts", "%(snapshot_ts)s")
        bind_params.setdefault("snapshot_ts", snapshot_ts)

    where_sql = f" WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    target_cte_body = f"__TARGET_DATASET__ AS (\n    SELECT * FROM {safe_target}{where_sql}\n)"

    # Only inject __TARGET_DATASET__ CTE if referenced in SQL or temporal filters are active
    needs_cte = "__TARGET_DATASET__" in normalized_sql.upper() or bool(where_clauses)
    if not needs_cte:
        return normalized_sql, bind_params

    # If SQL already begins with WITH [RECURSIVE], prepend __TARGET_DATASET__ into existing WITH
    with_match = re.match(r"^\s*WITH\s+(RECURSIVE\s+)?", normalized_sql, flags=re.IGNORECASE)
    if with_match:
        recursive_kw = with_match.group(1) or ""
        rest = normalized_sql[with_match.end():]
        wrapped_sql = f"WITH {recursive_kw}{target_cte_body},\n{rest}"
    else:
        wrapped_sql = f"WITH {target_cte_body}\n{normalized_sql}"

    return wrapped_sql, bind_params
