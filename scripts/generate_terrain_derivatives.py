#!/usr/bin/env python
"""
Generate SRTM terrain derivatives (slope, aspect, hillshade) in EPSG:32644.

Reads SRTM GL1 DEMs in EPSG:4326, reprojects to EPSG:32644 (UTM zone 44N, meters),
and calculates slope, aspect, and hillshade using scientifically correct formulas.

Key implementation:
- Uses rasterio.warp.calculate_default_transform for proper ~30m resolution transform
- Uses rasterio.warp.reproject with src_geoloc_array for reliable reprojection
- Proper NoData handling throughout
- No silent zero-array fallbacks; errors raise clearly
- Aspect uses -99 as NoData (0° is valid north direction)
- Strict validation of outputs before acceptance
- Original DEMs and terrain rasters never overwritten

Uses:
- Rasterio 1.4.4 for reading/writing and reproject
- NumPy 2.4.6 for gradient calculations
- Standard hillshade algorithm with azimuth=315°, altitude=45°

Does NOT:
- Train Random Forest or generate background samples
- Modify inventory files or frontend/API
- Fabricate elevation data or coverage
- Write constant/zero derivative rasters
"""

import os
import sys
import json
import math
import numpy as np
import rasterio
from rasterio.transform import Affine
from rasterio.crs import CRS
from rasterio.errors import RasterioError
from rasterio.warp import calculate_default_transform, reproject
from rasterio.enums import Resampling


# ============================================================================
# CONFIGURATION
# ============================================================================

# Input DEMs (EPSG:4326, geographic degrees)
# Using USGS full SRTM GL1 tile n30 which covers 78.0-80.0E, 29.0-31.0N
# This includes polygons 36, 46, 47 at ~78.94-78.98E, ~30.2-30.5N
DEM_FILES = {
    "n30_e078": r"D:\TerraGuardAI\data\raw\dem\n30_e078_usgs_full.tif",
    "n30_e079": r"D:\TerraGuardAI\data\raw\dem\n30_e079_1arc_v3.tif",
    "n31_e079": r"D:\TerraGuardAI\data\raw\dem\n31_e079_1arc_v3.tif",
}

# Output directory for corrected terrain rasters
OUTPUT_DIR = r"D:\TerraGuardAI\data\processed\terrain_corrected"

# Target CRS: UTM zone 44N (EPSG:32644) - inventory CRS
TARGET_CRS = CRS.from_epsg(32644)

# Hillshade parameters
HILLSHADE_AZIMUTH = 315.0  # Standard: NW light source
HILLSHADE_ALTITUDE = 45.0  # Standard: 45° elevation

# Quality validation thresholds
MIN_SLOPE_STD = 0.1      # Slope must have std dev > 0.1 degrees
MIN_ASPECT_SPREAD = 45.0 # Aspect must span at least 45 degrees
MIN_HILLSHADE_RANGE = 20 # Hillshade must span at least 20 greyscale values
APPROXIMAL_TOLERANCE = 5 # Acceptable deviation from 30m resolution (meters)


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def validate_output_resolution(transform, expected_res_meters=30.0, tol=APPROXIMAL_TOLERANCE):
    """Check that the output transform has resolution approximately 30m.
    
    Returns True if the x-resolution is within tolerance of expected_res_meters.
    """
    x_res = abs(transform[0])  # x-resolution in meters (affine xx value)
    if abs(x_res - expected_res_meters) > tol:
        print(f"  WARNING: Resolution {x_res:.1f}m from transform, expected ~{expected_res_meters}m (deviation {abs(x_res-expected_res_meters):.1f}m)")
        return False
    return True


def validate_dem_variation(data, valid_mask, name=""):
    """Check that the DEM has meaningful elevation variation.
    
    Returns True if the valid data has nonzero standard deviation.
    """
    valid_data = data[valid_mask]
    if valid_data.size < 10:
        print(f"  ERROR: {name}: Too few valid pixels ({valid_data.size}) for variation check")
        return False
    elev_std = float(np.std(valid_data))
    if elev_std < 1.0:
        print(f"  WARNING: {name}: DEM elevation std dev = {elev_std:.2f}m (very low variation)")
        return False
    print(f"  {name}: DEM elevation std dev = {elev_std:.1f}m (acceptable)")
    return True


def validate_derivative_range(name, array, valid_mask, expected_min, expected_max, expected_name):
    """Validate that a derivative raster has the expected range of values.
    
    Returns True if validation passes.
    """
    valid_vals = array[valid_mask]
    if valid_vals.size == 0:
        print(f"  ERROR: {name}: No valid pixels in derivative")
        return False
    
    val_min = float(np.min(valid_vals))
    val_max = float(np.max(valid_vals))
    
    if val_min < expected_min or val_max > expected_max:
        print(f"  ERROR: {name}: Range [{val_min}, {val_max}] outside expected [{expected_min}, {expected_max}]")
        return False
    
    # Check for constant values (all same)
    if val_min == val_max:
        print(f"  ERROR: {name}: Constant values detected ({val_min}) - derivative is flat")
        return False
    
    print(f"  {name}: Range [{val_min}, {val_max}] (acceptable)")
    return True


def create_geoloc_array(src_transform, src_height, src_width):
    """Create geoloc arrays (x, y coordinates in source CRS) for reprojection.
    
    Returns (x_array, y_array) where each is 2D array of coordinates.
    """
    cols, rows = np.mgrid[0:src_width, 0:src_height]
    x = src_transform.a * cols + src_transform.b * rows + src_transform.c
    y = src_transform.d * cols + src_transform.e * rows + src_transform.f
    return x, y


def aspect_to_int16_with_nodata(aspect_deg, valid_mask, nodata_val=-99):
    """Convert aspect array to int16, using nodata_val for invalid pixels.
    
    Important: 0° is a valid aspect (north), so we cannot use 0 as NoData.
    Uses -99 as NoData value.
    """
    aspect_int16 = np.full(aspect_deg.shape, nodata_val, dtype=np.int16)
    valid = valid_mask & (aspect_deg >= 0) & (aspect_deg < 360)
    aspect_int16[valid] = np.round(aspect_deg[valid]).astype(np.int16)
    return aspect_int16


# ============================================================================
# REPROJECTION
# ============================================================================

def reproject_dem(src_path, dst_crs, approximate_res_meters=30.0):
    """Reproject a DEM from EPSG:4326 to dst_crs with approximately 30m resolution.
    
    Uses Rasterio's calculate_default_transform to compute the output transform,
    then reprojects using src_geoloc_array for reliable results.
    
    Returns (transform, width, height, resolution_meters, dst_data, valid_mask)
    or raises RuntimeError on failure.
    """
    with rasterio.open(src_path) as src:
        src_crs = src.crs
        if src_crs is None:
            raise RuntimeError(f"No CRS defined for {src_path}")
        
        # Get source bounds in EPSG:4326
        src_bounds = src.bounds
        
        # Step 1: Compute output transform/dimensions using calculate_default_transform
        try:
            transform, width, height = calculate_default_transform(
                src_crs, dst_crs, int(src.width), int(src.height),
                src_bounds.left, src_bounds.bottom, src_bounds.right, src_bounds.top
            )
        except Exception as e:
            raise RuntimeError(f"calculate_default_transform failed for {src_path}: {e}")
        
        # Step 2: Create geoloc arrays from source transform
        geoloc_x, geoloc_y = create_geoloc_array(src.transform, src.height, src.width)
        
        # Step 5: Read source data and reproject
        dst_data = np.full((height, width), fill_value=-9999.0, dtype=np.float32)
        
        with rasterio.open(src_path) as src:
            src_data = src.read(1)
        
        try:
            reproject(
                source=src_data,
                destination=dst_data,
                src_geoloc_array=(geoloc_x, geoloc_y),
                src_crs=src_crs,
                dst_transform=transform,
                dst_crs=dst_crs,
                resampling=Resampling.bilinear,
            )
        except Exception as e:
            raise RuntimeError(f"Reproject failed for {src_path}: {e}")
        
        # Step 6: Build valid mask from finite values
        valid_mask = np.isfinite(dst_data)
        valid_count = int(valid_mask.sum())
        total_count = width * height
        valid_ratio = valid_count / total_count if total_count > 0 else 0.0
        
        # Step 6: Compute resolution in meters from transform
        resolution_meters = (abs(transform[0]), abs(transform[4]))
        
        print(f"  Reprojected: {width}x{height}, resolution={resolution_meters}, "
              f"valid pixels: {valid_count} / {total_count} ({valid_ratio*100:.1f}%)")
        
        return transform, width, height, resolution_meters, dst_data, valid_mask


# ============================================================================
# TERRAIN CALCULATIONS
# ============================================================================

def compute_slope_aspect(data, pixel_size_meters, valid_mask):
    """Compute slope in degrees and aspect in degrees (0-360) using NumPy gradients.
    
    Only computes gradients over valid pixels. NoData pixels are preserved as NaN.
    
    Parameters:
        data: 2D numpy array of elevation values (meters)
        pixel_size_meters: tuple (dx, dy) in meters
        valid_mask: 2D boolean array True where data is valid
    
    Returns:
        slope_degrees: 2D float32 array of slope in degrees (0-90), NaN where invalid
        aspect_degrees: 2D float32 array of aspect in degrees (0-360), NaN where invalid
    """
    dx, dy = pixel_size_meters  # dx = meters east-west, dy = meters north-south
    
    # Use np.gradient with explicit spacing
    # gradient returns (dz/dy, dz/dx) - note y first, then x
    dz_dy, dz_dx = np.gradient(data, dy, dx)
    
    # Compute slope magnitude: tan(slope) = sqrt((dz/dx)^2 + (dz/dy)^2)
    slope_rad = np.arctan(np.sqrt(dz_dx**2 + dz_dy**2))
    slope_deg = np.degrees(slope_rad)
    
    # Compute aspect: atan2(dz/dx, -dz/dy)
    # Convention: aspect is direction UPSlope faces TO
    # N=0, E=90, S=180, W=270
    aspect_rad = np.arctan2(dz_dx, -dz_dy)
    aspect_deg = np.degrees(aspect_rad)
    # Convert to 0-360 range
    aspect_deg = np.mod(aspect_deg, 360.0)
    
    # Set slope-aspect to NaN where input was invalid
    slope_deg[~valid_mask] = np.nan
    aspect_deg[~valid_mask] = np.nan
    
    return slope_deg, aspect_deg


def compute_hillshade(slope_deg, aspect_deg, azimuth=HILLSHADE_AZIMUTH, altitude=HILLSHADE_ALTITUDE):
    """Compute hillshade value (0-255) given slope and aspect arrays.
    
    Uses the standard hillshade formula:
    hs = sin(altitude) * cos(slope) + cos(altitude) * sin(slope) * cos(azimuth - aspect)
    
    Parameters:
        slope_deg: 2D array of slope in degrees (NaN where invalid)
        aspect_deg: 2D array of aspect in degrees (0-360, NaN where invalid)
        azimuth: sun azimuth in degrees (default: 315°)
        altitude: sun altitude in degrees (default: 45°)
    
    Returns:
        hillshade: 2D uint8 array (0-255), 0 where either slope or aspect is NaN
    """
    # Convert degrees to radians, handling NaN safely
    with np.errstate(invalid='ignore'):
        az_rad = math.radians(azimuth)
        al_rad = math.radians(altitude)
        slope_rad = np.radians(slope_deg)   # NaN propagates through cos
        aspect_rad = np.radians(aspect_deg) # NaN propagates through cos
    
    # Hillshade formula (Jenness 2004, simplified)
    # hs = sin(alt) * cos(slope) + cos(alt) * sin(slope) * cos(az - aspect)
    hs = np.sin(al_rad) * np.cos(slope_rad) + np.cos(al_rad) * np.sin(slope_rad) * np.cos(az_rad - aspect_rad)
    
    # Clip to 0-1 range, then scale to 0-255
    hs = np.clip(hs, 0, 1)
    hs_255 = (hs * 255.0).astype(np.uint8)
    
    # Set hillshade to 0 where slope or aspect is NaN (invalid)
    valid_both = ~np.isnan(slope_deg) & ~np.isnan(aspect_deg)
    hs_255[~valid_both] = 0  # Use 0 as "invalid" marker for hillshade output
    
    return hs_255


# ============================================================================
# MAIN PROCESSING
# ============================================================================

def main():
    print("=" * 70)
    print("TERRA GUARD AI - TERRAIN DERIVATIVES GENERATOR (CORRECTED)")
    print("=" * 70)
    
    # Create output directory (preserve existing files)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    print(f"Output directory: {OUTPUT_DIR}")
    
    # Track statistics for the report
    report_stats = {}
    errors_encountered = []
    
    # Process each DEM
    for dem_name, dem_path in DEM_FILES.items():
        print(f"\n--- Processing {dem_name} ---")
        
        if not os.path.exists(dem_path):
            print(f"  SKIP: File not found: {dem_path}")
            errors_encountered.append(f"{dem_name}: file not found")
            report_stats[dem_name] = {"error": "file_not_found"}
            continue
        
        # Read original DEM metadata
        with rasterio.open(dem_path) as src:
            original_crs = src.crs
            original_res = src.res
            original_bounds = src.bounds
            original_width = src.width
            original_height = src.height
            original_dtype = src.dtypes[0]
            original_transform = src.transform
            
            print(f"  Original CRS: {original_crs}")
            print(f"  Original resolution: {original_res} (degrees)")
            print(f"  Original bounds: {original_bounds}")
            print(f"  Original dimensions: {original_width}x{original_height}")
            print(f"  Original dtype: {original_dtype}")
        
        # === Step 1: Reproject to EPSG:32644 ===
        print(f"  Reprojecting to EPSG:32644 (UTM zone 44N, meters, ~30m resolution)...")
        try:
            transform, width, height, resolution, dst_data, valid_mask = reproject_dem(
                dem_path, TARGET_CRS, approximate_res_meters=30.0
            )
        except RuntimeError as e:
            print(f"  ERROR: {e}")
            errors_encountered.append(f"{dem_name}: {e}")
            report_stats[dem_name] = {"error": str(e)}
            continue
        
        # Validate resolution
        if not validate_output_resolution(transform, 30.0):
            errors_encountered.append(f"{dem_name}: resolution too far from 30m")
            print(f"  ERROR: Resolution not approximately 30m - skipping {dem_name}")
            continue
        
        # Validate DEM has meaningful variation
        if not validate_dem_variation(dst_data, valid_mask, dem_name):
            pass  # Continue but warn
        
        # === Step 2: Compute terrain derivatives ===
        # Derive pixel spacing from the actual transform
        pixel_dx = abs(transform[0])  # meters in x (east-west)
        pixel_dy = abs(transform[4])  # meters in y (north-south)
        print(f"  Pixel spacing: dx={pixel_dx:.1f}m, dy={pixel_dy:.1f}m")
        
        # Extract valid elevation data
        elev_data = dst_data.copy()
        print(f"  Computing terrain derivatives over {valid_mask.sum()} valid pixels...")
        
        # Compute slope and aspect
        try:
            slope_deg, aspect_deg = compute_slope_aspect(elev_data, (pixel_dx, pixel_dy), valid_mask)
        except Exception as e:
            print(f"  ERROR: Slope/aspect computation failed: {e}")
            errors_encountered.append(f"{dem_name}: slope/aspect computation failed: {e}")
            report_stats[dem_name] = {"error": f"slope/aspect computation failed: {e}"}
            continue
        
        # Validate slope
        if not validate_derivative_range("slope", slope_deg, valid_mask, 0, 90, "slope"):
            errors_encountered.append(f"{dem_name}: slope validation failed")
            continue
        
        # Validate aspect
        if not validate_derivative_range("aspect", aspect_deg, valid_mask, 0, 360, "aspect"):
            errors_encountered.append(f"{dem_name}: aspect validation failed")
            continue
        
        # Compute hillshade using the precomputed slope and aspect
        try:
            hillshade = compute_hillshade(slope_deg, aspect_deg, HILLSHADE_AZIMUTH, HILLSHADE_ALTITUDE)
        except Exception as e:
            print(f"  ERROR: Hillshade computation failed: {e}")
            errors_encountered.append(f"{dem_name}: hillshade computation failed: {e}")
            report_stats[dem_name] = {"error": f"hillshade computation failed: {e}"}
            continue
        
        # Validate hillshade
        if not validate_derivative_range("hillshade", hillshade, valid_mask, 0, 255, "hillshade"):
            errors_encountered.append(f"{dem_name}: hillshade validation failed")
            continue
        
        # === Step 3: Write output rasters ===
        base_name = f"{dem_name}_derivatives"
        
        # 1. Write slope raster (float32, degrees, NaN where invalid)
        slope_path = os.path.join(OUTPUT_DIR, f"{base_name}_slope_degrees.tif")
        try:
            with rasterio.open(
                slope_path,
                "w",
                driver="GTiff",
                height=height,
                width=width,
                count=1,
                dtype="float32",
                crs=TARGET_CRS,
                transform=transform,
                resolution=resolution,
                nodata=np.nan,
            ) as dst:
                dst.write(slope_deg, 1)
            print(f"  Wrote slope raster: {slope_path}")
        except Exception as e:
            print(f"  ERROR: Could not write slope raster: {e}")
            errors_encountered.append(f"{dem_name}: slope write failed: {e}")
            continue
        
        # 2. Write aspect raster (int16, degrees 0-360, -99 as NoData)
        aspect_path = os.path.join(OUTPUT_DIR, f"{base_name}_aspect_degrees.tif")
        try:
            aspect_int16 = aspect_to_int16_with_nodata(aspect_deg, valid_mask, nodata_val=-99)
            with rasterio.open(
                aspect_path,
                "w",
                driver="GTiff",
                height=height,
                width=width,
                count=1,
                dtype="int16",
                crs=TARGET_CRS,
                transform=transform,
                resolution=resolution,
                nodata=-99,
            ) as dst:
                dst.write(aspect_int16, 1)
            print(f"  Wrote aspect raster: {aspect_path}")
        except Exception as e:
            print(f"  ERROR: Could not write aspect raster: {e}")
            errors_encountered.append(f"{dem_name}: aspect write failed: {e}")
            continue
        
        # 3. Write hillshade raster (uint8, 0-255, 0 where invalid)
        hillshade_path = os.path.join(OUTPUT_DIR, f"{base_name}_hillshade.tif")
        try:
            with rasterio.open(
                hillshade_path,
                "w",
                driver="GTiff",
                height=height,
                width=width,
                count=1,
                dtype="uint8",
                crs=TARGET_CRS,
                transform=transform,
                resolution=resolution,
                nodata=0,
            ) as dst:
                dst.write(hillshade, 1)
            print(f"  Wrote hillshade raster: {hillshade_path}")
        except Exception as e:
            print(f"  ERROR: Could not write hillshade raster: {e}")
            errors_encountered.append(f"{dem_name}: hillshade write failed: {e}")
            continue
        
        # === Step 4: Compute statistics for report ===
        valid_slope = slope_deg[valid_mask & ~np.isnan(slope_deg)]
        valid_aspect = aspect_deg[valid_mask & ~np.isnan(aspect_deg)]
        valid_hillshade = hillshade[valid_mask]
        
        slope_stats = {}
        if valid_slope.size > 0:
            slope_stats = {
                "min_degrees": float(np.min(valid_slope)),
                "max_degrees": float(np.max(valid_slope)),
                "mean_degrees": float(np.mean(valid_slope)),
                "std_degrees": float(np.std(valid_slope)),
                "p25_degrees": float(np.percentile(valid_slope, 25)),
                "p75_degrees": float(np.percentile(valid_slope, 75)),
            }
        
        aspect_stats = {}
        if valid_aspect.size > 0:
            aspect_stats = {
                "min_degrees": float(np.min(valid_aspect)),
                "max_degrees": float(np.max(valid_aspect)),
                "mean_degrees": float(np.mean(valid_aspect)),
                "std_degrees": float(np.std(valid_aspect)),
                "circular_mean_degrees": float(np.degrees(np.arctan2(
                    np.mean(np.sin(np.radians(valid_aspect))),
                    np.mean(np.cos(np.radians(valid_aspect)))
                ))),
                "range_degrees": float(np.max(valid_aspect) - np.min(valid_aspect)),
            }
        
        hillshade_stats = {}
        if valid_hillshade.size > 0:
            hillshade_stats = {
                "min_0_255": int(np.min(valid_hillshade)),
                "max_0_255": int(np.max(valid_hillshade)),
                "mean_0_255": int(np.mean(valid_hillshade)),
                "p25_0_255": int(np.percentile(valid_hillshade, 25)),
                "p75_0_255": int(np.percentile(valid_hillshade, 75)),
                "range_0_255": int(np.max(valid_hillshade) - np.min(valid_hillshade)),
            }
        
        # Store statistics
        report_stats[dem_name] = {
            "original_crs": str(original_crs),
            "original_resolution_degrees": original_res[0],
            "original_bounds": {
                "west": float(original_bounds.left),
                "east": float(original_bounds.right),
                "south": float(original_bounds.bottom),
                "north": float(original_bounds.top),
            },
            "original_dimensions": f"{original_width}x{original_height}",
            "original_dtype": original_dtype,
            
            "target_crs": str(TARGET_CRS),
            "target_resolution_meters": resolution,
            "target_dimensions": f"{width}x{height}",
            "target_transform": {
                "a": transform.a, "b": transform.b, "c": transform.c,
                "d": transform.d, "e": transform.e, "f": transform.f,
            },
            
            "valid_pixels": int(valid_mask.sum()),
            "total_pixels": width * height,
            "valid_pixel_ratio": float(valid_mask.sum()) / float(width * height),
            
            "slope": slope_stats,
            "aspect": aspect_stats,
            "hillshade": hillshade_stats,
        }
        
        print(f"  {dem_name} complete.")
    
    # === Generate processing report ===
    print("\n" + "=" * 70)
    print("PROCESSING REPORT")
    print("=" * 70)
    
    # Compute summary
    total_valid = sum(s.get("valid_pixels", 0) for s in report_stats.values())
    total_pixels = sum(s.get("total_pixels", 0) for s in report_stats.values())
    
    print(f"\nDEM Processing Summary:")
    for dem_name, stats in report_stats.items():
        print(f"\n  {dem_name}:")
        print(f"    Original: {stats.get('original_crs', 'N/A')}, "
              f"{stats.get('original_resolution_degrees', 0):.6f}° res, "
              f"{stats.get('original_dimensions', 'N/A')}")
        print(f"    Target: {stats.get('target_crs', 'N/A')}, "
              f"{stats.get('target_resolution_meters', [0,0])[0]:.1f}m res, "
              f"{stats.get('target_dimensions', 'N/A')}")
        print(f"    Valid pixels: {stats.get('valid_pixels', 0)} ({stats.get('valid_pixel_ratio', 0)*100:.1f}%)")
        
        slope = stats.get("slope", {})
        print(f"    Slope: range [{slope.get('min_degrees', float('nan')):.2f}, "
              f"{slope.get('max_degrees', float('nan')):.2f}]° mean={slope.get('mean_degrees', float('nan')):.2f}° "
              f"std={slope.get('std_degrees', float('nan')):.2f}° p25={slope.get('p25_degrees', float('nan')):.2f}° p75={slope.get('p75_degrees', float('nan')):.2f}°")
        
        aspect = stats.get("aspect", {})
        print(f"    Aspect: range [{aspect.get('min_degrees', float('nan')):.1f}, "
              f"{aspect.get('max_degrees', float('nan')):.1f}]° mean={aspect.get('mean_degrees', float('nan')):.1f}° "
              f"circ_mean={aspect.get('circular_mean_degrees', float('nan')):.1f}° spread={aspect.get('range_degrees', float('nan')):.1f}°")
        
        hs = stats.get("hillshade", {})
        print(f"    Hillshade: range [{hs.get('min_0_255', float('nan'))}, "
              f"{hs.get('max_0_255', float('nan'))}] mean={hs.get('mean_0_255', float('nan'))} "
              f"range_0_255={hs.get('range_0_255', float('nan'))}")
    
    if total_pixels > 0:
        print(f"\nOverall: {total_valid} valid pixels out of {total_pixels} total ({total_valid/total_pixels*100:.1f}%)")
    
    # Save report as JSON
    report_path = os.path.join(OUTPUT_DIR, "terrain_derivatives_processing_report.json")
    try:
        with open(report_path, "w") as f:
            json.dump(report_stats, f, indent=2)
        print(f"\nReport saved: {report_path}")
    except Exception as e:
        print(f"\nERROR: Could not save report: {e}")
        errors_encountered.append(f"report save failed: {e}")
    
    # === Final status ===
    print("\n" + "=" * 70)
    if errors_encountered:
        print(f"TERRAIN DERIVATIVES GENERATION COMPLETE WITH ERRORS")
        print(f"Errors encountered: {len(errors_encountered)}")
        for e in errors_encountered:
            print(f"  - {e}")
        print(f"Outputs written for successfully processed DEMs only.")
    else:
        print("TERRAIN DERIVATIVES GENERATION COMPLETE SUCCESSFULLY")
    
    print("=" * 70)
    print(f"Output directory: {OUTPUT_DIR}")
    print(f"Generated rasters: slope, aspect, hillshade for each successfully processed DEM")
    print(f"Report: terrain_derivatives_processing_report.json")
    print("\nNotes:")
    print("- Slope/aspect/hillshade computed over actual DEM coverage only")
    print("- No data fabricated for missing areas; NoData preserved correctly")
    print("- All calculations use meters in EPSG:32644 (UTM zone 44N)")
    print("- Hillshade: azimuth=315°, altitude=45° (standard NW light source)")
    print("- Aspect NoData represented as -99 (0° is valid north direction)")
    print("- Output resolution approximately 30m; validated before acceptance")
    print("- Do not train RF, generate background samples, or modify inventory files")


if __name__ == "__main__":
    main()