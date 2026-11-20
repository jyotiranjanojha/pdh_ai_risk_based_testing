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


def sanitize_sql_identifier(identifier: str, label: str = "identifier") -> str:
    """Validate and return a safe 1-to-3 part Snowflake SQL identifier."""
    return _validate_identifier(identifier, label)


def extract_sql_lineage_summary(sql_or_diff: str) -> Dict[str, Any]:
    """
    Parse transformation SQL or a PR SQL diff with sqlglot to extract
    referenced tables, joins, filters, aggregations, and CASE WHEN branches.
    """
    if not sql_or_diff or not sql_or_diff.strip():
        return {"tables": [], "joins": [], "filters": [], "aggregations": [], "case_branches": 0}

    # Strip git unified-diff markers (+ / - / @@) if a PR diff was supplied
    cleaned_lines: List[str] = []
    for line in sql_or_diff.splitlines():
        stripped = line.lstrip()
        if stripped.startswith(("+++", "---", "@@", "diff --git", "index ")):
            continue
        if stripped.startswith("+") or stripped.startswith("-"):
            cleaned_lines.append(stripped[1:])
        else:
            cleaned_lines.append(line)
    cleaned_sql = "\n".join(cleaned_lines).strip()

    tables: set[str] = set()
    joins: List[str] = []
    filters: List[str] = []
    aggregations: set[str] = set()
    case_branches = 0

    try:
        parsed_stmts = sqlglot.parse(cleaned_sql, read="snowflake")
    except Exception:
        return {
            "tables": [],
            "joins": [],
            "filters": [],
            "aggregations": [],
            "case_branches": 0,
            "raw_snippet": cleaned_sql[:1200],
        }

    for stmt in parsed_stmts:
        if stmt is None:
            continue
        for tbl in stmt.find_all(exp.Table):
            if tbl.name:
                fq = ".".join(p for p in (tbl.catalog, tbl.db, tbl.name) if p)
                tables.add(fq)
        for j in stmt.find_all(exp.Join):
            joins.append(j.sql(dialect="snowflake")[:200])
        for w in stmt.find_all(exp.Where):
            filters.append(w.sql(dialect="snowflake")[:200])
        for agg in stmt.find_all(exp.AggFunc):
            aggregations.add(agg.sql_name().upper() if hasattr(agg, "sql_name") else type(agg).__name__.upper())
        case_branches += len(list(stmt.find_all(exp.Case)))

    return {
        "tables": sorted(tables),
        "joins": joins[:10],
        "filters": filters[:10],
        "aggregations": sorted(aggregations),
        "case_branches": case_branches,
    }


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
    baseline_fqn: Optional[str] = None,
) -> Tuple[str, Dict[str, Any]]:
    """
    Validate SQL and dynamically wrap `__TARGET_DATASET__` (and optional `__BASELINE_DATASET__`)
    as filtered CTEs using parameterized bind variables for planning_cycle_id and snapshot_ts.
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
    cte_parts: List[str] = [
        f"__TARGET_DATASET__ AS (\n    SELECT * FROM {safe_target}{where_sql}\n)"
    ]

    upper_sql = normalized_sql.upper()
    needs_baseline = "__BASELINE_DATASET__" in upper_sql
    if needs_baseline:
        effective_baseline = baseline_fqn or safe_target
        safe_baseline = _validate_identifier(effective_baseline, "baseline_fqn")
        cte_parts.append(
            f"__BASELINE_DATASET__ AS (\n    SELECT * FROM {safe_baseline}{where_sql}\n)"
        )

    # Only inject CTEs if referenced in SQL or temporal filters are active
    needs_cte = "__TARGET_DATASET__" in upper_sql or needs_baseline or bool(where_clauses)
    if not needs_cte:
        return normalized_sql, bind_params

    cte_block = ",\n".join(cte_parts)

    # If SQL already begins with WITH [RECURSIVE], prepend CTEs into existing WITH
    with_match = re.match(r"^\s*WITH\s+(RECURSIVE\s+)?", normalized_sql, flags=re.IGNORECASE)
    if with_match:
        recursive_kw = with_match.group(1) or ""
        rest = normalized_sql[with_match.end():]
        wrapped_sql = f"WITH {recursive_kw}{cte_block},\n{rest}"
    else:
        wrapped_sql = f"WITH {cte_block}\n{normalized_sql}"

    return wrapped_sql, bind_params
