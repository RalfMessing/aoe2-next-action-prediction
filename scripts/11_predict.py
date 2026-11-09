#!/usr/bin/env python3
"""Write the shared predictions file: transformer + trigram reference + plausibility, one row per test step.

One row = one target run (the next *different* action) of a session in the chosen split. Columns:
  keys      session_id, match_id, step (run index), first_seq / last_seq (join to decisions.parquet)
  context   start_time_s, minute, age, run_length, true_token, true_object, true_action
  model     top5_tokens, top5_probs, top1_token, top1_object, p_true, true_rank, entropy, is_top1, is_top5
  trigram   tri_top5_tokens, tri_top5_probs, tri_top1_token, tri_p_true, tri_level, tri_is_top1, tri_is_top5
  plausible true_plausible, top1_plausible, top1_reasons, top5_n_plausible

Plausibility asks: could the predicted action have been done at the moment the player actually acted
(start_time_s of the true run)? It uses aoe2_plausibility.Checker, which only counts evidence from
before that time. true_plausible is the same check for the real action (its false-alarm rate).

Usage: python scripts/11_predict.py [--checkpoint models/transformer_v1/best.pt] [--split test]
                                    [--out data/processed/predictions.parquet] [--no-plausibility]
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import aoe2_sequences as seqs  # noqa: E402
from aoe2_model import ModelConfig, NextActionTransformer  # noqa: E402
from aoe2_trigram import TrigramModel  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TOP_K = 5


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--checkpoint", type=Path, default=ROOT / "models/transformer_v1/best.pt")
    parser.add_argument("--split", choices=("train", "val", "test"), default="test")
    parser.add_argument("--processed", type=Path, default=seqs.DEFAULT_PROCESSED)
    parser.add_argument("--db", type=Path, default=seqs.DEFAULT_PROCESSED / "aoe2.db")
    parser.add_argument("--out", type=Path, default=None, help="default: data/processed/predictions.parquet")
    parser.add_argument("--no-plausibility", action="store_true", help="skip the plausibility join (no aoe2.db)")
    return parser.parse_args()


def load_model(path: Path, processed: Path) -> tuple[NextActionTransformer, dict]:
    checkpoint = torch.load(path, map_location="cpu", weights_only=False)
    if checkpoint["vocab_sha256"] != seqs.vocab_fingerprint(processed):
        raise ValueError("vocab.csv differs from the one the checkpoint was trained with; re-run the export or retrain")
    model = NextActionTransformer(ModelConfig(**checkpoint["model_config"]), pad_id=seqs.PAD)
    model.load_state_dict(checkpoint["model_state"])
    return model.eval(), checkpoint


@torch.no_grad()
def model_predictions(model: NextActionTransformer, windows: seqs.Windows, batch_size: int = 256) -> pd.DataFrame:
    """Top-k, probability and rank of the true token, entropy for every scored position."""
    rows = {k: [] for k in ("session_id", "step", "top5_tokens", "top5_probs", "p_true", "true_rank", "entropy")}
    for start in range(0, len(windows), batch_size):
        part = slice(start, start + batch_size)
        logits = model(*(torch.as_tensor(getattr(windows, n)[part]) for n in ("tokens", "minutes", "run_lengths")))
        targets = torch.as_tensor(windows.targets[part])
        scored = targets.ne(seqs.IGNORE_INDEX)
        probs = logits[scored].softmax(dim=-1)
        true = targets[scored]
        p_true = probs.gather(1, true[:, None]).squeeze(1)
        top_probs, top_tokens = probs.topk(TOP_K, dim=-1)
        session_ids = np.broadcast_to(windows.session_ids[part, None], scored.shape)[scored.numpy()]
        rows["session_id"].append(session_ids)
        rows["step"].append(windows.steps[part][scored.numpy()])
        rows["top5_tokens"] += top_tokens.numpy().astype(np.int16).tolist()
        rows["top5_probs"] += top_probs.numpy().astype(np.float32).tolist()
        rows["p_true"].append(p_true.numpy())
        rows["true_rank"].append((probs > p_true[:, None]).sum(dim=-1).numpy() + 1)
        rows["entropy"].append(-(probs * probs.clamp_min(1e-12).log()).sum(dim=-1).numpy())
    return pd.DataFrame({k: (np.concatenate(v) if k not in ("top5_tokens", "top5_probs") else v)
                         for k, v in rows.items()})


def session_actions(session: seqs.Session) -> np.ndarray:
    """The run tokens of a session (targets without the trailing IGNORE_INDEX)."""
    return session.targets[:-1]


def trigram_predictions(train_sessions: list[seqs.Session], sessions: list[seqs.Session],
                        n_actions: int) -> pd.DataFrame:
    model = TrigramModel(n_actions).fit([session_actions(s) for s in train_sessions])
    rows = []
    for session in sessions:
        for step, prediction in enumerate(model.predict_sequence(session_actions(session), TOP_K)):
            rows.append((session.session_id, step, prediction.top_k, prediction.probs, prediction.p_true,
                         prediction.level))
    return pd.DataFrame(rows, columns=["session_id", "step", "tri_top5_tokens", "tri_top5_probs",
                                       "tri_p_true", "tri_level"])


def plausibility(frame: pd.DataFrame, game_object_of_token: np.ndarray, db: Path) -> pd.DataFrame:
    from aoe2_plausibility import Checker  # needs the tables built by run_all.py

    checker = Checker(sqlite3.connect(db))
    ordered = frame.sort_values(["session_id", "step"])  # keeps the checker's one-session cache warm
    true_ok, top1_ok, top1_reasons, top5_ok = [], [], [], []
    for row in ordered.itertuples(index=False):
        session_id, time_s = int(row.session_id), int(row.start_time_s)
        true_ok.append(not checker.check(session_id, time_s, int(game_object_of_token[row.true_token])))
        reasons = [checker.check(session_id, time_s, int(game_object_of_token[t])) for t in row.top5_tokens]
        top1_ok.append(not reasons[0])
        top1_reasons.append("; ".join(f"{code}: {detail}" for code, detail in reasons[0]))
        top5_ok.append(sum(not r for r in reasons))
    checks = ordered[["session_id", "step"]].assign(true_plausible=true_ok, top1_plausible=top1_ok,
                                                    top1_reasons=top1_reasons, top5_n_plausible=top5_ok)
    return frame.merge(checks, on=["session_id", "step"], how="left", validate="one_to_one")


def main() -> None:
    args = parse_args()
    started = time.time()
    model, checkpoint = load_model(args.checkpoint, args.processed)
    runs, sessions = seqs.prepare(args.processed)
    vocab = seqs.load_vocab(args.processed)
    windows = seqs.make_windows(sessions[args.split], model.config.context,
                                checkpoint["train_config"]["stride"])

    frame = model_predictions(model, windows)
    frame = frame.merge(trigram_predictions(sessions["train"], sessions[args.split], len(vocab)),
                        on=["session_id", "step"], how="left", validate="one_to_one")
    context = runs[runs["split"] == args.split][["session_id", "step", "match_id", "first_seq", "last_seq",
                                                  "start_time_s", "minute", "age", "run_length", "token_id",
                                                  "object", "action"]]
    frame = context.rename(columns={"token_id": "true_token", "object": "true_object", "action": "true_action"}) \
        .merge(frame, on=["session_id", "step"], how="inner", validate="one_to_one")

    names = vocab.set_index("token_id")["name"]
    frame["top1_token"] = frame["top5_tokens"].str[0]
    frame["top1_object"] = frame["top1_token"].map(names)
    frame["is_top1"] = frame["top1_token"].eq(frame["true_token"])
    frame["is_top5"] = frame["true_rank"].le(TOP_K)
    frame["tri_top1_token"] = frame["tri_top5_tokens"].str[0]
    frame["tri_is_top1"] = frame["tri_top1_token"].eq(frame["true_token"])
    frame["tri_is_top5"] = [t in top for t, top in zip(frame["true_token"], frame["tri_top5_tokens"])]
    if not args.no_plausibility:
        frame = plausibility(frame, vocab.sort_values("token_id")["game_object_id"].to_numpy(), args.db)

    frame.insert(0, "split", args.split)
    frame["checkpoint"] = f"{args.checkpoint.parent.name}/{args.checkpoint.name}@step{checkpoint['step']}"
    frame = frame.sort_values(["session_id", "step"], ignore_index=True)
    out = args.out or args.processed / "predictions.parquet"
    frame.to_parquet(out, index=False, compression="zstd")
    print(f"{out}: {len(frame):,} rows ({frame.session_id.nunique()} sessions); "
          f"model top1 {frame.is_top1.mean():.3f} top5 {frame.is_top5.mean():.3f}; "
          f"trigram top1 {frame.tri_is_top1.mean():.3f} top5 {frame.tri_is_top5.mean():.3f}"
          + ("" if args.no_plausibility else
             f"; top1 plausible {frame.top1_plausible.mean():.3f}, true plausible {frame.true_plausible.mean():.3f}")
          + f" ({time.time() - started:.0f}s)")


if __name__ == "__main__":
    main()
