# Limitation: the subgraph score and the embedding are fragile

Still open. Two facts explain most odd alerts, and neither is fixed. Label source for the counts: operator assertion of attack times.

## 1. The subgraph score counts flagged nodes, not severity

`src/pipeline/alg1.py` (`construct`):

$$ \mathrm{sg\_score} = \sum_{n \in \text{subgraph} \cap \text{flagged}} \mathrm{prob}(n), \qquad
   \mathrm{prob}(n) = \mathrm{clamp}\left(\frac{\mathrm{score}(n) - t_{\min}}{t_{\max}-t_{\min}}, 0, 1\right) $$

Each flagged node adds at most 1.0, so the score is close to the number of flagged nodes in one connected cluster. [AUTHOR CODE] defines
this sum; the consequence is [OURS] / [INFERENCE].

Two examples:
- 46 benign nodes from a Windows servicing run, each with prob 0.74: 46 × 0.74 ≈ 34, Moderate, red.
- 6 real attack nodes at prob 1.0: 6 × 1.0 = 6, Minor, green.

So a big benign cluster outranks a small attack. A red window means "a large unusual cluster", not "a severe attack".

## 2. Detection can rest on one node, and its score depends on the whole window

A node's score comes from a multi-hop `RGCNConv` embedding (D5), not from its own 15 features. The same C2 socket, with almost the same
features, scored +1.165 (flagged) in one run and −1.50 (normal) in another on the same model, because its neighbours drifted. No feature
says "this peer was never seen before": NET features are only Send/Recv counts and idle times.

## What helps

- A longer baseline removes recurring benign clusters (`retrain-2day-fp-lesson.md`).
- Our own tooling is filtered out: `etw2nodlink.own_tooling_pids` drops the `start-live.ps1` watchdog in training and live.
- exp08 uses 2 RGCN layers, a shallower neighbourhood. It was changed together with the baseline and the filter, so its own effect is not isolated.
- Step gradually: 1 layer only if 2 helps but not enough.

**Durable fix, not built:** a peer-novelty NET feature. It would be a new [OURS] deviation and needs a retrain of every model.

See DEVIATIONS D5, D8.
