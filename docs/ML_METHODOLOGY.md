# Machine Learning Methodology - Random Forest Landslide Susceptibility

## Overview

This document outlines the Random Forest methodology for the TerraGuard AI landslide susceptibility mapping project. The model uses verified geographical conditioning factors to estimate relative landslide susceptibility across the pilot study area.

**Random Forest is used for susceptibility assessment ONLY.** Rainfall monitoring remains independent via Open-Meteo and is NOT used as RF inputs in this design.

## Research Philosophy

### Core Principles
1. **Data-Driven Approach**: All models must be trained on verified, real datasets (NRSC inventory, verified landslide locations)
2. **Scientific Rigor**: No fabricated data, metrics, or results
3. **Reproducibility**: Complete documentation of all processes
4. **Uncertainty Quantification**: Explicit representation of prediction uncertainty
5. **Spatial Validation**: Prevention of data leakage through proper spatial validation

### Ethical Considerations
- Never treat unlabelled areas as negative examples without documented negative-sampling methodology
- Clearly distinguish susceptibility from rainfall-triggered hazard and real-time prediction
- Report model performance only after actual evaluation (do not claim accuracy before training/evaluation)
- Do not present susceptibility as a certainty of landslide occurrence
- Susceptibility indicates areas where landslides have occurred historically under similar conditions

### Model Scope
- **Purpose**: Landslide susceptibility assessment (relative risk ranking)
- **NOT used for**: Rainfall forecasting, real-time landslide prediction, or certainty of occurrence
- **RF inputs**: Verified geographical conditioning factors only (Open-Meteo rainfall is independent)
- **Output**: Susceptibility class (Low/Medium/High) with probability, not actual landslide occurrence

## Conditioning Factors

The Random Forest model uses the following verified geographical conditioning factors:

1. **Elevation** (meters) - from SRTM DEM (data/raw/dem/)
2. **Slope** (degrees) - from SRTM DEM (data/processed/terrain/slope_*.tif) - GENERATED
3. **Aspect** (degrees, 0-360) - from SRTM DEM (data/processed/terrain/aspect_*.tif) - GENERATED
4. **Curvature** - from SRTM DEM (if reliable method available)
5. **Geological characteristics** - if available from reliable sources (GSI, NRSC)
6. **Soil information** - if available from Soil Survey of India
7. **Land use/land cover** - if available from ESA WorldCover or NRSC data
8. **Distance to streams** - calculated from hydrography data, if reliable
9. **Distance to roads** - calculated from OSM/hydrography data, if reliable
10. **Distance to faults** - if reliable geological data available

**Currently Available Factors (verified):**
- Elevation (from SRTM DEM tiles: n30_e079_1arc_v3.tif, n31_e079_1arc_v3.tif)
- Slope (generated: data/processed/terrain/slope_n30_e079_1arc_v3.tif, max=89.8°)
- Aspect (generated: data/processed/terrain/aspect_n30_e079_1arc_v3.tif, max=359.8°)
- Hillshade (generated: data/processed/terrain/hillshade_n30_e079_1arc_v3.tif, max=251)

**Pending Factors (require data acquisition):**
- Geological characteristics (NRSC PDF requires extraction)
- Soil information (requires additional data source)
- Land use/land cover (requires additional data source)
- Distance to streams/roads/faults (requires hydrography/fault data acquisition)

## Training Data Requirements

### Verified Historical Landslide Inventory

**Current Status:**
- **NRSC Landslide Atlas PDF**: Downloaded (33.9MB, PDF-1.7 verified, requires extraction)
  - Path: `data/raw/landslide_inventory/Landslides_Atlas_of_India_Updated_25Aug2023.pdf`
  - Status: DOWNLOADED but NOT yet extracted to machine-readable labels/polygons
  - Source: NRSC/ISRO, URL: https://www.nrsc.gov.in/nrscnew/resources_atlas_landslide.php
  - Requires: Coordinate extraction to machine-readable format (shp, geojson, or csv)

**Required Before Training:**
1. Extract NRSC PDF to machine-readable landslide coordinates/polygons
2. Validate extracted coordinates (ground truth or cross-check with other sources)
3. Ensure coordinate reference system consistency (EPSG:4326 / WGS84)
4. Document extraction methodology and any limitations

### Negative Sampling Methodology

1. Identify stable areas using slope < 5° and elevation outside known landslide-prone bands
2. Select random points from stable areas with documented methodology
3. Verify each point is > 200m from any recorded landslide polygon
4. Ensure negative samples are geographically distributed across the pilot area
5. Document the negative-sampling methodology in model metadata
6. Minimum 100 negative samples recommended for robust training

### Minimum Dataset Requirements

| Requirement | Minimum | Recommended | Status |
|---|---|---|---|
| Verified landslide events | 50 events | 200+ events | Pending NRSC extraction |
| Negative samples | 100 points | 500+ points | Pending |
| Conditioning factors | 3+ factors | 8-10 factors | 3 available (elev, slope, aspect) |
| Spatial distribution | Across pilot area | Across full district | Pending district finalization |
| Attribute completeness | 100% for samples | 100% for samples | Partial (DEM factors ready) |

## Spatial Validation Strategy

### Recommended Approach

1. **Hold-out test set**: 20% of verified landslide points reserved for final evaluation (do NOT use in training)

2. **Spatial block cross-validation**: 
   - Divide pilot area into spatial blocks (e.g., 5 blocks based on quadrilateral grid or k-means clustering)
   - Train on k-1 blocks, test on remaining block
   - Repeat for all blocks (k-fold spatial)
   - Report average ROC-AUC, precision, recall, F1 across folds

3. **Spatial autocorrelation test**: 
   - Test training data for spatial autocorrelation (Moran's I, Geary's C)
   - If high autocorrelation: prefer spatial CV over random k-fold
   - If low autocorrelation: random CV may be acceptable

4. **Report metrics** (after evaluation):
   - ROC-AUC score
   - Precision at various thresholds
   - Recall at various thresholds
   - F1-score
   - Confusion matrix
   - Feature importances

### Validation Protocol

```python
# Example spatial validation workflow
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import cross_val_score
from sklearn.metrics import classification_report, confusion_matrix, roc_auc_score
import numpy as np

# Load verified training data
# X = conditioning factors (elevation, slope, aspect, curvature, etc.)
# y = landslide label (1 = landslide, 0 = stable area from negative sampling)

# Split into train/test with spatial blocking
# X_train, X_test, y_train, y_test = spatial_train_test_split(X, y, test_size=0.2)

# Train Random Forest
rf = RandomForestClassifier(n_estimators=100, random_state=42)
rf.fit(X_train, y_train)

# Spatial cross-validation
spatial_scores = cross_val_score(rf, X, y, cv=SpatialBlockCV(n_splits=5))
print(f"Spatial CV ROC-AUC: {spatial_scores.mean():.3f} (+/- {spatial_scores.std():.3f})")

# Final evaluation on hold-out test set
y_pred = rf.predict(X_test)
y_proba = rf.predict_proba(X_test)[:, 1]
print("Classification Report:")
print(classification_report(y_test, y_pred))
print(f"ROC-AUC: {roc_auc_score(y_test, y_proba):.3f}")
print("Confusion Matrix:")
print(confusion_matrix(y_test, y_pred))
```

**DO NOT report model performance metrics before completing the training/evaluation pipeline.**

## Feature Importance

After training, report feature importances:

```python
importances = rf.feature_importances_
feature_names = ['elevation', 'slope', 'aspect', 'curvature', 'geological', 'soil', 'landcover', 'dist_streams', 'dist_roads', 'dist_faults']
importance_df = pd.DataFrame({'feature': feature_names, 'importance': importances})
importance_df = importance_df.sort_values('importance', ascending=False)
print(importance_df)
```

## Uncertainty Statement

> "This is a Random Forest susceptibility assessment, not a real-time prediction. Susceptibility indicates areas where landslides have occurred historically under similar conditions. Actual landslide occurrence depends on countless factors including recent rainfall, specific terrain conditions, and triggering events. For emergency concerns, consult official meteorological and geological authorities. Model ROC-AUC: [value], validated on hold-out test set. Rainfall monitoring is independent via Open-Meteo and is NOT used as RF inputs."

## Deployment Notes

- Model file stored as `rf_susceptibility_model.pkl` or `rf_susceptibility_model.json`
- Feature importances documented in model metadata
- Do not use model for real-time prediction without validated test performance
- Clearly distinguish susceptibility from rainfall-triggered hazard and real-time prediction
- Model retraining recommended when new validated landslide inventory data becomes available
- Minimum 50 validated landslide events required before model training

## Quality Assurance and Validation

### Model Validation Protocol

1. **Spatial split**: Use spatial block cross-validation, not random k-fold
2. **Hold-out test set**: Reserve 20% of landslide points for final evaluation
3. **Report metrics**: ROC-AUC, precision, recall, F1-score, confusion matrix
4. **Do not claim before evaluation**: Model performance metrics reported only after complete training/evaluation pipeline
5. **Document all assumptions**: including sampling methodology, feature selection, validation strategy

### Feature Validation

- Each conditioning factor must have verified data source
- No fabricated or interpolated values presented as real
- All units and metrics clearly documented
- Spatial extent of each factor clearly mapped

## Integration with Independent Weather Component

### Rainfall Independence

- **Open-Meteo API**: Provides current weather and forecast precipitation
- **RF Model**: Uses ONLY verified geographical conditioning factors (elevation, slope, aspect, curvature, geology, soil, land use, distance to features)
- **NOT used as RF inputs**: Open-Meteo precipitation forecasts, rainfall accumulation, or intensity values
- **Display**: Both susceptibility map and rainfall dashboard displayed independently on website
- **User Guidance**: Clearly distinguish between susceptibility assessment and rainfall forecasts

### Coordination

- User selects location → both susceptibility prediction AND rainfall data shown
- Susceptibility based on static terrain factors (pre-computed from DEM)
- Rainfall based on real-time/future Open-Meteo forecast
- No data merging or feature cross-contamination
- Clear UI distinction: "Susceptibility (RF model)" vs "Rainfall (Open-Meteo forecast)"

## Task Tracker Integration

**Updated Phases:**

### Phase 1: Data Acquisition and Validation ✅ (Completed)
- NRSC PDF downloaded and verified (33.9MB, PDF-1.7)
- SRTM DEM files acquired (n30_e079, n31_e079)
- Terrain rasters generated (slope, aspect, hillshade)
- Open-Meteo API integration functional
- Coordinate validation (6/6 tests passing)

### Phase 2: Training Data Preparation 🔄 (In Progress)
- [ ] Extract NRSC PDF to machine-readable landslide coordinates/polygons
- [ ] Validate extracted landslide coordinates
- [ ] Finalize pilot district (Chamoli/Dehradun/Pithoragarh)
- [ ] Download SRTM GeoTIFF for pilot district extent (USGS EarthExplorer)
- [ ] Download IMERG subset for rainfall validation (NASA Earthdata)
- [ ] Compile all conditioning factors into training-ready format
- [ ] Document negative-sampling methodology

### Phase 3: Model Training and Evaluation 📋 (Pending)
- [ ] Prepare training dataset (X, y) with verified landslide and negative samples
- [ ] Implement spatial block cross-validation
- [ ] Train Random Forest model
- [ ] Evaluate on hold-out test set
- [ ] Report ROC-AUC, precision, recall, F1, confusion matrix
- [ ] Document feature importances
- [ ] Save model artifact (rf_susceptibility_model.pkl)

### Phase 4: API and Frontend Integration 📋 (Pending)
- [ ] Implement /rf/susceptibility API endpoint
- [ ] Add susceptibility indicator display to map sidebar
- [ ] Add rainfall/terrain indicator display to map sidebar
- [ ] Test RF model predictions with hold-out values
- [ ] Update UI/UX specifications for RF susceptibility display

### Phase 5: Documentation and Release 📋 (Pending)
- [ ] Update all documentation (README, ARCHITECTURE_SPEC, ML_METHODOLOGY, UI_UX_SPEC, TASK_TRACKER)
- [ ] Add model limitations and uncertainty statements
- [ ] Prepare data source attribution
- [ ] Final testing and quality assurance
- [ ] Release candidate

**Next immediate task**: Complete Phase 2 items (NRSC PDF extraction, pilot district finalization, USGS/NASA data download)ations
- Maintain transparency about model uncertainties
- **Prioritize public safety over model performance**
- **This project is now rule-based, not ML-based**

## Study Area Definition

### Geographic Scope
- **Primary Region**: Uttarakhand, India
- **Recommended Sub-regions**: 
  - Central Uttarakhand (high landslide density)
  - River valley areas
  - Foothill zones
- **Minimum Study Area**: 100 km² for meaningful statistical analysis

### Data Requirements by Study Area
- **Historical Landslide Data**: Minimum 50 recorded events
- **Terrain Data**: DEM with <30m resolution
- **Climate Data**: Minimum 20 years of precipitation records
- **Covariates**: Minimum 10 relevant feature variables

## Feature Engineering

### Core Feature Categories

#### 1. Terrain Features
- **Slope**: Gradient in degrees (0-90)
- **Aspect**: Direction of slope (0-360 degrees)
- **Curvature**: Profile and cross-sectional curvature
- **Elevation**: Height above sea level
- **Hillshade**: Illumination angle
- **Roughness**: Terrain texture metrics
- **TPI**: Topographic Position Index
- **TRD**: Terrain Ruggedness Index

#### 2. Climate Features
- **Annual Precipitation**: Total rainfall (mm)
- **Precipitation Seasonality**: CV of monthly rainfall
- **Rainy Days**: Number of days with precipitation
- **Drought Index**: Precipitation deficit metrics
- **Extreme Events**: 24-hour maximum rainfall
- **Seasonal Patterns**: Kharif/Rabi season differentiation

#### 3. Geological and Soil Features
- **Lithology**: Rock type and hardness
- **Soil Type**: USDA soil taxonomy
- **Soil Depth**: Thickness of soil layer
- **Slope Stability**: Initial shear strength
- **Bedrock Depth**: Depth to bedrock

#### 4. Human Impact Features
- **Land Cover**: Urban, agricultural, forest coverage
- **Road Density**: km per km²
- **Drainage Network**: Stream density and pattern
- **Settlement Density**: Population per km²
- **Land Use**: Built-up area percentage

#### 5. Temporal Features
- **Time since last event**: Recurrence interval
- **Seasonality**: Seasonal occurrence patterns
- **Climate trends**: Long-term precipitation trends

### Feature Extraction Methodology

#### Preprocessing Steps
1. **Coordinate System Conversion**
   - Transform to WGS84 (EPSG:4326)
   - Ensure consistent spatial reference

2. **Resolution Standardization**
   - Target resolution: 30m (1-arcsecond)
   - Resample to consistent grid
   - Apply appropriate interpolation

3. **Missing Data Handling**
   - Identify missing value patterns
   - Use spatial interpolation where appropriate
   - Document all imputation methods

4. **Outlier Detection**
   - Statistical outlier removal
   - Spatial outlier identification
   - Manual review of extreme values

#### Feature Quality Assessment
```python
class FeatureQualityAssessor:
    def __init__(self):
        self.quality_metrics = {}
    
    def assess_quality(self, feature_data: np.ndarray, feature_type: str) -> dict:
        quality_score = 0.0
        issues = []
        
        # Completeness check
        completeness = np.count_nonzero(~np.isnan(feature_data)) / feature_data.size
        if completeness < 0.95:
            issues.append(f"Low completeness: {completeness:.2%}")
        
        # Distribution check
        if feature_type in ['elevation', 'slope', 'rainfall']:
            # Check for realistic value ranges
            if np.any(feature_data > 10000):
                issues.append("Suspicious high values detected")
        
        # Spatial consistency
        # Check for abrupt transitions that might indicate data errors
        
        return {
            'quality_score': quality_score,
            'issues': issues,
            'completeness': completeness
        }
```

## Machine Learning Models - DEPRECATED

### Primary Models - DEPRECATED

#### 1. Logistic Regression - DEPRECATED
**Purpose**: Baseline model, interpretability  
**Strengths**: Interpretable coefficients, Fast training, Good baseline performance

**Parameters**:
- `C`: Inverse regularization strength (default: 1.0)
- `solver`: Algorithm for optimization (lbfgs, sag, etc.)
- `penalty`: Regularization type (l2, l1)

**Implementation** - DEPRECATED:
```python
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import cross_val_score
from sklearn.metrics import roc_auc_score, confusion_matrix
# NO LONGER USED IN THIS PROJECT
```

#### 2. Random Forest - DEPRECATED
**Purpose**: Non-linear patterns, feature importance  
**Strengths**: Handles non-linear relationships, Robust to outliers, Provides feature importance metrics, Less prone to overfitting

**Parameters**:
- `n_estimators`: Number of trees (default: 100)
- `max_depth`: Maximum tree depth (default: None)
- `min_samples_split`: Minimum samples for split (default: 2)

**Implementation** - DEPRECATED:
```python
from sklearn.ensemble import RandomForestClassifier
# NO LONGER USED IN THIS PROJECT
```

#### 3. Gradient Boosting - DEPRECATED
**Purpose**: Sequential learning, high accuracy  
**Strengths**: High predictive accuracy, Handles complex interactions, Can be optimized for specific metrics, Robust to class imbalance

**Parameters**:
- `n_estimators`: Number of boosting stages (default: 100)
- `learning_rate`: Shrinks contribution (default: 0.1)
- `max_depth`: Maximum depth of individual trees

**Implementation** - DEPRECATED:
```python
from sklearn.ensemble import GradientBoostingClassifier
# NO LONGER USED IN THIS PROJECT
```

### Model Training Protocol - DEPRECATED
```python
# NO LONGER USED IN THIS PROJECT
# This project has shifted to rule-based screening
```

## Rule-Based Methodology - NEW DIRECTION

### Core Principles
1. **Data-Driven Rules**: All risk indicators based on verified, real datasets
2. **Scientific Rigor**: No fabricated data, metrics, or results
3. **Transparency**: All rules and thresholds clearly documented and published
4. **Reproducibility**: Complete documentation of all processes
5. **Uncertainty Explicit**: Uncertainty explicitly stated, not fabricated as prediction probabilities
6. **Spatial Validation**: Prevention of data leakage through proper validation

### Rule-Based Risk Screening Indicators

#### Rainfall Thresholds (Scientifically Supported)
- **24-hour rainfall > 150mm**: Concerning rainfall amount for Himalayan region
- **24-hour rainfall > 200mm**: High concerning rainfall amount
- **48-hour cumulative rainfall > 250mm**: Significant for landslide triggering
- **These thresholds are general guidelines**; specific thresholds should be validated for Chamoli/Uttarakhand

#### Terrain Indicators
- **Slope > 30 degrees**: Steep terrain associated with landslide risk
- **Elevation range**: 1000-2000m elevation band (common landslide area in Himalayas)
- **Aspect**: Certain directions may correlate with weather patterns

#### Rainfall Forecast Indicators (Open-Meteo)
- **24-hour precipitation probability > 60%**: Elevated concern level
- **24-hour precipitation sum > 150mm**: Elevated concern level
- **These are indicative triggers only**, not prediction probabilities

#### Historical Landslide Indicators
- **Location within 500m of recorded historical landslide**: Elevated concern
- **Similar slope and elevation to historical events**: Elevated concern
- **These reference historical occurrence only**, not prediction

#### Rule-Based Screening Output
- **Rule Indicators**: List of active rule triggers
- **Concern Level**: "Low", "Moderate", "Elevated", "High" (based on active rules)
- **Data Quality**: "Verified", "Preliminary", "Insufficient Data"
- **Never output**: "Probability X%" or "Landslide will definitely occur"

### Uncertainty Statement
> "This is rule-based screening, not machine learning prediction. The indicators above are scientifically supported guidelines only. Actual landslide occurrence depends on countless factors. For emergency concerns, consult official meteorological and geological authorities."

## Uncertainty Quantification

### Prediction Uncertainty - DEPRECATED
```python
# NO LONGER USED IN THIS PROJECT
# Uncertainty is now expressed as data quality levels and rule trigger status,
# NOT as prediction probabilities.
```

### Quality Levels
- **Verified**: All data sources confirmed, rules scientifically supported
- **Preliminary**: Data available but not yet fully validated for Chamoli/Uttarakhand
- **Insufficient Data**: Not enough information to activate meaningful rules

## Model Persistence and Deployment - DEPRECATED

### Model Serialization - DEPRECATED
```python
# NO LONGER USED IN THIS PROJECT
# Rule-based rules are documented in text, not saved as serialized models.
```

## Quality Assurance and Validation - DEPRECATED

### Model Validation Protocol - DEPRECATED
```python
# NO LONGER USED IN THIS PROJECT
# Validation now focuses on: data quality, rule scientific support, threshold transparency
```

## Deployment and Monitoring - DEPRECATED

### Model Monitoring - DEPRECATED
```python
# NO LONGER USED IN THIS PROJECT
# Monitoring now focuses on: data freshness, rule trigger status, data quality levels
```

## Documentation and Reporting - DEPRECATED

### Model Documentation - DEPRECATED
```python
# NO LONGER USED IN THIS PROJECT
# Documentation now focuses on: rule scientific support, threshold transparency, data quality levels
```

## Best Practices and Guidelines - DEPRECATED (Updated)

### Development Best Practices - UPDATED
1. **Always validate data quality** before activating rules
2. **Use spatial validation** to prevent data leakage
3. **Document all assumptions** and limitations **transparently**
4. **Maintain version control** for rules and data
5. **Implement proper error handling** and logging
6. **Use reproducible pipelines** for rule updates
7. **Explicitly quantify uncertainty** as data quality levels, not prediction probabilities
8. **Plan for rule maintenance** and updates

### Reporting Guidelines - UPDATED
1. **Transparency**: Clearly state all limitations and data quality levels
2. **Reproducibility**: Provide all rule code and data information
3. **Uncertainty**: Report data quality levels (Verified/Preliminary/Insufficient)
4. **Validation**: Document all validation procedures for rules
5. **Ethics**: Consider ethical implications of risk screening (never claim prediction probabilities)

### Next milestone - DEPRECATED
**ML_METHODOLOGY.md**: Completed with comprehensive machine learning methodology.

**Key Points**:
- Emphasis on scientific rigor and data quality
- Comprehensive validation protocols including spatial validation
- Detailed model documentation and uncertainty quantification
- Clear guidelines for interpretability and explainability

**This milestone is superseded by the rule-based implementation in Phase 2-5 of TASK_TRACKER.md**.

## Task Tracker Update - DEPRECATED

**ML_METHODOLOGY.md**: Completed with comprehensive machine learning methodology.

**Key Points**:
- Emphasis on scientific rigor and data quality
- Comprehensive validation protocols including spatial validation
- Detailed model documentation and uncertainty quantification
- Best practices for deployment and monitoring
- Clear guidelines for interpretability and explainability

**This document is preserved for reference. The project has shifted to rule-based landslide risk screening.**

**Next milestone**: Implementation of rule-based screening (pending successful Phases 2-4 completion per TASK_TRACKER.md).