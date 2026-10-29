# Next-Action Prediction in Age of Empires II

Group project for **AI and Visualization** (JKU Linz, WS 2026) – Group *Vieso*

We train a small transformer to predict a player's next action in Age of Empires II and use visual analytics to understand **when** it fails (Phase I) and **why** (Phase II).

---

## Task

We predict a player's next action (build, research, train) from their previous actions, using an object-centric event log of 1,000 matches. The model outputs a probability distribution over all action types (top-k predictions).

Attack actions and match outcomes are not part of the dataset and are therefore excluded.

## Data

| Source | Description |
|---|---|
| [Zenodo 13365584](https://zenodo.org/records/13365584) | OCEL 2.0 event log of 1,000 AoE2 matches (~2.4M events). We use the SQLite version. |
| [aoe2techtree.net/data/data.json](https://aoe2techtree.net/data/data.json) | Tech tree, used to map actions to categories (building, unit, technology) and ages. |

The raw data is **not** committed to the repository (SQLite file: ~890 MB). See [Setup](#setup).

## Model

- **Main model:** small causal (decoder-only) transformer, ~0.5–1M parameters, trained from scratch in PyTorch.
  - Input: the last up to 128 actions plus game time
  - Output: probability distribution over ~269 action types
- **Baseline:** trigram (Markov) model with backoff, which only sees the last two actions. Where the transformer beats the baseline, it must be using longer context.

## Analysis plan

### Phase I – Model behaviour (black box)

We compare predictions with the actual next actions and analyse when the model fails, e.g.:

- after age-ups
- for rare actions (unique units)
- for specific openings

Main views:

- **Prefix tree of build orders**, coloured by error rate
- **Session timelines** (correct / wrong predictions over game time)
- **Calibration plots** (confidence vs. actual accuracy)
- Comparison with the trigram baseline

### Phase II – Model internals

To explain the patterns found in Phase I:

- Attention patterns
- Action embeddings (UMAP)
- Probing of hidden states for the current age
- Training checkpoints

## Repository structure

```
├── data/
│   ├── raw/            # SQLite file from Zenodo (not committed)
│   └── processed/      # actions.parquet (generated)
├── scripts/            # preprocessing, training
├── notebooks/          # Phase I / Phase II analyses
└── README.md
```

## Setup

1. Clone the repository and install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Download `age_of_empires_ocel2.sqlite` from [Zenodo](https://zenodo.org/records/13365584) and place it in `data/raw/`.
   For a quick look at the structure, the 10-match sample (`age_of_empires_ocel2_10_match_filter.zip`, 3.8 MB) is enough.
3. Run the preprocessing to create the flat action table:
   ```bash
   python scripts/preprocess.py
   ```
    **Warning**: Script does not exist yet!

Output: `data/processed/actions.parquet` (one row per action: session, game time, action).

> Everyone works with the same `actions.parquet`. Do not write your own parsing of the raw data.

## Credits

Dataset: Liss, L., Elbert, N., Flath, C. M., & van der Aalst, W. M. P. (2024). *Object-Centric Event Log for Age of Empires Game Interactions*. Zenodo. https://doi.org/10.5281/zenodo.13365584 (CC BY 4.0)