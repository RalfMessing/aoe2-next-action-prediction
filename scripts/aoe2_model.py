"""Small causal (decoder-only) transformer for next-action prediction.

Input per position: action token + run start minute + run-length bucket + position in the window.
Output per position: logits over the action vocabulary for the *next different* action.

The attention blocks are written out instead of using nn.TransformerEncoder so that attention
weights and per-layer hidden states can be returned for Phase II (attention maps, probing).
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import torch
import torch.nn.functional as F
from torch import nn


@dataclass
class ModelConfig:
    n_actions: int                 # output classes (vocab.csv rows)
    n_input_tokens: int            # n_actions + special tokens (PAD, BOS)
    n_minutes: int                 # MAX_MINUTE + 1
    n_run_length_buckets: int
    context: int = 128
    d_model: int = 128
    n_layers: int = 4
    n_heads: int = 4
    d_ff: int = 512
    dropout: float = 0.1

    def to_dict(self) -> dict:
        return asdict(self)


class CausalSelfAttention(nn.Module):
    def __init__(self, config: ModelConfig):
        super().__init__()
        if config.d_model % config.n_heads:
            raise ValueError("d_model must be divisible by n_heads")
        self.n_heads = config.n_heads
        self.qkv = nn.Linear(config.d_model, 3 * config.d_model)
        self.out = nn.Linear(config.d_model, config.d_model)
        self.dropout = config.dropout

    def forward(self, x: torch.Tensor, key_padding: torch.Tensor, return_attention: bool):
        batch, length, d_model = x.shape
        q, k, v = self.qkv(x).view(batch, length, 3, self.n_heads, d_model // self.n_heads).unbind(dim=2)
        q, k, v = (t.transpose(1, 2) for t in (q, k, v))  # (batch, heads, length, head_dim)
        causal = torch.ones(length, length, dtype=torch.bool, device=x.device).tril()
        allowed = causal & ~key_padding[:, None, None, :]
        # A padded query row would have no allowed key; let it attend to itself to avoid NaNs.
        allowed = allowed | torch.eye(length, dtype=torch.bool, device=x.device)
        if return_attention:
            scores = (q @ k.transpose(-2, -1)) / math.sqrt(q.size(-1))
            weights = scores.masked_fill(~allowed, float("-inf")).softmax(dim=-1)
            y = F.dropout(weights, self.dropout, self.training) @ v
        else:
            weights = None
            y = F.scaled_dot_product_attention(q, k, v, attn_mask=allowed,
                                               dropout_p=self.dropout if self.training else 0.0)
        return self.out(y.transpose(1, 2).reshape(batch, length, d_model)), weights


class Block(nn.Module):
    """Pre-norm transformer block."""

    def __init__(self, config: ModelConfig):
        super().__init__()
        self.norm1 = nn.LayerNorm(config.d_model)
        self.attention = CausalSelfAttention(config)
        self.norm2 = nn.LayerNorm(config.d_model)
        self.mlp = nn.Sequential(nn.Linear(config.d_model, config.d_ff), nn.GELU(),
                                 nn.Linear(config.d_ff, config.d_model))
        self.dropout = nn.Dropout(config.dropout)

    def forward(self, x, key_padding, return_attention):
        attended, weights = self.attention(self.norm1(x), key_padding, return_attention)
        x = x + self.dropout(attended)
        x = x + self.dropout(self.mlp(self.norm2(x)))
        return x, weights


class NextActionTransformer(nn.Module):
    def __init__(self, config: ModelConfig, pad_id: int = 0):
        super().__init__()
        self.config = config
        self.pad_id = pad_id
        self.token_embedding = nn.Embedding(config.n_input_tokens, config.d_model, padding_idx=pad_id)
        self.minute_embedding = nn.Embedding(config.n_minutes, config.d_model)
        self.run_length_embedding = nn.Embedding(config.n_run_length_buckets, config.d_model)
        self.position_embedding = nn.Embedding(config.context, config.d_model)
        self.dropout = nn.Dropout(config.dropout)
        self.blocks = nn.ModuleList(Block(config) for _ in range(config.n_layers))
        self.final_norm = nn.LayerNorm(config.d_model)
        self.head = nn.Linear(config.d_model, config.n_actions)
        self.apply(self._init_weights)

    @staticmethod
    def _init_weights(module: nn.Module) -> None:
        if isinstance(module, (nn.Linear, nn.Embedding)):
            nn.init.normal_(module.weight, mean=0.0, std=0.02)
        if isinstance(module, nn.Linear) and module.bias is not None:
            nn.init.zeros_(module.bias)

    def forward(self, tokens, minutes, run_lengths, return_internals: bool = False):
        """Logits (batch, length, n_actions). With return_internals also a dict with
        'hidden' (list of n_layers + 1 tensors, embedding output first) and 'attention'
        (list of n_layers tensors of shape (batch, heads, length, length))."""
        length = tokens.size(1)
        if length > self.config.context:
            raise ValueError(f"sequence length {length} exceeds context {self.config.context}")
        positions = torch.arange(length, device=tokens.device)
        x = (self.token_embedding(tokens) + self.minute_embedding(minutes)
             + self.run_length_embedding(run_lengths) + self.position_embedding(positions))
        x = self.dropout(x)
        key_padding = tokens.eq(self.pad_id)
        hidden, attention = [x], []
        for block in self.blocks:
            x, weights = block(x, key_padding, return_internals)
            hidden.append(x)
            attention.append(weights)
        logits = self.head(self.final_norm(x))
        if return_internals:
            return logits, {"hidden": hidden, "attention": attention}
        return logits

    def n_parameters(self) -> int:
        return sum(p.numel() for p in self.parameters())
