#!/usr/bin/env python3
"""Train the next-action transformer and save checkpoints.

Outputs in --out (default models/<run-name>/, not committed):
  config.json            model + training configuration, vocab fingerprint, data statistics
  metrics.csv            one row per evaluation (step, epoch, train/val loss, val top-1/top-5, lr)
  checkpoints/step_XXXXXXX.pt   at step 0 and at log-spaced steps (16, 32, 64, ...) for Phase II
  checkpoints/epoch_XXX.pt      after every epoch
  best.pt                lowest validation loss (used by 11_predict.py)

Training stops after --epochs or when the validation loss has not improved for --patience epochs.

Usage: python scripts/10_train.py [--run-name NAME] [--epochs N] [--max-steps N] [--threads N]
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

sys.path.insert(0, str(Path(__file__).resolve().parent))
import aoe2_sequences as seqs  # noqa: E402
from aoe2_model import ModelConfig, NextActionTransformer  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class TrainConfig:
    run_name: str = "transformer_v1"
    seed: int = 42
    epochs: int = 40
    max_steps: int = 0          # 0 = no limit (useful for a quick smoke test)
    batch_size: int = 64
    lr: float = 1e-3
    min_lr_ratio: float = 0.1
    weight_decay: float = 0.1
    warmup_steps: int = 200
    grad_clip: float = 1.0
    patience: int = 5
    stride: int = 64
    first_logged_step: int = 16


def parse_args() -> tuple[TrainConfig, ModelConfig, argparse.Namespace]:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    defaults = TrainConfig()
    for name, value in asdict(defaults).items():
        parser.add_argument(f"--{name.replace('_', '-')}", type=type(value), default=value)
    for name in ("d_model", "n_layers", "n_heads", "d_ff", "context"):
        parser.add_argument(f"--{name.replace('_', '-')}", type=int, default=ModelConfig.__dataclass_fields__[name].default)
    parser.add_argument("--dropout", type=float, default=ModelConfig.dropout)
    parser.add_argument("--processed", type=Path, default=seqs.DEFAULT_PROCESSED)
    parser.add_argument("--out", type=Path, default=None, help="default: models/<run-name>")
    parser.add_argument("--threads", type=int, default=0, help="torch CPU threads (0 = torch default)")
    args = parser.parse_args()
    train_config = TrainConfig(**{k: getattr(args, k) for k in asdict(defaults)})
    model_config = ModelConfig(n_actions=0, n_input_tokens=0, n_minutes=seqs.MAX_MINUTE + 1,
                               n_run_length_buckets=seqs.N_RUN_LENGTH_BUCKETS, context=args.context,
                               d_model=args.d_model, n_layers=args.n_layers, n_heads=args.n_heads,
                               d_ff=args.d_ff, dropout=args.dropout)
    return train_config, model_config, args


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def to_tensors(windows: seqs.Windows) -> dict[str, torch.Tensor]:
    return {name: torch.as_tensor(getattr(windows, name))
            for name in ("tokens", "minutes", "run_lengths", "targets")}


def learning_rate(step: int, total_steps: int, config: TrainConfig) -> float:
    """Linear warm-up, then cosine decay to min_lr_ratio * lr."""
    if step < config.warmup_steps:
        return config.lr * (step + 1) / config.warmup_steps
    progress = min(1.0, (step - config.warmup_steps) / max(1, total_steps - config.warmup_steps))
    cosine = 0.5 * (1 + math.cos(math.pi * progress))
    return config.lr * (config.min_lr_ratio + (1 - config.min_lr_ratio) * cosine)


def batch_loss(model, batch: dict[str, torch.Tensor]) -> torch.Tensor:
    logits = model(batch["tokens"], batch["minutes"], batch["run_lengths"])
    return F.cross_entropy(logits.flatten(0, 1), batch["targets"].flatten(), ignore_index=seqs.IGNORE_INDEX)


@torch.no_grad()
def evaluate(model, data: dict[str, torch.Tensor], device, batch_size: int = 256) -> dict[str, float]:
    """Mean loss and top-1/top-5 accuracy over all scored positions."""
    model.eval()
    loss_sum, top1, top5, count = 0.0, 0, 0, 0
    for start in range(0, len(data["tokens"]), batch_size):
        batch = {k: v[start:start + batch_size].to(device) for k, v in data.items()}
        logits = model(batch["tokens"], batch["minutes"], batch["run_lengths"])
        scored = batch["targets"].ne(seqs.IGNORE_INDEX)
        logits, targets = logits[scored], batch["targets"][scored]
        loss_sum += F.cross_entropy(logits, targets, reduction="sum").item()
        ranking = logits.topk(5, dim=-1).indices
        top1 += ranking[:, 0].eq(targets).sum().item()
        top5 += ranking.eq(targets[:, None]).any(dim=-1).sum().item()
        count += len(targets)
    model.train()
    return {"loss": loss_sum / count, "top1": top1 / count, "top5": top5 / count}


class CheckpointWriter:
    def __init__(self, out: Path, model_config: ModelConfig, train_config: TrainConfig, fingerprint: str):
        self.out = out
        (out / "checkpoints").mkdir(parents=True, exist_ok=True)
        self.meta = {"model_config": model_config.to_dict(), "train_config": asdict(train_config),
                     "vocab_sha256": fingerprint}
        self.next_logged_step = train_config.first_logged_step

    def save(self, path: Path, model, step: int, epoch: int, metrics: dict | None = None) -> None:
        torch.save({**self.meta, "step": step, "epoch": epoch, "metrics": metrics or {},
                    "model_state": model.state_dict()}, path)

    def maybe_save_log_step(self, model, step: int, epoch: int) -> None:
        """Log-spaced checkpoints (16, 32, 64, ...) capture the fast early phase of training."""
        if step == self.next_logged_step:
            self.save(self.out / f"checkpoints/step_{step:07d}.pt", model, step, epoch)
            self.next_logged_step *= 2


def main() -> None:
    train_config, model_config, args = parse_args()
    if args.threads:
        torch.set_num_threads(args.threads)
    set_seed(train_config.seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    out = args.out or ROOT / "models" / train_config.run_name

    started = time.time()
    runs, sessions = seqs.prepare(args.processed)
    vocab = seqs.load_vocab(args.processed)
    model_config.n_actions = len(vocab)
    model_config.n_input_tokens = len(vocab) + seqs.TOKEN_OFFSET
    train = to_tensors(seqs.make_windows(sessions["train"], model_config.context, train_config.stride))
    val = to_tensors(seqs.make_windows(sessions["val"], model_config.context, train_config.stride))
    stats = {name: {"sessions": len(s), "runs": int(sum(len(x) - 1 for x in s))} for name, s in sessions.items()}
    print(f"data: {stats}; train windows {len(train['tokens'])}, val windows {len(val['tokens'])} "
          f"({time.time() - started:.0f}s)")

    model = NextActionTransformer(model_config, pad_id=seqs.PAD).to(device)
    print(f"model: {model.n_parameters():,} parameters on {device}")
    decay = [p for n, p in model.named_parameters() if p.dim() >= 2 and "embedding" not in n]
    no_decay = [p for n, p in model.named_parameters() if not (p.dim() >= 2 and "embedding" not in n)]
    optimizer = torch.optim.AdamW([{"params": decay, "weight_decay": train_config.weight_decay},
                                   {"params": no_decay, "weight_decay": 0.0}], lr=train_config.lr)

    steps_per_epoch = math.ceil(len(train["tokens"]) / train_config.batch_size)
    total_steps = steps_per_epoch * train_config.epochs
    if train_config.max_steps:
        total_steps = min(total_steps, train_config.max_steps)

    writer = CheckpointWriter(out, model_config, train_config, seqs.vocab_fingerprint(args.processed))
    (out / "config.json").write_text(json.dumps({**writer.meta, "data": stats,
                                                 "n_parameters": model.n_parameters()}, indent=2),
                                     encoding="utf-8")
    writer.save(out / "checkpoints/step_0000000.pt", model, step=0, epoch=0)

    metrics_file = open(out / "metrics.csv", "w", newline="", encoding="utf-8")
    metrics_writer = csv.writer(metrics_file)
    metrics_writer.writerow(["step", "epoch", "train_loss", "val_loss", "val_top1", "val_top5", "lr", "seconds"])
    generator = torch.Generator().manual_seed(train_config.seed)
    best_loss, epochs_without_improvement, step = float("inf"), 0, 0
    try:
        for epoch in range(1, train_config.epochs + 1):
            order = torch.randperm(len(train["tokens"]), generator=generator)
            running, batches = 0.0, 0
            for start in range(0, len(order), train_config.batch_size):
                if train_config.max_steps and step >= train_config.max_steps:
                    break
                index = order[start:start + train_config.batch_size]
                batch = {k: v[index].to(device) for k, v in train.items()}
                for group in optimizer.param_groups:
                    group["lr"] = learning_rate(step, total_steps, train_config)
                loss = batch_loss(model, batch)
                optimizer.zero_grad(set_to_none=True)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), train_config.grad_clip)
                optimizer.step()
                step += 1
                running += loss.item()
                batches += 1
                writer.maybe_save_log_step(model, step, epoch)

            result = evaluate(model, val, device)
            lr = optimizer.param_groups[0]["lr"]
            metrics_writer.writerow([step, epoch, f"{running / max(1, batches):.4f}", f"{result['loss']:.4f}",
                                     f"{result['top1']:.4f}", f"{result['top5']:.4f}", f"{lr:.2e}",
                                     f"{time.time() - started:.0f}"])
            metrics_file.flush()
            writer.save(out / f"checkpoints/epoch_{epoch:03d}.pt", model, step, epoch, result)
            improved = result["loss"] < best_loss
            if improved:
                best_loss, epochs_without_improvement = result["loss"], 0
                writer.save(out / "best.pt", model, step, epoch, result)
            else:
                epochs_without_improvement += 1
            print(f"epoch {epoch:3d}  step {step:6d}  train {running / max(1, batches):.3f}  "
                  f"val {result['loss']:.3f}  top1 {result['top1']:.3f}  top5 {result['top5']:.3f}"
                  f"{'  *' if improved else ''}  ({time.time() - started:.0f}s)", flush=True)
            if epochs_without_improvement >= train_config.patience:
                print(f"early stop: no improvement for {train_config.patience} epochs")
                break
            if train_config.max_steps and step >= train_config.max_steps:
                break
    finally:
        metrics_file.close()
    print(f"best val loss {best_loss:.4f}; outputs in {out}")


if __name__ == "__main__":
    main()
