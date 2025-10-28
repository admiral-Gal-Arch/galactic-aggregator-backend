import asyncio
import httpx
import json
from typing import Dict, Any, List, Optional
from datetime import datetime, date, timedelta
from fastapi import FastAPI, HTTPException, status, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from contextlib import asynccontextmanager
from dotenv import load_dotenv
import os

# --- Load Environment Variables ---
load_dotenv()
# --- 1. CONFIGURATION ---
NASA_API_KEY = os.getenv("NASA_API_KEY", "DEMO_KEY")
NOAA_API_KEY = os.getenv("NOAA_API_KEY", "DEMO_NOAA_KEY") 
REFRESH_INTERVAL_SECONDS = int(os.getenv("REFRESH_INTERVAL_SECONDS", "30"))

# In-memory store for aggregated data
IN_MEMORY_CACHE: Dict[str, Any] = {}
STOP_EVENT = asyncio.Event() 

# Base URLs for clarity
NASA_BASE = "https://api.nasa.gov"
SPACEX_BASE = "https://api.spacexdata.com"
ISS_BASE = "http://api.open-notify.org"
EXOPLANET_BASE = "https://exoplanetarchive.ipac.caltech.edu/cgi-bin/nph-ws/pub/scs/query"

# NEW EXTERNAL APIs
GEO_BASE = "https://nominatim.openstreetmap.org"
NEWS_BASE = "https://api.spaceflightnewsapi.net/v4"
NOAA_BASE = "https://www.ngdc.noaa.gov/geomag-web/ws/models" # Mock endpoint

# NEW APIs for AI TRACK ADVISOR
STARLINK_BASE = f"{SPACEX_BASE}/v4/starlink"
DEBRIS_BASE = "https://api.spaceweb.com/conjunctions" # Mock service endpoint
TELEMETRY_BASE = "https://telemetry.mock.com/api/v1/stream" # Mock service endpoint

# APIS used by the internal cache refresh (core dashboard metrics)
today = datetime.now().strftime("%Y-%m-%d")
EXTERNAL_APIS: Dict[str, str] = {
    "nasa_apod": f"{NASA_BASE}/planetary/apod?api_key={NASA_API_KEY}",
    "spacex_latest": f"{SPACEX_BASE}/v5/launches/latest",
    "iss_location": f"{ISS_BASE}/iss-now.json",
    "people_in_space": f"{ISS_BASE}/astros.json",
    "nasa_neo": f"{NASA_BASE}/neo/rest/v1/feed?start_date={today}&end_date={today}&api_key={NASA_API_KEY}",
}

# --- 2. Data Schemas ---

class AggregatedData(BaseModel):
    apod_title: str
    spacex_flight_number: str
    iss_location: str
    people_in_space_count: str
    neo_count: str
    api_count: int
    last_updated: str

class APODItem(BaseModel):
    title: str
    date: str
    url: str | None = None
    explanation: str

class PeopleInSpace(BaseModel):
    message: str
    number: int
    people: List[Dict[str, str]]

class SpaceXLaunch(BaseModel):
    flight_number: int
    name: str
    date_utc: str
    
class NewsArticle(BaseModel):
    id: int
    title: str
    url: str
    summary: str

# --- 3. CORE FETCHING & PROCESSING LOGIC ---

async def fetch_api_data(url: str, client: httpx.AsyncClient, headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Handles an individual API call with error handling."""
    try:
        response = await client.get(url, timeout=10.0, headers=headers)
        response.raise_for_status() 
        return response.json()
    
    except httpx.TimeoutException:
        print(f"Error: Timeout contacting external API: {url}")
        return {"error": "Timeout Error"}

    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code
        print(f"Error: HTTP Error {status_code} for external API: {url}")
        return {"error": f"HTTP Status Error {status_code}"}

    except httpx.RequestError as exc:
        print(f"Error: Connection failed for external API: {url}")
        return {"error": "Connection Failed"}

    except Exception as e:
        error_detail = str(e).split('\n')[0]
        print(f"Error: Unexpected processing error (JSON/Format issue) for {url}. Detail: {error_detail}")
        return {"error": "Unexpected Processing Error"}

async def refresh_and_cache_data():
    """Asynchronously fetches data for the main dashboard metrics."""
    global IN_MEMORY_CACHE
    print(f"\n[CACHE REFRESH] Starting data fetch at {datetime.now().isoformat()}")

    api_urls = list(EXTERNAL_APIS.values())
    
    async with httpx.AsyncClient() as client:
        tasks = [fetch_api_data(url, client) for url in api_urls]
        results: List[Dict[str, Any]] = await asyncio.gather(*tasks)

    apod_data, spacex_data, iss_data, astros_data, neo_data = results

    def get_safe_value(data: Dict[str, Any], keys: List[str], default_msg: str) -> str:
        if "error" in data:
            return data['error']
        try:
            current_data = data
            for key in keys:
                current_data = current_data.get(key, {})
            return str(current_data) if current_data is not None else default_msg
        except Exception:
            return default_msg

    apod_title = get_safe_value(apod_data, ['title'], 'Error fetching APOD data.')
    spacex_flight_number = get_safe_value(spacex_data, ['flight_number'], 'N/A')
    lat = get_safe_value(iss_data, ['iss_position', 'latitude'], 'N/A')
    lon = get_safe_value(iss_data, ['iss_position', 'longitude'], 'N/A')
    iss_location = f"Lat: {lat}, Lon: {lon}" if lat != 'N/A' else 'N/A'
    count = get_safe_value(astros_data, ['number'], 'N/A')
    people_in_space_count = f"{count} people" if str(count).isdigit() else str(count)
    neo_list: List[Any] = neo_data.get('near_earth_objects', {}).get(today, [])
    
    if "error" in neo_data:
        neo_count = neo_data['error']
    else:
        neo_count = f"{len(neo_list)} Asteroids"

    new_data = {
        "apod_title": apod_title,
        "spacex_flight_number": spacex_flight_number,
        "iss_location": iss_location,
        "people_in_space_count": people_in_space_count,
        "neo_count": neo_count,
        "api_count": len(EXTERNAL_APIS),
        "last_updated": datetime.now().isoformat()
    }
    
    IN_MEMORY_CACHE = new_data
    print(f"[CACHE REFRESH] Cache successfully updated at {new_data['last_updated']}")

# --- ASYNCHRONOUS CACHE LOOP ---

async def continuous_refresh_async_loop():
    await refresh_and_cache_data()
    while not STOP_EVENT.is_set():
        try:
            await asyncio.sleep(REFRESH_INTERVAL_SECONDS)
            await refresh_and_cache_data()
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[CACHE REFRESH] Unhandled error in async loop: {e}. Sleeping longer.")
            await asyncio.sleep(REFRESH_INTERVAL_SECONDS * 2)

# --- 4. FASTAPI LIFESPAN AND APP INIT ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.refresh_task = asyncio.create_task(continuous_refresh_async_loop())
    print(f"[STARTUP] Asynchronous background refresh task started. Interval: {REFRESH_INTERVAL_SECONDS}s")
    
    yield

    app.state.refresh_task.cancel()
    try:
        await app.state.refresh_task
    except asyncio.CancelledError:
        pass
    print("[SHUTDOWN] Background refresh task stopped.")


app = FastAPI(
    title="Galactic Archives Hackathon API",
    description="Specialized API endpoints tailored for the Hackathon's Alien Artifact, Cartographer, and Mission Control tracks.",
    version="2.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- 5. FRONTEND SERVING LOGIC ---

try:
    with open("index.html", "r", encoding='utf-8') as f: 
        INDEX_HTML_CONTENT = f.read()
except FileNotFoundError:
    INDEX_HTML_CONTENT = "<h1>Error: index.html not found! Ensure it is in the same directory as api_aggitator.py.</h1>"

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def get_dashboard_html():
    return INDEX_HTML_CONTENT

# --- 6. CACHED API ENDPOINT ---

@app.get("/api/dashboard", response_model=AggregatedData, summary="[Core] Get cached, aggregated metrics for the dashboard.")
async def get_aggregated_dashboard_data():
    if not IN_MEMORY_CACHE:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service cache is not yet initialized. Please wait a moment and retry."
        )
    return AggregatedData(**IN_MEMORY_CACHE)

# =========================================================================
# --- 7. HACKATHON TRACK ENDPOINTS (LIVE FETCH & AGGREGATED) ---
# =========================================================================

# --- Helper for NEO Classification Data ---
async def fetch_and_process_catalog_data(page: int, size: int, client: httpx.AsyncClient) -> Dict[str, Any]:
    """Helper to fetch and clean paginated NEO data for Alien Artifact track."""
    url = (
        f"{NASA_BASE}/neo/rest/v1/neo/browse?"
        f"page={page}&"
        f"size={size}&"
        f"api_key={NASA_API_KEY}"
    )
    result = await fetch_api_data(url, client)
    
    if "error" in result:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"External NEO Browse Error: {result['error']}"
        )
        
    def extract_neo_metrics(neo):
        """Simplifies NEO data for classification models."""
        orbital_data = neo.get('orbital_data', {})
        
        diameter_max = neo.get('estimated_diameter', {}).get('kilometers', {}).get('estimated_diameter_max', 0)
        
        return {
            "name": neo.get('name'),
            "id": neo.get('id'),
            "absolute_magnitude_h": neo.get('absolute_magnitude_h'),
            "avg_diameter_km": diameter_max,
            "moid": float(orbital_data.get('minimum_orbit_intersection', 0.0)),
            "orbital_period_days": float(orbital_data.get('orbital_period', 0.0)),
            "is_hazardous": neo.get('is_potentially_hazardous_asteroid'),
        }

    return {
        "page": result.get('page', {}),
        "data_points": [extract_neo_metrics(neo) for neo in result.get('near_earth_objects', [])]
    }


# --- ALIEN ARTIFACTS DIVISION (Anomaly Detection & Novel Data) ---

@app.get("/api/artifact/neo_clusters", summary="[Alien Artifacts] Paginated NEO Data for ML Clustering.")
async def get_neo_clusters(
    page: int = Query(0, description="Page number (0-based index).", ge=0),
    size: int = Query(50, description="Items per page (Max 50).", ge=1, le=50)
):
    """Returns a highly simplified, paginated dataset of NEOs ready for clustering/classification."""
    async with httpx.AsyncClient() as client:
        return await fetch_and_process_catalog_data(page, size, client)
        
@app.get("/api/artifact/geomagnetic_data", summary="[Alien Artifacts] Latest Geomagnetic Field Data (Anomaly Search).")
async def get_geomagnetic_data(
    lat: float = Query(51.5, description="Latitude (e.g., London)."),
    lon: float = Query(0.0, description="Longitude (e.g., London).")
):
    """
    Returns simulated/mocked geomagnetic field data for a location. 
    Contestants can search for deviations (anomalies) from a normal baseline.
    Requires NOAA_API_KEY if using real NOAA endpoints (currently mocked).
    """
    # NOTE: Real NOAA API endpoint is complex; this mock provides stable data for testing.
    current_time = datetime.now().isoformat()
    return {
        "status": "MOCK_SUCCESS",
        "location": f"Lat: {lat}, Lon: {lon}",
        "time": current_time,
        "magnetic_field_strength_nT": 50000 + (datetime.now().second % 100) - 50, # Simulated flux
        "declination_deg": 5.0 + (datetime.now().second % 10) / 100.0,
        "notes": "Data mocked for stability. Use with caution for real analysis."
    }

@app.get("/api/artifact/lunar_surface_grid", summary="[Alien Artifacts] Mocked High-Res Lunar Surface Grid Metadata.")
async def get_lunar_surface_grid():
    """
    Returns simulated high-resolution lunar imagery metadata grid points for anomaly detection.
    Students can look for data gaps or unusual feature coordinates.
    """
    now = datetime.now()
    # Mock data structure: Grid points with acquisition time and quality score (proxy for sensor anomaly)
    mock_grid_data = []
    
    for i in range(1, 10):
        mock_grid_data.append({
            "grid_id": f"LROC-M{i:03}",
            "lat": round(-10.0 + i * 2.0 + (now.second % 10) / 10.0, 4),
            "lon": round(20.0 + i * 3.5 + (now.second % 10) / 10.0, 4),
            "acquisition_time": (now - timedelta(hours=i)).isoformat(),
            "sensor_quality_score": round(99.0 - i * 0.5 - (now.second % 3) * 0.1, 2) # Simulating slight drift
        })
        
    # Introduce a simulated anomaly (e.g., a significantly low quality score)
    mock_grid_data.append({
        "grid_id": "LROC-ANOMALY",
        "lat": -1.2345,
        "lon": 45.6789,
        "acquisition_time": (now - timedelta(hours=5.5)).isoformat(),
        "sensor_quality_score": 5.1 # Significantly low score
    })
    
    return {
        "status": "MOCK_SUCCESS",
        "timestamp": now.isoformat(),
        "surface_metadata": mock_grid_data,
        "notes": "Metadata for lunar grid cells; sensor_quality_score is the anomaly detection target."
    }


# --- CELESTIAL CARTOGRAPHER (Mapping, Location & Spatial Data) ---

@app.get("/api/cartographer/flyover_path", summary="[Cartographer] Mocked ISS Flyover Path (Guaranteed Data).")
async def get_mock_iss_path():
    """Returns a mocked path (list of lat/lon points) for a guaranteed ISS visualization near London."""
    
    # Mocked data simulating a useful ISS path over the UK/Europe.
    return {
        "status": "MOCK_SUCCESS",
        "location_name": "Standard UK Reference Point",
        "path_points": [
            {"lat": 55.0, "lon": -10.0, "note": "Entry"},
            {"lat": 51.5, "lon": 0.0, "note": "Over London"},
            {"lat": 48.0, "lon": 10.0, "note": "Over Europe"},
            {"lat": 45.0, "lon": 20.0, "note": "Exit"}
        ]
    }


@app.get("/api/cartographer/launch_sites", summary="[Cartographer] Simplified SpaceX Launch Sites.")
async def get_simplified_launch_sites():
    """Returns a filtered list of SpaceX launch sites containing only essential map data."""
    url = f"{SPACEX_BASE}/v4/launchpads"
    async with httpx.AsyncClient() as client:
        result = await fetch_api_data(url, client)
    
    if "error" in result or not isinstance(result, list):
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to retrieve Launchpad data.")

    # Filter and simplify the data to only essential fields for a map marker
    return [
        {
            "name": site.get('name'),
            "full_name": site.get('full_name'),
            "latitude": site.get('latitude'),
            "longitude": site.get('longitude')
        }
        for site in result
    ]


@app.get("/api/cartographer/geocode_location", summary="[Cartographer] Geocode Address to Coordinates.")
async def geocode_location(
    address: str = Query(..., description="Address or place name to geocode (e.g., Kennedy Space Center).")
):
    """Uses a stable geocoding service (OpenStreetMap) to convert a text address into coordinates."""
    url = f"{GEO_BASE}/search.php?q={address}&format=json&limit=1"
    headers = {"User-Agent": "GalacticArchivesHackathon/2.0"} # Required for OpenStreetMap Nominatim
    
    async with httpx.AsyncClient() as client:
        result = await fetch_api_data(url, client, headers=headers)
        
    if "error" in result or not isinstance(result, list) or not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Location not found or geocoding failed.")
        
    first_result = result[0]
    return {
        "display_name": first_result.get('display_name'),
        "latitude": first_result.get('lat'),
        "longitude": first_result.get('lon'),
        "bounding_box": first_result.get('boundingbox')
    }

@app.get("/api/cartographer/reverse_geocode", summary="[Cartographer] Convert Coordinates to Address/Place Name.")
async def reverse_geocode_location(
    lat: float = Query(..., description="Latitude (e.g., 28.56)"),
    lon: float = Query(..., description="Longitude (e.g., -80.58)")
):
    """Uses a stable geocoding service (OpenStreetMap) to convert coordinates back into an address."""
    url = f"{GEO_BASE}/reverse?lat={lat}&lon={lon}&format=json"
    headers = {"User-Agent": "GalacticArchivesHackathon/2.0"}
    
    async with httpx.AsyncClient() as client:
        result = await fetch_api_data(url, client, headers=headers)
        
    if "error" in result or result.get('error'):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Location data not found or API error.")

    return {
        "display_name": result.get('display_name'),
        "address_components": result.get('address')
    }


@app.get("/api/cartographer/exoplanet_targets", summary="[Cartographer] Simplified Exoplanet Data for Mapping/Charting.")
async def get_exoplanet_targets():
    """Returns a simplified dataset of recent exoplanet targets for celestial mapping and charting."""
    
    # NASA Exoplanet Archive query for confirmed planets with radius and period data
    # NOTE: This API returns CSV/TSV format by default, so we request JSON and limit the columns.
    QUERY = (
        "select+pl_name,ra,dec,pl_rade,pl_orbper,disc_year,sy_snum,sy_pnum+from+planets"
        "+where+pl_eqt>0+and+pl_controvflag=0+order+by+disc_year+desc+limit+10"
    )
    url = f"{EXOPLANET_BASE}?query={QUERY}&output=json"
    
    async with httpx.AsyncClient() as client:
        result = await fetch_api_data(url, client)
        
    if "error" in result:
        # Note: Exoplanet API sometimes returns 400 for bad query syntax, handle generic
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Exoplanet Fetch Error: {result['error']}")

    # The Exoplanet API returns an outer structure, often needing to access the 'data' key or similar,
    # but we assume the JSON structure here is list-based for simplicity.
    if not isinstance(result, list):
         raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Exoplanet API returned unexpected data format.")

    # Process and clean data
    cleaned_data = []
    for item in result:
        cleaned_data.append({
            "name": item.get('pl_name'),
            "ra_deg": item.get('ra'),
            "dec_deg": item.get('dec'),
            "radius_earth_units": item.get('pl_rade'),
            "orbital_period_days": item.get('pl_orbper'),
            "discovery_year": item.get('disc_year')
        })
        
    return cleaned_data


# --- AI TRACK ADVISOR (Predictive & Operational Status) ---

@app.get("/api/advisor/latest_news", response_model=List[NewsArticle], summary="[AI Advisor] Get Latest Space News Articles.")
async def get_latest_space_news():
    """Returns a simplified list of the most recent space and launch related news articles."""
    url = f"{NEWS_BASE}/articles?_limit=10&_sort=publishedAt&_contains=Space"
    async with httpx.AsyncClient() as client:
        result = await fetch_api_data(url, client)
        
    if "error" in result or not isinstance(result, list):
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to retrieve Space News data.")
        
    # Simplify the list to match the Pydantic schema
    simplified_articles = []
    for article in result:
        try:
            simplified_articles.append(NewsArticle(
                id=article['id'],
                title=article['title'],
                url=article['url'],
                summary=article['summary']
            ))
        except (KeyError, TypeError) as e:
            print(f"Skipping malformed news article: {e}")
            continue
            
    return simplified_articles


@app.get("/api/control/solar_storm_alert", summary="[Control] Simplified solar storm status (72h).")
async def get_solar_storm_alert():
    """Checks the last 72 hours for coronal mass ejection (CME) events."""
    end_date = datetime.now().date().isoformat()
    start_date = (datetime.now() - timedelta(days=3)).date().isoformat() # Last 72 hours
    
    # Using the existing NASA DONKI endpoint (as it's suitable and already in the system)
    url = f"{NASA_BASE}/DONKI/CMEAnalysis?startDate={start_date}&endDate={end_date}&api_key={NASA_API_KEY}"
    async with httpx.AsyncClient() as client:
        result = await fetch_api_data(url, client)
        
    if "error" in result:
        return {
            "status": "ERROR",
            "active_cme_alert": False,
            "reason": "Failed to fetch DONKI data."
        }
    
    # Check if the result is a non-empty list of events
    active_alert = isinstance(result, list) and len(result) > 0
    latest_event_date = result[0].get('time21_5', 'N/A') if active_alert else 'N/A'
    
    return {
        "status": "OK",
        "active_cme_alert": active_alert,
        "latest_event_utc": latest_event_date
    }


@app.get("/api/advisor/conjunction_risks", summary="[AI Advisor] Current Satellite Conjunction Risk Data (Space Debris).")
async def get_conjunction_risks():
    """
    Fetches mock data simulating recent conjunction events to provide a dataset 
    for predicting orbital collision risk (SSA).
    """
    # NOTE: Live SSA data is not available via simple public APIs, so we use a structured mock.
    # We simulate data that a predictive model would use.
    current_time = datetime.now().isoformat()
    
    return {
        "status": "MOCK_SUCCESS",
        "timestamp": current_time,
        "risk_events": [
            {
                "event_id": 1001,
                "asset_id_a": "STARLINK-4501",
                "asset_id_b": "DEBRIS-1998-067C",
                "risk_score_poc": 0.00015,
                "miss_distance_km": 1.25,
                "time_to_closest_approach_hours": 12.5
            },
            {
                "event_id": 1002,
                "asset_id_a": "ISS",
                "asset_id_b": "COSMOS-2251",
                "risk_score_poc": 0.000005,
                "miss_distance_km": 5.0,
                "time_to_closest_approach_hours": 72.1
            },
            {
                "event_id": 1003,
                "asset_id_a": "DEBRIS-2009-001X",
                "asset_id_b": "DEBRIS-2023-100A",
                "risk_score_poc": 0.005,
                "miss_distance_km": 0.5,
                "time_to_closest_approach_hours": 8.9
            }
        ],
        "notes": "Data mocked to provide a stable, high-value input for predictive risk modeling."
    }


@app.get("/api/advisor/starlink_status", summary="[AI Advisor] Current Starlink Satellite Catalog Status.")
async def get_starlink_status():
    """
    Fetches the full catalog of Starlink satellites, providing a dense dataset 
    of operational assets for simulation and predictive modeling of network integrity.
    """
    url = STARLINK_BASE
    
    async with httpx.AsyncClient() as client:
        # Note: This list can be large, we might need a longer timeout or client limits
        result = await fetch_api_data(url, client)
        
    if "error" in result:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Starlink Fetch Error: {result['error']}")

    # Process and simplify the large list
    operational_satellites = [
        {
            "id": sat.get('id'),
            "launch_date": sat.get('launch', 'N/A'),
            "mass_kg": sat.get('mass_kg'),
            "version": sat.get('version'),
            "velocity_km_s": sat.get('velocity_km_s'),
            "latitude": sat.get('latitude'),
            "longitude": sat.get('longitude'),
        }
        for sat in result if sat.get('spaceTrack', {}).get('DECAY') is None
    ]
        
    return {"status": "OK", "count": len(operational_satellites), "satellites": operational_satellites}


@app.get("/api/advisor/mission_telemetry", summary="[AI Advisor] Mock Time-Series Telemetry Data (Anomaly Prediction).")
async def get_mission_telemetry():
    """
    Returns mock time-series data (simulating sensor readings) for predictive failure modeling.
    """
    # Generate simple time-series data that shows a slight upward trend and noise
    now = datetime.now()
    telemetry_data = []
    
    for i in range(20): # 20 simulated data points
        timestamp = (now - timedelta(minutes=20 - i)).isoformat()
        base_temp = 50 + i * 0.1 # Slight upward drift
        noise = (now.second % 7) * 0.5
        
        telemetry_data.append({
            "timestamp": timestamp,
            "core_temp_c": round(base_temp + noise, 2),
            "battery_level_v": round(28.0 + (5 - (i % 5)) * 0.1, 2),
            "pressure_psi": round(350.0 + (i * 0.5) + noise, 2) 
        })
        
    return {
        "status": "MOCK_SUCCESS",
        "unit_id": "SAT-CORE-001",
        "telemetry": telemetry_data,
        "notes": "Multivariate time-series data for anomaly detection and prediction."
    }

# --- NEW: GPS CONSTELLATION HEALTH ---

@app.get("/api/advisor/gps_health_status", summary="[AI Advisor] Mock GPS Constellation Health Metrics.")
async def get_gps_health_status():
    """
    Returns mock metrics on GPS/GNSS satellite health (e.g., signal-to-noise ratio, drift) 
    for navigation prediction models.
    """
    now = datetime.now().isoformat()
    return {
        "status": "MOCK_SUCCESS",
        "system": "GNSS-PRIMARY",
        "time": now,
        "metrics": [
            {
                "satellite_id": "NAV-23",
                "signal_to_noise_ratio_db": 45.2 + (now.microsecond % 50) / 100.0,
                "orbital_drift_cm": 1.5 + (now.microsecond % 30) / 100.0,
                "power_output_w": 500.0 + (now.second % 10)
            },
            {
                "satellite_id": "NAV-12",
                "signal_to_noise_ratio_db": 38.5 + (now.microsecond % 60) / 100.0,
                "orbital_drift_cm": 2.1 + (now.microsecond % 40) / 100.0,
                "power_output_w": 490.0 + (now.second % 15)
            }
        ],
        "notes": "Multivariate data for predicting navigation integrity and system failure."
    }


# =========================================================================
# --- 10. LEGACY PASSTHROUGH ENDPOINTS (Kept for compatibility) ---
# =========================================================================

@app.get("/api/nasa/apod", summary="[Legacy] Get Astronomy Picture of the Day.")
async def get_apod_only():
    async with httpx.AsyncClient() as client:
        result = await fetch_api_data(EXTERNAL_APIS["nasa_apod"], client)
    if "error" in result: raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"APOD Error: {result['error']}")
    return result

@app.get("/api/spacex/company", summary="[Legacy] Get SpaceX company information.")
async def get_spacex_company():
    url = f"{SPACEX_BASE}/v4/company"
    async with httpx.AsyncClient() as client:
        result = await fetch_api_data(url, client)
    if "error" in result: raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"SpaceX Error: {result['error']}")
    return result

@app.get("/api/iss/iss-now", summary="[Legacy] Get the current ISS location.")
async def get_iss_now():
    url = f"{ISS_BASE}/iss-now.json"
    async with httpx.AsyncClient() as client:
        result = await fetch_api_data(url, client)
    if "error" in result: raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"ISS Error: {result['error']}")
    return result

@app.get("/api/nasa/neo/catalog", summary="[Legacy] Browse paginated NEO catalog.")
async def get_neo_browse_catalog_legacy(
    page: int = Query(0, description="Page number (0-based index).", ge=0),
    size: int = Query(50, description="Items per page (Max 50).", ge=1, le=50)
):
    """Uses the NEO Catalog to browse asteroids (limited data returned)."""
    url = f"{NASA_BASE}/neo/rest/v1/neo/browse?page={page}&size={size}&api_key={NASA_API_KEY}"
    async with httpx.AsyncClient() as client:
        result = await fetch_api_data(url, client)
    if "error" in result: raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"NEO Error: {result['error']}")
    return result

@app.get("/api/nasa/neo/feed", summary="[Legacy] Get raw NEO feed data for date range.")
async def get_neo_feed_legacy(
    start_date: date,
    end_date: date
):
    """Returns the raw NEO feed data (requires start/end dates)."""
    nasa_neo_url = (
        f"{NASA_BASE}/neo/rest/v1/feed?"
        f"start_date={start_date.isoformat()}&"
        f"end_date={end_date.isoformat()}&"
        f"api_key={NASA_API_KEY}"
    )
    async with httpx.AsyncClient() as client:
        result = await fetch_api_data(nasa_neo_url, client)
    if "error" in result: raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"NEO Feed Error: {result['error']}")
    return result
