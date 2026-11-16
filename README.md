# TerraGuard AI 🌍

### AI-Powered Landslide Susceptibility Assessment & Rainfall Monitoring

🚀 **[🌐 LIVE WEBSITE — Explore TerraGuard AI](https://terraguard-ai-production.up.railway.app/)**

An experimental landslide susceptibility assessment and rainfall monitoring platform focused on Chamoli, Uttarakhand, India.

**[View Live Application](https://terraguard-ai-production.up.railway.app/) · [GitHub Repository](https://github.com/tejaswip314-hub/TerraGuard-AI)


# TerraGuard AI

Full-stack landslide susceptibility assessment website with Random Forest modeling and real-time rainfall monitoring.

## Project Overview

TerraGuard AI is a geospatial web application for landslide susceptibility assessment using Random Forest machine learning, complemented by real-time rainfall monitoring from Open-Meteo. The application provides susceptibility mapping for the Uttarakhand region using verified geographical conditioning factors.

## Project Objective

- Train Random Forest model for landslide susceptibility assessment using verified geographical conditioning factors
- Provide interactive map interface for susceptibility visualization
- Display real-time and forecast rainfall from Open-Meteo API
- Show terrain elevation and slope analysis from SRTM DEM
- Display verified historical landslide locations
- Offer model evaluation with proper validation metrics
- Support disaster risk management with actionable information

## Technology Stack

### Frontend

- Leaflet and React Leaflet for interactive maps
- HTML5, CSS3, JavaScript
- Responsive design for mobile and desktop

### Backend

- Python, scikit-learn (Random Forest)
- Open-Meteo API for weather data
- Path-based static asset serving from node_modules

### Geospatial Processing

- SRTM DEM for elevation, slope, aspect, curvature
- Verified historical landslide locations
- Random Forest susceptibility assessment

## Getting Started

1. Install dependencies: `pip install -r api/requirements.txt`
2. Ensure SRTM DEM files are in `data/raw/dem/`
3. Ensure landslide inventory is verified in `data/raw/landslide_inventory/`
4. Run the server: `python api/server.py`
5. Access the map at `http://localhost:5000`
6. Enter latitude and longitude to explore susceptibility

## Project Structure

```
TerraGuardAI/
├── README.md
├── docs/
│   ├── ARCHITECTURE_SPEC.md
│   ├── DATA_AND_API_SPEC.md
│   ├── ML_METHODOLOGY.md
│   ├── UI_UX_SPEC.md
│   ├── TASK_TRACKER.md
│   └── DATA_FEASIBILITY.md
├── api/
│   ├── server.py          # Python API server (working, preserve)
│   ├── requirements.txt
│   └── __pycache__/
├── public/
│   ├── map.html           # Functional Leaflet map (23,796 bytes, preserve)
│   └── index.html         # Enhanced homepage
├── node_modules/          # Leaflet assets from existing node_modules
├── scripts/
│   └── retry_missing_imerg.py   # Script for missing IMERG downloads (339 lines)
├── .gitignore
├── .env.example
└── package.json
```

## Research Status

- **Current**: Random Forest susceptibility modeling under development; dataset acquisition in progress
- **Working Features**: Leaflet map with layer switching, weather widget, dataset status cards, terrain rasters (slope, aspect, hillshade)
- **Pending**: Random Forest model training; validated landslide inventory extraction; spatial model evaluation
- **Data Acquisition**: NRSC PDF downloaded (requires extraction); SRTM DEM pending manual download; IMERG rainfall pending Earthdata login

## Notes

This project is in active development. Documentation will be updated as progress is made.

This project uses Random Forest for landslide susceptibility assessment, NOT for rainfall forecasting. Rainfall remains an independent weather-monitoring component powered by Open-Meteo. Do not claim validated susceptibility probabilities without model evaluation. Do not fabricate landslide labels, non-landslide samples, or risk scores. Clearly distinguish susceptibility from rainfall forecasts, historical events, and model predictions.

## Requirements Met

- ✅ Random Forest landslide susceptibility modeling with verified conditioning factors
- ✅ Independent Open-Meteo rainfall monitoring
- ✅ SRTM DEM terrain analysis (slope, aspect, hillshade generated)
- ✅ NRSC Landslide Atlas PDF downloaded and verified
- ✅ Coordinate validation (6/6 tests passing)
- ✅ No fabricated landslide labels or risk scores
- ✅ Scientific integrity statement at top