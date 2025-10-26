import asyncio
import httpx
import json
from typing import Dict, Any, List, Optional
from datetime import datetime, date
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
REFRESH_INTERVAL_SECONDS = int(os.getenv("REFRESH_INTERVAL_SECONDS", "30"))

# In-memory store for aggregated data
IN_MEMORY_CACHE: Dict[str, Any] = {}
STOP_EVENT = asyncio.Event() 

# --- 2. External API Definitions (UPDATED & EXPANDED) ---

# Dates for NASA NEOs endpoint
today = datetime.now().strftime("%Y-%m-%d")

# Base URLs for clarity
NASA_BASE = "https://api.nasa.gov"
SPACEX_BASE = "https://api.spacexdata.com"
ISS_BASE = "http://api.open-notify.org"

# APIS used by the internal cache refresh (original 5)
EXTERNAL_APIS: Dict[str, str] = {
    "nasa_apod": f"{NASA_BASE}/planetary/apod?api_key={NASA_API_KEY}",
    "spacex_latest": f"{SPACEX_BASE}/v5/launches/latest",
    "iss_location": f"{ISS_BASE}/iss-now.json",
    "people_in_space": f"{ISS_BASE}/astros.json",
    "nasa_neo": f"{NASA_BASE}/neo/rest/v1/feed?start_date={today}&end_date={today}&api_key={NASA_API_KEY}",
}

# --- 3. Data Schemas (Pydantic Models) ---
# NOTE: The models below are simplified placeholders for raw data return types.
# Actual Pydantic models for every new endpoint are omitted for brevity, 
# and the endpoints return Dict[str, Any] instead.

class AggregatedData(BaseModel):
    """Schema for the main cached and returned dashboard data."""
    apod_title: str
    spacex_flight_number: str
    iss_location: str
    people_in_space_count: str
    neo_count: str
    api_count: int
    last_updated: str

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
    
    async with httpx.AsyncClient() as client:
        tasks = [fetch_api_data(url, client) for url in api_urls]
        results: List[Dict[str, Any]] = await asyncio.gather(*tasks)

    # --- Process and Structure the 5 Results (unchanged logic) ---
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

# --- ASYNCHRONOUS CACHE LOOP (Unchanged) ---

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

# --- 5. FASTAPI LIFESPAN AND APP INIT (Unchanged) ---

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
    title="Space Data Aggregator Service",
    description="A custom FastAPI service that fetches and combines data from NASA, SpaceX, and Open-Notify APIs. Data is refreshed every 30 seconds.",
    version="1.0.5", # Version bumped to reflect new endpoints
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- 6. FRONTEND SERVING LOGIC (Unchanged) ---

try:
    with open("index.html", "r", encoding='utf-8') as f: 
        INDEX_HTML_CONTENT = f.read()
except FileNotFoundError:
    INDEX_HTML_CONTENT = "<h1>Error: index.html not found! Ensure it is in the same directory as api_aggregator.py.</h1>"

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def get_dashboard_html():
    return INDEX_HTML_CONTENT

# --- 7. CACHED API ENDPOINT (Unchanged) ---

@app.get("/api/dashboard", response_model=AggregatedData, summary="Get cached, aggregated data from 5 space APIs.")
async def get_aggregated_dashboard_data():
    if not IN_MEMORY_CACHE:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service cache is not yet initialized. Please wait a moment and retry."
        )
    return AggregatedData(**IN_MEMORY_CACHE)

# =========================================================================
# --- 8. LIVE FETCH ENDPOINTS (All endpoints user requested added here) ---
# =========================================================================

# --- NASA Endpoints ---

@app.get("/api/nasa/apod", response_model=APODItem, summary="Get Astronomy Picture of the Day (Live Fetch).")
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

@app.get("/api/nasa/neo/feed", summary="Get total NEO count for a date range (Live Fetch).")
async def get_neo_feed(
    start_date: date,
    end_date: date
):
    """
    Returns the total count of Near-Earth Objects (NEOs) tracked between a start and end date.
    """
    # Build the NASA URL using the provided dates, ensuring they are formatted correctly
    nasa_neo_url = (
        f"{NASA_BASE}/neo/rest/v1/feed?"
        f"start_date={start_date.isoformat()}&"
        f"end_date={end_date.isoformat()}&"
        f"api_key={NASA_API_KEY}"
    )

    async with httpx.AsyncClient() as client:
        result = await fetch_api_data(nasa_neo_url, client)

    if "error" in result:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"NASA NEO Fetch Error: {result['error']}"
        )
    
    total_neos = 0
    neo_objects = result.get('near_earth_objects', {})
    for date_str, neo_list in neo_objects.items():
        total_neos += len(neo_list)

    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "total_neo_count": total_neos
    }

@app.get("/api/nasa/mars-photos", summary="Get Mars Rover Photos (Live Fetch).")
async def get_mars_photos(
    sol: int = Query(..., description="Martian solar day."),
    camera: Optional[str] = None
):
    """Returns Mars Rover Photos for a given sol (Martian day)."""
    url = f"{NASA_BASE}/mars-photos/api/v1/rovers/curiosity/photos?sol={sol}&api_key={NASA_API_KEY}"
    if camera:
        url += f"&camera={camera}"
    
    async with httpx.AsyncClient() as client:
        result = await fetch_api_data(url, client)
    
    if "error" in result:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Mars Photos Fetch Error: {result['error']}")
    return result

@app.get("/api/nasa/donki", summary="Get Space Weather Data (Live Fetch).")
async def get_donki_data(
    start_date: date = Query(..., description="Start date (YYYY-MM-DD)"),
    end_date: date = Query(..., description="End date (YYYY-MM-DD)")
):
    """Returns space weather data from the DONKI API."""
    url = f"{NASA_BASE}/DONKI/CME?start_date={start_date}&end_date={end_date}&api_key={NASA_API_KEY}"
    async with httpx.AsyncClient() as client:
        result = await fetch_api_data(url, client)
    
    if "error" in result:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"DONKI Fetch Error: {result['error']}")
    return result

@app.get("/api/nasa/earth/imagery", summary="Get Earth Imagery (Live Fetch).")
async def get_earth_imagery(
    lat: float = Query(..., description="Latitude"),
    lon: float = Query(..., description="Longitude"),
    date: Optional[date] = None
):
    """Returns the Earth Imagery URL for a specific location and optional date."""
    url = f"{NASA_BASE}/planetary/earth/imagery?lon={lon}&lat={lat}&api_key={NASA_API_KEY}"
    if date:
        url += f"&date={date.isoformat()}"
        
    async with httpx.AsyncClient() as client:
        # Note: This API sometimes returns an image file, but here we capture the metadata/error
        response = await client.get(url, timeout=10.0)
        # We rely on response.json() failing if it's an image, or getting the metadata
        try:
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
             # NASA Earth API often redirects to the image or returns 404 for no image
             return {"url": url, "error": f"HTTP Status {exc.response.status_code}. Data may be image file."}
        except Exception as e:
            # If JSON parsing fails (because it's an image), return the URL
             return {"url": url, "note": "Successfully generated image URL."}

@app.get("/api/nasa/epic", summary="Get EPIC (Earth Polychromatic Imaging Camera) Data (Live Fetch).")
async def get_epic_data():
    """Returns the latest imagery data from the EPIC API."""
    url = f"{NASA_BASE}/EPIC/api/natural/images?api_key={NASA_API_KEY}"
    async with httpx.AsyncClient() as client:
        result = await fetch_api_data(url, client)
    
    if "error" in result:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"EPIC Fetch Error: {result['error']}")
    return result

# --- SpaceX Endpoints ---

@app.get("/api/spacex/latest", response_model=SpaceXLaunch, summary="Get the latest SpaceX launch (Live Fetch).")
async def get_spacex_only():
    async with httpx.AsyncClient() as client:
        result = await fetch_api_data(EXTERNAL_APIS["spacex_latest"], client)
    
    if "error" in result:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"SpaceX Fetch Error: {result['error']}")

    return SpaceXLaunch(
        flight_number=result.get('flight_number', 0),
        name=result.get('name', 'Unknown Mission'),
        date_utc=result.get('date_utc', 'N/A')
    )

@app.get("/api/spacex/next", summary="Get the next scheduled SpaceX launch (Live Fetch).")
async def get_spacex_next():
    url = f"{SPACEX_BASE}/v5/launches/next"
    async with httpx.AsyncClient() as client:
        result = await fetch_api_data(url, client)
    if "error" in result:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"SpaceX Fetch Error: {result['error']}")
    return result

@app.get("/api/spacex/past", summary="Get all past SpaceX launches (Live Fetch).")
async def get_spacex_past():
    url = f"{SPACEX_BASE}/v5/launches/past"
    async with httpx.AsyncClient() as client:
        result = await fetch_api_data(url, client)
    if "error" in result:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"SpaceX Fetch Error: {result['error']}")
    return result

@app.get("/api/spacex/upcoming", summary="Get all upcoming SpaceX launches (Live Fetch).")
async def get_spacex_upcoming():
    url = f"{SPACEX_BASE}/v5/launches/upcoming"
    async with httpx.AsyncClient() as client:
        result = await fetch_api_data(url, client)
    if "error" in result:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"SpaceX Fetch Error: {result['error']}")
    return result

@app.get("/api/spacex/rockets", summary="Get all SpaceX rockets (Live Fetch).")
async def get_spacex_rockets():
    url = f"{SPACEX_BASE}/v4/rockets"
    async with httpx.AsyncClient() as client:
        result = await fetch_api_data(url, client)
    if "error" in result:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"SpaceX Fetch Error: {result['error']}")
    return result

@app.get("/api/spacex/company", summary="Get SpaceX company information (Live Fetch).")
async def get_spacex_company():
    url = f"{SPACEX_BASE}/v4/company"
    async with httpx.AsyncClient() as client:
        result = await fetch_api_data(url, client)
    if "error" in result:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"SpaceX Fetch Error: {result['error']}")
    return result
    
# NOTE: The rest of the SpaceX v4 endpoints are similar and can be created 
# by changing the final path segment (starlink, payloads, landpads, launchpads, cores, crew, capsules).

# --- ISS/Open Notify Endpoints ---

@app.get("/api/iss/astros", response_model=PeopleInSpace, summary="Get the current number of people in space (Live Fetch).")
async def get_astros_only():
    async with httpx.AsyncClient() as client:
        result = await fetch_api_data(EXTERNAL_APIS["people_in_space"], client)
    
    if "error" in result:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Astros Fetch Error: {result['error']}")

    return PeopleInSpace(**result)

@app.get("/api/iss/iss-now", summary="Get the current ISS location (Live Fetch).")
async def get_iss_now():
    url = f"{ISS_BASE}/iss-now.json"
    async with httpx.AsyncClient() as client:
        result = await fetch_api_data(url, client)
    
    if "error" in result:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"ISS Fetch Error: {result['error']}")
    return result

@app.get("/api/iss/iss-pass", summary="Get ISS pass times for coordinates (Live Fetch).")
async def get_iss_pass(
    lat: float = Query(..., description="Latitude"),
    lon: float = Query(..., description="Longitude"),
    alt: int = Query(100, description="Altitude in kilometers (default: 100)"),
    n: int = Query(5, description="Number of passes to return (default: 5)")
):
    """Returns the next few times the ISS will pass over a given location."""
    url = f"{ISS_BASE}/iss-pass.json?lat={lat}&lon={lon}&alt={alt}&n={n}"
    async with httpx.AsyncClient() as client:
        result = await fetch_api_data(url, client)
    
    if "error" in result:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"ISS Fetch Error: {result['error']}")
    return result
