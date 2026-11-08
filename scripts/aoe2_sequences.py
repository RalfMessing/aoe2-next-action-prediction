"""Turn the training export into model-ready sequences (shared by training, prediction and the trigram).

Pipeline (each step is a pure function so it can be tested and reused on its own):

    decisions = load_decisions(processed_dir)    # decisions.parquet joined with split.csv
    decisions = drop_flagged_sessions(decisions) # short_match, no_commands, outlier_commands
    runs      = collapse_repetitions(decisions)  # one row per run of the same token
    sessions  = build_sessions(runs)             # per-session arrays (tokens, minute, run-length bucket)
    windows   = make_windows(sessions, context)  # fixed-length windows, every target scored exactly once

Collapsing: consecutive decisions with the same token within a session form one *run*. The model
predicts the next *different* action. The run length is kept as an input feature (bucketed), so the
information "8 villagers in a row" is not lost. Every run keeps the `seq` of its first decision, so
predictions can be joined back to decisions.parquet and to the plausibility check.

Encoding of the model input:
    token ids  : PAD = 0, BOS = 1, action token t -> t + TOKEN_OFFSET
    targets    : action token id 0..n_actions-1 (no special tokens), IGNORE_INDEX where not scored
    minute     : run start minute, clipped to MAX_MINUTE (BOS/PAD use 0)
    run length : bucket index from RUN_LENGTH_EDGES (0 is reserved for BOS/PAD)
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROCESSED = ROOT / "data/processed"

PAD, BOS = 0, 1
TOKEN_OFFSET = 2
IGNORE_INDEX = -100
MAX_MINUTE = 90
# Upper bounds (inclusive) of the run-length buckets 1, 2, 3-4, 5-8, 9-16, 17+ (bucket ids 1..6).
RUN_LENGTH_EDGES = (1, 2, 4, 8, 16)
N_RUN_LENGTH_BUCKETS = len(RUN_LENGTH_EDGES) + 2  # + open-ended last bucket + reserved 0
FLAG_COLUMNS_DROPPED = ("short_match", "no_commands", "outlier_commands")


def load_decisions(processed_dir: Path = DEFAULT_PROCESSED) -> pd.DataFrame:
    """decisions.parquet with the split column, sorted by session and seq."""
    decisions = pd.read_parquet(processed_dir / "decisions.parquet")
    split = pd.read_csv(processed_dir / "split.csv", usecols=["match_id", "split"])
    decisions = decisions.merge(split, on="match_id", how="left", validate="many_to_one")
    if decisions["split"].isna().any():
        raise ValueError("decisions.parquet contains matches that are missing from split.csv")
    return decisions.sort_values(["session_id", "seq"], ignore_index=True)


def load_vocab(processed_dir: Path = DEFAULT_PROCESSED) -> pd.DataFrame:
    return pd.read_csv(processed_dir / "vocab.csv", encoding="utf-8")


def vocab_fingerprint(processed_dir: Path = DEFAULT_PROCESSED) -> str:
    """SHA-256 of vocab.csv, stored in checkpoints to detect a mismatching export."""
    return hashlib.sha256((processed_dir / "vocab.csv").read_bytes()).hexdigest()


def drop_flagged_sessions(decisions: pd.DataFrame, flags: tuple[str, ...] = FLAG_COLUMNS_DROPPED) -> pd.DataFrame:
    """Remove whole sessions marked as suspicious by the pipeline.

    has_unmapped_events is kept on purpose: the UNKNOWN events are already outside the vocabulary,
    the remaining decisions of those sessions are valid.
    """
    flagged = decisions[list(flags)].any(axis=1)
    return decisions.loc[~flagged].reset_index(drop=True)


def collapse_repetitions(decisions: pd.DataFrame) -> pd.DataFrame:
    """One row per run of identical consecutive tokens within a session (input sorted by session, seq)."""
    same_session = decisions["session_id"].eq(decisions["session_id"].shift())
    same_token = decisions["token_id"].eq(decisions["token_id"].shift())
    run_start = ~(same_session & same_token)
    run_id = run_start.cumsum()
    grouped = decisions.groupby(run_id, sort=False)
    runs = grouped.first()[["session_id", "match_id", "split", "token_id", "object", "action",
                            "seq", "time_s", "minute", "age"]]
    runs = runs.rename(columns={"seq": "first_seq", "time_s": "start_time_s"})
    runs["last_seq"] = grouped["seq"].last().to_numpy()
    runs["run_length"] = grouped.size().to_numpy()
    runs["step"] = runs.groupby("session_id").cumcount()  # index of the run within its session
    return runs.reset_index(drop=True)


def run_length_bucket(run_length: np.ndarray) -> np.ndarray:
    """Bucket ids 1..N_RUN_LENGTH_BUCKETS-1 (0 stays reserved for BOS/PAD)."""
    return np.searchsorted(np.asarray(RUN_LENGTH_EDGES), run_length, side="left") + 1


@dataclass(frozen=True)
class Session:
    """Model input of one session. Position 0 is BOS, position i >= 1 is run i-1."""
    session_id: int
    split: str
    tokens: np.ndarray       # (n_runs + 1,) input ids incl. BOS
    minutes: np.ndarray      # (n_runs + 1,)
    run_lengths: np.ndarray  # (n_runs + 1,) bucket ids
    targets: np.ndarray      # (n_runs + 1,) next action token per position, IGNORE_INDEX at the end
    steps: np.ndarray        # (n_runs + 1,) run index of the target per position, -1 at the end

    def __len__(self) -> int:
        return len(self.tokens)


def build_sessions(runs: pd.DataFrame) -> list[Session]:
    sessions = []
    for session_id, group in runs.groupby("session_id", sort=True):
        actions = group["token_id"].to_numpy(np.int64)
        n = len(actions)
        sessions.append(Session(
            session_id=int(session_id),
            split=str(group["split"].iat[0]),
            tokens=np.concatenate(([BOS], actions + TOKEN_OFFSET)),
            minutes=np.concatenate(([0], np.clip(group["minute"].to_numpy(np.int64), 0, MAX_MINUTE))),
            run_lengths=np.concatenate(([0], run_length_bucket(group["run_length"].to_numpy()))),
            targets=np.concatenate((actions, [IGNORE_INDEX])),
            steps=np.concatenate((np.arange(n), [-1])),
        ))
    return sessions


@dataclass(frozen=True)
class Windows:
    """Stacked fixed-length windows. `targets` is IGNORE_INDEX wherever a position is not scored."""
    tokens: np.ndarray       # (n_windows, context)
    minutes: np.ndarray
    run_lengths: np.ndarray
    targets: np.ndarray
    session_ids: np.ndarray  # (n_windows,)
    steps: np.ndarray        # (n_windows, context) run index of the target, -1 if not scored

    def __len__(self) -> int:
        return len(self.tokens)


def make_windows(sessions: list[Session], context: int = 128, stride: int | None = None) -> Windows:
    """Cut sessions into windows of `context` positions so that every target is scored exactly once.

    The first window of a session scores all positions. Each later window starts `stride` positions
    further and scores only its last `stride` positions, so those targets see at least
    `context - stride` positions of history.
    """
    stride = stride or context // 2
    if not 0 < stride <= context:
        raise ValueError("stride must be in (0, context]")
    columns = {k: [] for k in ("tokens", "minutes", "run_lengths", "targets", "steps")}
    session_ids = []
    for session in sessions:
        n = len(session)
        start = 0
        while True:
            end = min(start + context, n)
            score_from = 0 if start == 0 else context - stride
            for name in columns:
                fill = {"tokens": PAD, "targets": IGNORE_INDEX, "steps": -1}.get(name, 0)
                window = np.full(context, fill, dtype=np.int64)
                values = getattr(session, name)[start:end]
                window[:len(values)] = values
                if name in ("targets", "steps"):
                    window[:score_from] = fill
                columns[name].append(window)
            session_ids.append(session.session_id)
            if end == n:
                break
            start += stride
    stacked = {k: np.stack(v) for k, v in columns.items()}
    return Windows(session_ids=np.asarray(session_ids), **stacked)


def prepare(processed_dir: Path = DEFAULT_PROCESSED) -> tuple[pd.DataFrame, dict[str, list[Session]]]:
    """Convenience entry point: filtered, collapsed runs and the sessions per split."""
    runs = collapse_repetitions(drop_flagged_sessions(load_decisions(processed_dir)))
    sessions = build_sessions(runs)
    by_split = {name: [s for s in sessions if s.split == name] for name in ("train", "val", "test")}
    return runs, by_split
