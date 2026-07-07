"""Orchestrator for the post-session labeling pipeline (Stage 3)."""

from episodic_db.store.db import Database
from episodic_db.proxy.token_bridge import TokenBridge
from .contribution import mark_contributions
from .waste_signals import compute_waste_signals
from .outcome import classify_outcome


def run_labeling(db: Database, session_id: str):
    """Run the full labeling pipeline for a completed session."""
    conn = db.conn

    bridge = TokenBridge(db)
    bridge.reconcile_session(session_id)

    mark_contributions(conn, session_id)

    compute_waste_signals(conn, session_id)

    outcome = classify_outcome(conn, session_id)

    conn.execute(
        "UPDATE sessions SET success = ? WHERE session_id = ?",
        (1 if outcome == "converged" else 0, session_id),
    )
    conn.commit()
