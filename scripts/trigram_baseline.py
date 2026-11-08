"""Trigram baseline (human-only pair) for next-action prediction in AoE2.

Setup: token_id as target, sorted by session_id + seq, flagged sessions removed,
consecutive repetitions collapsed, BOS padding. Join with predictions.parquet on
session_id + first_seq. Output: data/processed/trigram_test_predictions.parquet
"""
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

DATA_DIR = Path("data/processed")
OUTPUT_PATH = DATA_DIR / "trigram_test_predictions.parquet"
FLAG_COLUMNS = ["short_match", "no_commands", "outlier_commands"]
BOS = -1
K = 5

# 1. Load data and attach the split
vocab_df = pd.read_csv(DATA_DIR / "vocab.csv", encoding="utf-8")
split_df = pd.read_csv(DATA_DIR / "split.csv", usecols=["match_id", "split"])
decisions_df = pd.read_parquet(DATA_DIR / "decisions.parquet")
decisions_df = decisions_df.merge(split_df, on="match_id", how="left")
assert decisions_df["split"].notna().all(), "some matches are missing from split.csv"
decisions_df = decisions_df.sort_values(["session_id", "seq"]).reset_index(drop=True)
print(f"Loaded {len(decisions_df):,} decisions in {decisions_df['session_id'].nunique()} sessions")

# 2. Remove flagged sessions
flagged = decisions_df[FLAG_COLUMNS].any(axis=1)
n_flagged = decisions_df.loc[flagged, "session_id"].nunique()
decisions_df = decisions_df.loc[~flagged].reset_index(drop=True)
print(f"Removed {n_flagged} flagged sessions, {len(decisions_df):,} decisions left")

# 3. Collapse consecutive repetitions of the same action
same_session = decisions_df["session_id"].eq(decisions_df["session_id"].shift())
same_token = decisions_df["token_id"].eq(decisions_df["token_id"].shift())
run_id = (~(same_session & same_token)).cumsum()
runs = decisions_df.groupby(run_id, sort=False).agg(
    session_id=("session_id", "first"),
    split=("split", "first"),
    token_id=("token_id", "first"),
    first_seq=("seq", "first"),
    minute=("minute", "first"),
    age=("age", "first"),
    run_length=("seq", "size"),
).reset_index(drop=True)
runs["step"] = runs.groupby("session_id").cumcount()
print(f"Collapsed to {len(runs):,} steps ({len(decisions_df) / len(runs):.1f}x shorter)")


# 4. Trigram model with backoff
class TrigramBackoffModel:
    def __init__(self):
        self.trigram_counts = defaultdict(Counter)
        self.bigram_counts = defaultdict(Counter)
        self.unigram_counts = Counter()

    def fit(self, sequences):
        for seq in sequences:
            history = [BOS, BOS] + list(seq)
            for i, curr_act in enumerate(seq):
                self.trigram_counts[(history[i], history[i + 1])][curr_act] += 1
                self.bigram_counts[history[i + 1]][curr_act] += 1
                self.unigram_counts[curr_act] += 1

    def predict_top_k(self, prev_prev_act, prev_act, k=K):
        levels = []
        if self.trigram_counts.get((prev_prev_act, prev_act)):
            levels.append(("trigram", self.trigram_counts[(prev_prev_act, prev_act)]))
        if self.bigram_counts.get(prev_act):
            levels.append(("bigram", self.bigram_counts[prev_act]))
        levels.append(("unigram", self.unigram_counts))
        level, counts = levels[0]
        total = sum(counts.values())
        top_k = [act for act, _ in counts.most_common(k)]
        for _, lower in levels[1:]:
            if len(top_k) >= k:
                break
            top_k += [a for a, _ in lower.most_common() if a not in top_k][: k - len(top_k)]
        probs = [counts.get(a, 0) / total for a in top_k]
        return top_k, probs, level, counts, total


# 5. Train on the train split
train_runs = runs[runs["split"] == "train"]
test_runs = runs[runs["split"] == "test"]
train_sequences = train_runs.groupby("session_id")["token_id"].apply(list).tolist()
model = TrigramBackoffModel()
model.fit(train_sequences)
print(f"Trained on {len(train_sequences)} sessions")

# 6. Evaluate on the test split
results = []
for session_id, group in test_runs.groupby("session_id"):
    history = [BOS, BOS] + group["token_id"].tolist()
    for i, row in enumerate(group.itertuples(index=False)):
        top_k, probs, level, counts, total = model.predict_top_k(history[i], history[i + 1])
        results.append({
            "session_id": session_id, "first_seq": row.first_seq, "step": row.step,
            "minute": row.minute, "age": row.age, "run_length": row.run_length,
            "true_token": row.token_id, "top5_tokens": top_k, "top5_probs": probs,
            "top1_token": top_k[0], "confidence": probs[0],
            "p_true": counts.get(row.token_id, 0) / total, "backoff_level": level,
            "is_top1": row.token_id == top_k[0], "is_top5": row.token_id in top_k,
        })
results_df = pd.DataFrame(results)
names = vocab_df.set_index("token_id")["name"]
results_df["true_action"] = results_df["true_token"].map(names)
results_df["top1_action"] = results_df["top1_token"].map(names)

print("\n" + "=" * 45)
print(f"TEST RESULTS ({len(results_df):,} steps, {results_df['session_id'].nunique()} sessions)")
print(f"Top-1 accuracy : {results_df['is_top1'].mean() * 100:.1f}%")
print(f"Top-5 accuracy : {results_df['is_top5'].mean() * 100:.1f}%")
print("=" * 45)
print((results_df["backoff_level"].value_counts(normalize=True) * 100).round(1))
print((results_df.groupby("age")["is_top1"].mean() * 100).round(1))

# 7. Save predictions
results_df.to_parquet(OUTPUT_PATH, index=False)
print(f"\nSaved predictions to {OUTPUT_PATH}")
