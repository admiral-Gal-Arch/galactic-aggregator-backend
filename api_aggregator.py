import asyncio
import httpx
import json
# import time # REMOVED: No longer needed for asynchronous sleep
from typing import Dict, Any, List
from datetime import datetime
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse # ADDED IMPORT
from pydantic import BaseModel
from contextlib import asynccontextmanager
from dotenv import load_dotenv
import os

# --- Load Environment Variables ---
load_dotenv()
# --- 1. CONFIGURATION ---
# Load key from .env file. Defaults to "30" if not found.
NASA_API_KEY = os.getenv("NASA_API_KEY", "DEMO_KEY")
REFRESH_INTERVAL_SECONDS = int(os.getenv("REFRESH_INTERVAL_SECONDS", "30"))

# In-memory store for aggregated data
IN_MEMORY_CACHE: Dict[str, Any] = {}
# Global flag to control the continuous refresh (now used for stopping the async loop)
STOP_EVENT = asyncio.Event() 

# --- 2. External API Definitions ---
# Dates for NASA NEOs endpoint
today = datetime.now().strftime("%Y-%m-%d")

EXTERNAL_APIS: Dict[str, str] = {
    "nasa_apod": f"https://api.nasa.gov/planetary/apod?api_key={NASA_API_KEY}",
    "spacex_latest": "https://api.spacexdata.com/v5/launches/latest",
    "iss_location": "http://api.open-notify.org/iss-now.json",
    "people_in_space": "http://api.open-notify.org/astros.json",
    "nasa_neo": f"https://api.nasa.gov/neo/rest/v1/feed?start_date={today}&end_date={today}&api_key={NASA_API_KEY}",
}

# --- 3. Data Schemas (Pydantic Models) ---

class AggregatedData(BaseModel):
    """Schema for the main cached and returned dashboard data."""
    apod_title: str
    spacex_flight_number: str
    iss_location: str
    people_in_space_count: str
    neo_count: str
    api_count: int
    last_updated: str # Added to show when data was last refreshed

class APODItem(BaseModel):
    """Schema for the individual exposed endpoints."""
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

# --- 4. CORE FETCHING & PROCESSING LOGIC ---

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
    """
    Asynchronous function that fetches data from all external APIs concurrently and updates the cache.
    """
    global IN_MEMORY_CACHE
    print(f"\n[CACHE REFRESH] Starting data fetch at {datetime.now().isoformat()}")

    api_urls = list(EXTERNAL_APIS.values())
    
    # Use a fresh client for the background task
    async with httpx.AsyncClient() as client:
        tasks = [fetch_api_data(url, client) for url in api_urls]
        results: List[Dict[str, Any]] = await asyncio.gather(*tasks)

    # --- Process and Structure the 5 Results ---
    apod_data, spacex_data, iss_data, astros_data, neo_data = results

    def get_safe_value(data: Dict[str, Any], keys: List[str], default_msg: str) -> str:
        if "error" in data:
            return data['error']
        try:
            current_data = data
            for key in keys:
                current_data = current_data.get(key, {})
            # If the final access is not a dictionary (it's a scalar value), convert to string
            return str(current_data) if current_data is not None else default_msg
        except Exception:
            return default_msg

    # 1. APOD Data (NASA)
    apod_title = get_safe_value(apod_data, ['title'], 'Error fetching APOD data.')

    # 2. SpaceX Latest Launch Data
    spacex_flight_number = get_safe_value(spacex_data, ['flight_number'], 'N/A')

    # 3. ISS Location Data (Open Notify)
    lat = get_safe_value(iss_data, ['iss_position', 'latitude'], 'N/A')
    lon = get_safe_value(iss_data, ['iss_position', 'longitude'], 'N/A')
    iss_location = f"Lat: {lat}, Lon: {lon}" if lat != 'N/A' else 'N/A'

    # 4. People in Space Count (Open Notify)
    count = get_safe_value(astros_data, ['number'], 'N/A')
    people_in_space_count = f"{count} people" if str(count).isdigit() else str(count)
        
    # 5. NEO Count (NASA)
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

# --- FIXED ASYNCHRONOUS CACHE LOOP ---

async def continuous_refresh_async_loop():
    """
    Asynchronous function that continuously refreshes the cache.
    Uses asyncio.sleep to correctly yield control and prevent blocking.
    """
    # Initial load of data immediately on startup
    await refresh_and_cache_data()

    while not STOP_EVENT.is_set():
        try:
            # Asynchronous sleep is the correct way to pause in an async loop
            await asyncio.sleep(REFRESH_INTERVAL_SECONDS)
            await refresh_and_cache_data()
        except asyncio.CancelledError:
            # Expected when the task is cancelled on shutdown
            break
        except Exception as e:
            print(f"[CACHE REFRESH] Unhandled error in async loop: {e}. Sleeping longer.")
            await asyncio.sleep(REFRESH_INTERVAL_SECONDS * 2)

# --- 5. FASTAPI LIFESPAN AND APP INIT ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Handles startup (initializing cache, starting background task) and shutdown.
    """
    # Start the continuous refresh loop as a background task (FIXED)
    app.state.refresh_task = asyncio.create_task(continuous_refresh_async_loop())
    print(f"[STARTUP] Asynchronous background refresh task started. Interval: {REFRESH_INTERVAL_SECONDS}s")
    
    yield # Application is ready to receive requests

    # On shutdown, gracefully stop the background task (FIXED)
    app.state.refresh_task.cancel()
    # Await the task with a suppression block for the expected CancelledError
    try:
        await app.state.refresh_task
    except asyncio.CancelledError:
        pass
    print("[SHUTDOWN] Background refresh task stopped.")


app = FastAPI(
    title="Space Data Aggregator Service",
    description="A custom FastAPI service that fetches and combines data from NASA, SpaceX, and Open-Notify APIs. Data is refreshed every 30 seconds using a synchronous time.sleep loop.",
    version="1.0.4", # Updated version number
    lifespan=lifespan # Attach the startup/shutdown logic
)

# Re-add CORS middleware after re-defining app
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- 6. FRONTEND SERVING LOGIC (FIXES 404 AND UNICODEDECODEERROR) ---

# Read the content of index.html once on startup
try:
    # Use encoding='utf-8' to fix the UnicodeDecodeError
    with open("index.html", "r", encoding='utf-8') as f: 
        INDEX_HTML_CONTENT = f.read()
except FileNotFoundError:
    INDEX_HTML_CONTENT = "<h1>Error: index.html not found! Ensure it is in the same directory as api_aggregator.py.</h1>"

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def get_dashboard_html():
    """Serves the main dashboard HTML page."""
    return INDEX_HTML_CONTENT

# --- 7. API ENDPOINTS ---

@app.get("/api/dashboard", response_model=AggregatedData, summary="Get cached, aggregated data from 5 space APIs.")
async def get_aggregated_dashboard_data():
    """
    Returns the latest aggregated data from the in-memory cache. 
    This is fast as it does not wait for external API calls.
    """
    if not IN_MEMORY_CACHE:
        # If the cache is empty on first load, wait briefly or return error
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service cache is not yet initialized. Please wait a moment and retry."
        )
    return AggregatedData(**IN_MEMORY_CACHE)


# --- Individual Endpoints for Documentation (Live Fetch) ---

@app.get("/api/apod", response_model=APODItem, summary="Get NASA's Astronomy Picture of the Day data (Live Fetch).")
async def get_apod_only():
    """Returns the raw response from the NASA APOD API."""
    async with httpx.AsyncClient() as client:
        result = await fetch_api_data(EXTERNAL_APIS["nasa_apod"], client)
    
    if "error" in result:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"APOD Fetch Error: {result['error']}")

    return APODItem(
        title=result.get('title', 'N/A'),
        date=result.get('date', 'N/A'),
        url=result.get('url', None),
        explanation=result.get('explanation', 'No explanation provided.')
    )

@app.get("/api/spacex/latest", response_model=SpaceXLaunch, summary="Get details of the latest SpaceX launch (Live Fetch).")
async def get_spacex_only():
    """Returns the raw flight number, name, and date of the latest SpaceX launch."""
    async with httpx.AsyncClient() as client:
        result = await fetch_api_data(EXTERNAL_APIS["spacex_latest"], client)
    
    if "error" in result:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"SpaceX Fetch Error: {result['error']}")

    return SpaceXLaunch(
        flight_number=result.get('flight_number', 0),
        name=result.get('name', 'Unknown Mission'),
        date_utc=result.get('date_utc', 'N/A')
    )
    
@app.get("/api/iss/astros", response_model=PeopleInSpace, summary="Get the current number of people in space (Live Fetch).")
async def get_astros_only():
    """Returns the list of astronauts currently in space from the Open-Notify API."""
    async with httpx.AsyncClient() as client:
        result = await fetch_api_data(EXTERNAL_APIS["people_in_space"], client)
    
    if "error" in result:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Astros Fetch Error: {result['error']}")

    return PeopleInSpace(**result)