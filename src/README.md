# src/ — shared code

This folder is mounted into every VM at `~/NT2102` (that is, `/home/vagrant/NT2102`) as a **read/write synced folder**. Anything you put here is instantly visible inside all 6 VMs, and edits made in either place are two-way synced.

Suggested layout:

```
src/
├── notebooks/       # Jupyter notebooks for the RF/XGBoost baseline etc.
├── models/          # Serialized models (.pkl, .joblib) — small ones only
├── datasets/        # Small dataset samples ONLY; the big CIC CSVs go elsewhere
└── scripts/         # Shared training / inference / evaluation scripts
```

Commit code here; do **not** commit large datasets or captured PCAPs — those belong on the per-VM `/data` disks (under `./.vm-data/` on the host, gitignored).

## Getting to it from a VM

```bash
vagrant ssh sensor
cd ~/NT2102        # this is your src/
```
