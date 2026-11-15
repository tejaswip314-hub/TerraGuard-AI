# Application Architecture Specification

## Project Overview

TerraGuard AI is a geospatial web application for landslide susceptibility assessment using Random Forest machine learning, complemented by real-time rainfall monitoring. The application provides susceptibility mapping for the Uttarakhand region using verified geographical conditioning factors.

## Architecture Overview

### Frontend Architecture

**Framework:** HTML5, JavaScript, Leaflet and React-Leaflet (where applicable)
**Language:** JavaScript, HTML
**Styling:** CSS

**Component Structure:**
```
frontend/
├── components/
│   ├── maps/              # Interactive map components
│   ├── cards/             # Data display cards
│   ├── forms/             # Data input forms
│   └── ui/               # Shared UI components
├── pages/                 # HTML pages
├── utils/                # Helper functions
└── services/             # API service layers
```

### Backend Architecture

**Framework:** Python http.server with scikit-learn (no Flask/FastAPI dependency required)
**Language:** Python 3.x

**Service Structure:**
```
api/
├── server.py             # Python API server (working, preserve)
│   ├── Health endpoints: /, /health, /status
│   ├── Data endpoints: /datasets, /layers
│   ├── Weather endpoint: /weather?lat=&lon=
│   ├── RF endpoint: /rf/susceptibility - Random Forest susceptibility prediction
│   └── Node modules routing: /node_modules/ (serves from PROJECT_ROOT/node_modules)
└── public/               # Static files served from PROJECT_ROOT/public
```

## API Endpoints Specification

### Health and Status API

- `GET /` - Root endpoint, health check
- `GET /health` - Health check endpoint
- `GET /status` - Status endpoint with service details

### Geospatial Data API

- `GET /datasets` - Dataset status and verification information
- `GET /layers` - Available map layers and sources
- `GET /weather?lat=&lon=` - Open-Meteo weather forecast for coordinates
  - Default coordinates: Uttarakhand pilot region (30.33, 79.42)
  - Optional query parameters: lat, lon
  - Returns precipitation, rain, showers, precipitation probability, wind gusts
- `GET /node_modules/leaflet/dist/leaflet.css` - Leaflet CSS from node_modules
- `GET /node_modules/leaflet/dist/leaflet.js` - Leaflet JS from node_modules

### Random Forest Susceptibility API

- `GET /rf/susceptibility?lat=&lon=` - Get susceptibility prediction for coordinates
  - Default coordinates: Uttarakhand pilot region (30.33, 79.42)
  - Optional query parameters: lat, lon
  - Returns susceptibility class, probability, contributing factors
  - Model: RandomForestClassifier with verified conditioning factors
  - Output: susceptibility class (Low/Medium/High), probability percentage, list of active factors

## Data Flow Architecture

### Frontend to Backend

1. User selects location via coordinate entry form
2. Frontend sends request to appropriate API endpoint
3. Backend validates input parameters
4. Backend fetches data (weather, terrain info, model prediction)
5. Results returned to frontend for display

### Backend Internal Flow

1. Input validation and parameter checking
2. Data fetching from Open-Meteo API (weather independent)
3. Terrain data processing (slope, elevation from DEM)
4. Random Forest susceptibility prediction (using verified conditioning factors)
5. Result formatting and output

## Database Architecture

**Primary Storage:** No database required (file-based approach)

**Data Model:**
- **Study Areas**: Geographic boundaries and metadata (from verified inventory)
- **Historical Data**: Recorded landslide locations and dates (from NRSC inventory PDF)
- **Weather Data**: Open-Meteo API forecast data (temporary, per-request)
- **Terrain Data**: Elevation, slope, aspect, curvature information (from SRTM DEM)
- **RF Model Data**: Model metadata, feature importances, validation results (after training)

## Deployment Architecture

### Development Environment

- **Local**: Python server (`python api/server.py`)
- **Version Control**: Git with feature branch workflow
- **Static hosting**: Any static web server or GitHub Pages

### Production Environment

- **Frontend**: Static file hosting (Vercel, Netlify, GitHub Pages)
- **Backend**: Python any-host server (Heroku, Render, AWS, or simple VPS)
- **No database required** - file-based approach (model files stored on server)

### Infrastructure as Code

- **Docker**: Optional containerization
- **Static export**: GitHub Pages compatible

## Technology Stack Matrix

| Layer | Technology | Version | Notes |
|-------|------------|---------|-------|
| Frontend | Leaflet | 1.9+ | Interactive maps |
| Frontend | React Leaflet | Latest | Map integration (where used) |
| Backend | Python | 3.10+ | Standard library + scikit-learn |
| Backend | Open-Meteo API | Latest | Weather forecast data |
| ML | scikit-learn | 1.3+ | Random Forest |
| Geospatial | SRTM DEM | 30m | Elevation and slope data |
| Styling | CSS | Modern | Responsive design |

## Quality Assurance

### Testing Strategy

- **Unit Tests**: Individual component testing (frontend)
- **API Tests**: Endpoint functionality verification
- **Integration Tests**: Map-terrain-weather-RF interaction

### Code Quality

- **Linting**: ESLint (frontend), flake8 (backend Python)
- **Formatting**: Prettier, black
- **Type Checking**: JavaScript/TypeScript where applicable

## Monitoring and Observability

### Application Metrics

- Response times for API endpoints
- Error rates
- Model prediction success rates

### Logging

- Structured logging for API requests
- Error tracking and reporting

## Risk Assessment

### Technical Risks

- Data Availability: Limited dataset accessibility (pending acquisition)
- Model Generalization: Model performance on unseen locations
- Scalability: Static file hosting is inherently scalable
- Integration: Geospatial data handling (pending data acquisition)

### Mitigation Strategies

- **Data Strategy**: Documented data acquisition checklist (DATA_ACQUISITION_CHECKLIST.md)
- **Model Strategy**: Spatial validation, hold-out test set, prefer spatial block CV
- **Infrastructure**: Static hosting is low-cost and scalable
- **Integration**: Modular API design with clear separation of concerns

## Conclusion

This architecture specification provides a comprehensive framework for developing TerraGuard AI as a landslide susceptibility assessment website with Random Forest modeling and real-time rainfall monitoring. The modular design allows for incremental development while maintaining scalability and maintainability. Key focus areas include data accessibility, model transparency, and user experience optimization.

**Task Tracker Update**: ARCHITECTURE_SPEC.md completed with updated technical specifications for RF susceptibility modeling.

**Next milestone**: Implementation of coordinate explorer and basic map navigation (Phase 2).

## Model Training Protocol (Documentation)

### Conditioning Factors

The Random Forest model uses the following verified geographical conditioning factors:

1. **Elevation** (meters) - from SRTM DEM
2. **Slope** (degrees) - from SRTM DEM (generated in data/processed/terrain/)
3. **Aspect** (degrees, 0-360) - from SRTM DEM (generated in data/processed/terrain/)
4. **Curvature** (profile and planial) - from SRTM DEM (if reliable method available)
5. **Geological characteristics** - if available from GSI/Survey of India
6. **Soil information** - if available from Soil Survey of India
7. **Land use/land cover** - if available from ESA WorldCover or NRSC
8. **Distance to streams** - calculated from hydrography data, if reliable
9. **Distance to roads** - calculated from OSM/hydrography data, if reliable
10. **Distance to faults** - if reliable geological data available

### Training Data Requirements

- **Minimum 50 recorded landslide events** with verified coordinates
- **Minimum 100 negative samples** (locations without landslides) from stable areas
- **Spatial distribution**: landslides should be distributed across the pilot area
- **Attribute data**: all conditioning factors must be available for each sample
- **Quality control**: landslide labels must be verified (not fabricated)

### Negative Sampling Methodology

1. Identify stable areas using slope < 5° and elevation outside landslide-prone bands
2. Select random points from stable areas
3. Verify each point is > 200m from any recorded landslide polygon
4. Document the negative-sampling methodology in model metadata
5. Ensure negative samples are geographically distributed across the pilot area

### Spatial Validation Strategy

1. **Hold-out test set**: 20% of landslide points reserved for final evaluation
2. **Spatial block cross-validation**: Divide pilot area into blocks; train on k-1 blocks, test on remaining block
3. **Report metrics**: ROC-AUC, precision, recall, F1-score, confusion matrix
4. **Do not report before evaluation**: Model must be trained and evaluated before claiming any performance metrics
5. **Document spatial Autocorrelation**: Test for spatial autocorrelation in training data; if high, prefer spatial CV over random CV

### Model Evaluation

```python
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import cross_val_score, SpatialBlockCV
from sklearn.metrics import classification_report, confusion_matrix, roc_auc_score

# Example training pipeline
rf = RandomForestClassifier(n_estimators=100, random_state=42)
scores = cross_val_score(rf, X, y, cv=SpatialBlockCV(block_size=5000))
print(f"Spatial CV ROC-AUC: {scores.mean():.3f} (+/- {scores.std():.3f})")

# Final evaluation on hold-out test set
rf.fit(X_train, y_train)
y_pred = rf.predict(X_test)
y_proba = rf.predict_proba(X_test)[:, 1]
print(classification_report(y_test, y_pred))
print(f"ROC-AUC: {roc_auc_score(y_test, y_proba):.3f}")
print(confusion_matrix(y_test, y_pred))
```

### Feature Importance

After training, report feature importances:

```python
importances = rf.feature_importances_
feature_names = ['elevation', 'slope', 'aspect', 'curvature', 'geological', 'soil', 'landcover', 'dist_streams', 'dist_roads', 'dist_faults']
importance_df = pd.DataFrame({'feature': feature_names, 'importance': importances})
importance_df = importance_df.sort_values('importance', ascending=False)
```

### Uncertainty Statement

> "This is a Random Forest susceptibility assessment, not a real-time prediction. Susceptibility indicates areas where landslides have occurred historically under similar conditions. Actual landslide occurrence depends on countless factors including recent rainfall, specific terrain conditions, and triggering events. For emergency concerns, consult official meteorological and geological authorities. Model ROC-AUC: [value], validated on hold-out test set."

## Deployment Notes

- Model file stored as `rf_susceptibility_model.pkl` or `rf_susceptibility_model.json`
- Feature importances documented in model metadata
- Do not use model for real-time prediction without validated test performance
- Clearly distinguish susceptibility from rainfall-triggered hazard and real-time prediction
- Model retraining recommended when new validated landslide inventory data becomes available

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

## Task Tracker Update (Updated)

**ARCHITECTURE_SPEC.md**: Completed with detailed technical specifications for RF susceptibility modeling.

**Key Deliverables**:
- Random Forest susceptibility API endpoint specification
- Conditioning factor specifications (elevation, slope, aspect, curvature, etc.)
- Spatial validation strategy (spatial block CV, hold-out test set)
- Feature importance reporting specifications
- Model metadata and persistence specification
- Pre-existing architecture specifications preserved (Python, http.server, Leaflet/React-Leaflet, Open-Meteo)

**Next milestone**: Implementation of RF model training and API endpoint (pending dataset acquisition and validation).

---
*Note: This architecture specification incorporates Random Forest landslide susceptibility modeling while maintaining the independent Open-Meteo weather component. The full architecture preserves all pre-existing specifications (Python http.server, Leaflet map, dataset status tracking) and adds RF model training, evaluation, and prediction endpoints.*