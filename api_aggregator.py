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
# NOTE: N2YO_API_KEY is not strictly used in this version but is included 
# for future Mission Planning expansion.
N2YO_API_KEY = os.getenv("N2YO_API_KEY", "YOUR_N2YO_KEY")
REFRESH_INTERVAL_SECONDS = int(os.getenv("REFRESH_INTERVAL_SECONDS", "30"))

# In-memory store for aggregated data
IN_MEMORY_CACHE: Dict[str, Any] = {}
STOP_EVENT = asyncio.Event() 

# Base URLs for clarity
NASA_BASE = "https://api.nasa.gov"
SPACEX_BASE = "https://api.spacexdata.com"
ISS_BASE = "http://api.open-notify.org"

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

# --- 3. CORE FETCHING & PROCESSING LOGIC ---

async def fetch_api_data(url: str, client: httpx.AsyncClient) -> Dict[str, Any]:
    """Handles an individual API call with error handling."""
    try:
        response = await client.get(url, timeout=10.0)
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
        close_approach = neo.get('close_approach_data', [{}])[0]
        
        # Safely extract diameter
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


# --- ALIEN ARTIFACT (Data Analysis & Classification) ---

@app.get("/api/artifact/neo_clusters", summary="[Alien Artifact] Paginated NEO Data for ML Clustering.")
async def get_neo_clusters(
    page: int = Query(0, description="Page number (0-based index).", ge=0),
    size: int = Query(50, description="Items per page (Max 50).", ge=1, le=50)
):
    """Returns a highly simplified, paginated dataset of NEOs ready for clustering/classification."""
    async with httpx.AsyncClient() as client:
        return await fetch_and_process_catalog_data(page, size, client)


@app.get("/api/artifact/image_coordinates", summary="[Alien Artifact] Get latest EPIC imagery metadata coordinates.")
async def get_epic_coordinates():
    """Returns a list of image metadata (date, lat/lon) for the latest EPIC capture date."""
    url = f"{NASA_BASE}/EPIC/api/natural/images?api_key={NASA_API_KEY}"
    async with httpx.AsyncClient() as client:
        result = await fetch_api_data(url, client)
        
    if "error" in result or not isinstance(result, list):
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to retrieve EPIC data.")
        
    # Return a clean list of coordinate/timestamp pairs
    return [{
        "date_time": img.get('date'),
        "coords": img.get('centroid_coordinates')
    } for img in result]


# --- CARTOGRAPHER (Visualization & Mapping) ---

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


# --- PREDICTOR & MISSION CONTROL ---

@app.get("/api/control/next_launch_countdown", summary="[Control] Minimal payload for countdown clock.")
async def get_next_launch_countdown():
    """Returns only the critical data points required for a front-end countdown timer."""
    url = f"{SPACEX_BASE}/v5/launches/next"
    async with httpx.AsyncClient() as client:
        result = await fetch_api_data(url, client)
    
    if "error" in result:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="SpaceX Fetch Error.")
    
    return {
        "mission_name": result.get('name', 'Mission TBD'),
        "launch_timestamp_utc": result.get('date_utc'),
        "is_tbd": result.get('tbd', True)
    }

@app.get("/api/control/solar_storm_alert", summary="[Control] Simplified solar storm status (72h).")
async def get_solar_storm_alert():
    """Checks the last 72 hours for coronal mass ejection (CME) events."""
    end_date = datetime.now().date().isoformat()
    start_date = (datetime.now() - timedelta(days=3)).date().isoformat() # Last 72 hours
    
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

# =========================================================================
# --- 8. ORIGINAL PASSTHROUGH ENDPOINTS (For compatibility) ---
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
