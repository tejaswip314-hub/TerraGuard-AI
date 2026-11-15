#!/usr/bin/env python
"""Shared evaluation machinery for RF v3 experimental phase.

Design (fixed BEFORE any model is trained):
- Outer folds: 5 spatially contiguous x-bands covering the training AOI.
  Landslide pixels (24 unique polygons) are split by sorted x into 5 groups
  of ~4-5 positives; band boundaries are placed at midpoints between the
  landslide groups, so every fold contains positives and space is separated.
- Inner threshold tuning: strictly inside each outer fold, the outer-training
  subset is cross-validated (stratified 5-fold), OOF probabilities are
  collected, and the F1-maximizing threshold on 0.01..0.99 (step 0.01) is kept.
  The held-out test set is NEVER used for tuning or selection.
- Metrics per fold: ROC-AUC, PR-AUC, precision/recall/F1 at the inner-tuned
  threshold, confusion matrix with labels=[0,1].
- Aggregate: mean +/- std across folds.
"""
import json
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import StratifiedKFold
from sklearn.metrics import (confusion_matrix, precision_recall_fscore_support,
                             roc_auc_score, average_precision_score, f1_score)

DATASET = r"D:\TerraGuardAI\data\processed\pixel_rf_dataset_elevation_terrain_complete.csv"
DATASET_V3 = r"D:\TerraGuardAI\data\processed\pixel_rf_dataset_v3.csv"
BASE_FEATURES = ['elevation', 'slope', 'aspect_sin', 'aspect_cos', 'hillshade']
V3_EXTRA_FEATURES = ['plan_curvature', 'profile_curvature', 'mean_curvature']


def load_dataset(path=DATASET):
    df = pd.read_csv(path)
    train = df[df['train'] == 1].reset_index(drop=True)
    test = df[df['train'] == 0].reset_index(drop=True)
    return train, test


def spatial_fold_assignment(train_df):
    """Assign each training sample to one of 5 spatially contiguous x-bands.

    Returns bands (list of dicts with fold id, bounds, indices) and y for training.
    """
    x = train_df['x'].values
    y = train_df['class'].values
    ls_x = np.sort(x[y == 1])
    n_ls = len(ls_x)
    # split sorted landslide x into 5 near-equal contiguous chunks
    chunks = np.array_split(ls_x, 5)
    band_edges = [x.min()]
    for i in range(1, 5):
        left = chunks[i - 1].max()
        right = chunks[i].min()
        band_edges.append((left + right) / 2.0)
    band_edges.append(x.max() + 1e-6)

    bands = []
    for b in range(5):
        lo, hi = band_edges[b], band_edges[b + 1]
        idx = np.where((x >= lo) & (x < hi))[0]
        bands.append({'fold': b, 'lo': float(lo), 'hi': float(hi), 'indices': idx.tolist(),
                      'n_pos': int(y[idx].sum()), 'n_total': int(len(idx))})
    return bands, band_edges


def tune_threshold_oof(model_factory, Xtr, ytr, inner_seed=42, n_splits=5):
    """Inner out-of-fold threshold selection, strictly inside the outer-training subset."""
    skf = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=inner_seed)
    oof = np.zeros(len(ytr), dtype=np.float64)
    for tr_idx, va_idx in skf.split(Xtr, ytr):
        m = model_factory()
        m.fit(Xtr[tr_idx], ytr[tr_idx])
        oof[va_idx] = m.predict_proba(Xtr[va_idx])[:, 1]
    best_t, best_f1 = 0.5, -1.0
    grid = np.arange(0.01, 0.995, 0.01)
    for t in grid:
        f1 = f1_score(ytr, (oof >= t).astype(int), zero_division=0)
        if f1 > best_f1:
            best_f1, best_t = f1, float(t)
    return {'threshold': float(best_t), 'oof_f1_max': float(best_f1),
            'oof_probabilities': oof}


def outer_fold_eval(train_df, features, model_factory, band, seed=42):
    """One outer spatial fold: tune inside training part, evaluate on held-out band."""
    X = train_df[features].values.astype(np.float64)
    y = train_df['class'].values.astype(int)
    va_idx = np.array(band['indices'], dtype=int)
    tr_mask = np.ones(len(y), dtype=bool)
    tr_mask[va_idx] = False
    Xtr, ytr = X[tr_mask], y[tr_mask]
    Xva, yva = X[~tr_mask], y[~tr_mask]

    tune = tune_threshold_oof(model_factory, Xtr, ytr, inner_seed=seed)
    best_t = tune['threshold']

    model = model_factory()
    model.fit(Xtr, ytr)
    proba = model.predict_proba(Xva)[:, 1]
    yhat = (proba >= best_t).astype(int)

    n_pos = int(yva.sum())
    try:
        auc = float(roc_auc_score(yva, proba)) if n_pos > 0 else None
    except Exception:
        auc = None
    try:
        prc = float(average_precision_score(yva, proba)) if n_pos > 0 else None
    except Exception:
        prc = None

    cm = confusion_matrix(yva, yhat, labels=[0, 1]).ravel()
    tn, fp, fn, tp = [int(v) for v in cm]
    pr, rc, f1, _ = precision_recall_fscore_support(yva, yhat, average='binary', zero_division=0)
    return {'fold': band['fold'],
            'band': [round(band['lo'], 2), round(band['hi'], 2)],
            'n_train': int(len(ytr)), 'n_val': int(len(yva)), 'n_landslide_val': n_pos,
            'inner_tuned_threshold': best_t,
            'oof_f1_at_tuned_threshold': float(tune['oof_f1_max']),
            'roc_auc': auc, 'pr_auc': prc,
            'precision': float(pr), 'recall': float(rc), 'f1': float(f1),
            'confusion_matrix': {'TN': tn, 'FP': fp, 'FN': fn, 'TP': tp}}


def spatial_cv(train_df, features, model_factory, seed=42):
    bands, edges = spatial_fold_assignment(train_df)
    folds = [outer_fold_eval(train_df, features, model_factory, b, seed=seed) for b in bands]

    def agg(key):
        vals = [f[key] for f in folds if f[key] is not None]
        if not vals:
            return {'mean': None, 'std': None, 'min': None, 'max': None}
        return {'mean': float(np.mean(vals)), 'std': float(np.std(vals)),
                'min': float(np.min(vals)), 'max': float(np.max(vals))}

    return {'bands': bands, 'edge_values': edges, 'folds': folds,
            'aggregate': {'roc_auc': agg('roc_auc'), 'pr_auc': agg('pr_auc'),
                           'precision': agg('precision'), 'recall': agg('recall'),
                           'f1': agg('f1'), 'inner_tuned_threshold': agg('inner_tuned_threshold')}}


def pooled_oof_threshold(train_df, features, model_factory, bands, seed=42):
    """Pooled out-of-fold probabilities across the 5 outer bands (training data only)."""
    X = train_df[features].values.astype(np.float64)
    y = train_df['class'].values.astype(int)
    proba = np.full(len(y), np.nan, dtype=np.float64)
    for b in bands:
        va = np.array(b['indices'], dtype=int)
        tr = np.ones(len(y), dtype=bool)
        tr[va] = False
        m = model_factory()
        m.fit(X[tr], y[tr])
        proba[va] = m.predict_proba(X[va])[:, 1]
    best_t, best_f1 = 0.5, -1.0
    for t in np.arange(0.01, 0.995, 0.01):
        f1 = f1_score(y, (proba >= t).astype(int), zero_division=0)
        if f1 > best_f1:
            best_f1, best_t = f1, float(t)
    return {'threshold': float(best_t), 'oof_f1': float(best_f1),
            'oof_probabilities': proba}
