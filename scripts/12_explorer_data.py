#!/usr/bin/env python3
"""Build the prefix tree for the build-order explorer (explorer/data.js).

A node is a prefix of collapsed actions from the start of a session (the root is the empty prefix).
For every session that passes through a node and has a next action, the node aggregates:
  actual   how often each next action was really chosen
  model    the transformer's mean probability per action over these sessions (full distribution,
           not only the top-5, so the averages are exact) and its top-1 hit rate
  trigram  the trigram reference's top-5 for the last two actions of the prefix and its hit rate
Only nodes reached by at least --min-sessions sessions and up to --max-depth actions are kept.

The model sees more than the prefix (minute and run lengths), so sessions with the same prefix can get
different predictions; the node shows their mean. Sessions come from held-out splits only (default
val + test), so the model never saw them in training.

Usage: python scripts/12_explorer_data.py [--checkpoint models/transformer_v1/best.pt]
                                          [--splits val test] [--min-sessions 5] [--max-depth 40]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import aoe2_sequences as seqs  # noqa: E402
from aoe2_trigram import BOS_CONTEXT, TrigramModel  # noqa: E402
from importlib import import_module  # noqa: E402

predict = import_module("11_predict")  # reuse load_model and session_actions
ROOT = Path(__file__).resolve().parent.parent
TOP_ACTUAL, TOP_MODEL, TOP_TRIGRAM = 8, 8, 5


@dataclass
class Node:
    prefix: tuple[int, ...]
    sessions: int = 0
    actual: Counter = field(default_factory=Counter)
    prob_sum: np.ndarray | None = None
    model_hits: int = 0
    p_true_sum: float = 0.0
    minutes: list[int] = field(default_factory=list)


@torch.no_grad()
def full_distributions(model, sessions: list[seqs.Session], stride: int) -> dict[tuple[int, int], np.ndarray]:
    """Softmax over all actions for every (session_id, step)."""
    windows = seqs.make_windows(sessions, model.config.context, stride)
    result = {}
    for start in range(0, len(windows), 256):
        part = slice(start, start + 256)
        logits = model(*(torch.as_tensor(getattr(windows, n)[part]) for n in ("tokens", "minutes", "run_lengths")))
        scored = windows.steps[part] >= 0
        probs = logits.softmax(dim=-1).numpy()[scored]
        session_ids = np.broadcast_to(windows.session_ids[part, None], scored.shape)[scored]
        for session_id, step, p in zip(session_ids, windows.steps[part][scored], probs):
            result[(int(session_id), int(step))] = p
    return result


def build_tree(sessions: list[seqs.Session], probs: dict, max_depth: int) -> dict[tuple[int, ...], Node]:
    nodes: dict[tuple[int, ...], Node] = {}
    for session in sessions:
        actions = predict.session_actions(session)
        minutes = session.minutes[1:]
        for step in range(min(len(actions), max_depth + 1)):
            prefix = tuple(int(t) for t in actions[:step])
            node = nodes.setdefault(prefix, Node(prefix))
            p = probs[(session.session_id, step)]
            true = int(actions[step])
            node.sessions += 1
            node.actual[true] += 1
            node.prob_sum = p.copy() if node.prob_sum is None else node.prob_sum + p
            node.model_hits += int(p.argmax() == true)
            node.p_true_sum += float(p[true])
            node.minutes.append(int(minutes[step]))
    return nodes


def node_record(node: Node, node_id: dict, trigram: TrigramModel) -> dict:
    mean = node.prob_sum / node.sessions
    model_top = np.argsort(-mean)[:TOP_MODEL]
    history = [BOS_CONTEXT, BOS_CONTEXT] + list(node.prefix)
    tri = trigram.predict(history[-2], history[-1], true_token=-1, k=TOP_TRIGRAM)
    tri_hits = sum(c for t, c in node.actual.items() if t == tri.top_k[0])
    parent = node.prefix[:-1]
    return {
        "id": node_id[node.prefix],
        "parent": node_id.get(parent) if node.prefix else None,
        "token": node.prefix[-1] if node.prefix else None,
        "depth": len(node.prefix),
        "n": node.sessions,
        "minute": float(np.median(node.minutes)),
        "actual": [[int(t), c] for t, c in node.actual.most_common(TOP_ACTUAL)],
        "model": [[int(t), round(float(mean[t]), 4)] for t in model_top],
        "model_acc": round(node.model_hits / node.sessions, 4),
        "model_p_true": round(node.p_true_sum / node.sessions, 4),
        "tri": [[int(t), round(p, 4)] for t, p in zip(tri.top_k, tri.probs)],
        "tri_level": tri.level,
        "tri_acc": round(tri_hits / node.sessions, 4),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--checkpoint", type=Path, default=ROOT / "models/transformer_v1/best.pt")
    parser.add_argument("--processed", type=Path, default=seqs.DEFAULT_PROCESSED)
    parser.add_argument("--splits", nargs="+", default=["val", "test"])
    parser.add_argument("--min-sessions", type=int, default=5)
    parser.add_argument("--max-depth", type=int, default=40)
    parser.add_argument("--out", type=Path, default=ROOT / "explorer/data.js")
    args = parser.parse_args()

    model, checkpoint = predict.load_model(args.checkpoint, args.processed)
    _, sessions = seqs.prepare(args.processed)
    vocab = seqs.load_vocab(args.processed)
    trigram = TrigramModel(len(vocab)).fit([predict.session_actions(s) for s in sessions["train"]])
    stride = checkpoint["train_config"]["stride"]

    # Two views: held-out sessions only (honest model accuracy) and all sessions (deeper tree, but the
    # model and the trigram have seen the training sessions, so their accuracy there is in-sample).
    views = {"held_out": args.splits, "all": ["train", "val", "test"]}
    trees = {}
    for view, splits in views.items():
        chosen = [s for name in splits for s in sessions[name]]
        nodes = build_tree(chosen, full_distributions(model, chosen, stride), args.max_depth)
        trees[view] = {"splits": splits, "sessions": len(chosen),
                       "nodes": prune_and_export(nodes, args.min_sessions, trigram)}
        print(f"view {view}: {len(trees[view]['nodes'])} nodes from {len(chosen)} sessions, "
              f"max depth {max(r['depth'] for r in trees[view]['nodes'])}")

    data = {
        "meta": {"checkpoint": f"{args.checkpoint.parent.name}/{args.checkpoint.name}@step{checkpoint['step']}",
                 "min_sessions": args.min_sessions, "max_depth": args.max_depth},
        "vocab": [{"id": int(r.token_id), "name": r.name, "type": r.type, "action": r.action}
                  for r in vocab.itertuples()],
        "trees": trees,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("window.EXPLORER_DATA = " + json.dumps(data, separators=(",", ":")) + ";\n", encoding="utf-8")
    print(f"{args.out}: {args.out.stat().st_size / 1e6:.2f} MB")


def prune_and_export(nodes: dict[tuple[int, ...], Node], min_sessions: int, trigram: TrigramModel) -> list[dict]:
    """Keep nodes with enough sessions whose whole path is kept; ids ordered by depth, then frequency."""
    kept = sorted((n for n in nodes.values() if n.sessions >= min_sessions),
                  key=lambda n: (len(n.prefix), -n.sessions))
    kept_prefixes = {n.prefix for n in kept}
    kept = [n for n in kept if all(n.prefix[:i] in kept_prefixes for i in range(len(n.prefix)))]
    node_id = {n.prefix: i for i, n in enumerate(kept)}
    return [node_record(n, node_id, trigram) for n in kept]


if __name__ == "__main__":
    main()
