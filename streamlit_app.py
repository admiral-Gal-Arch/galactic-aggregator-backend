import streamlit as st
from typing import Dict, List, Any

# --- Configuration ---
API_BASE_URL = "https://api.galacticarchives.space"

# --- Data Structure representing Endpoints and Tracks (Extracted from api_aggitator.py) ---

API_ENDPOINTS: Dict[str, Dict[str, Any]] = {
    "Core Metrics": {
        "tag": "Core Metrics",
        "endpoints": [
            {"path": "/api/dashboard", "summary": "Get cached, aggregated metrics for the dashboard.", "params": []},
        ]
    },
    "Alien Artifacts Division": {
        "tag": "Alien Artifacts Division",
        "endpoints": [
            {"path": "/api/artifact/neo_clusters", "summary": "Paginated NEO Data for ML Clustering (High-D Data).", "params": [("page", "int"), ("size", "int")]},
            {"path": "/api/artifact/geomagnetic_data", "summary": "Latest Geomagnetic Field Data (Anomaly Search).", "params": [("lat", "float"), ("lon", "float")]},
            {"path": "/api/artifact/lunar_surface_grid", "summary": "Mocked High-Res Lunar Surface Grid Metadata.", "params": []},
        ]
    },
    "Celestial Cartographer": {
        "tag": "Celestial Cartographer",
        "endpoints": [
            {"path": "/api/cartographer/flyover_path", "summary": "Mocked ISS Flyover Path (Guaranteed Trajectory).", "params": []},
            {"path": "/api/cartographer/launch_sites", "summary": "Simplified SpaceX Launch Sites (Map Markers).", "params": []},
            {"path": "/api/cartographer/geocode_location", "summary": "Geocode Address to Coordinates (OSM).", "params": [("address", "str")]},
            {"path": "/api/cartographer/reverse_geocode", "summary": "Convert Coordinates to Address/Place Name (OSM).", "params": [("lat", "float"), ("lon", "float")]},
            {"path": "/api/cartographer/exoplanet_targets", "summary": "Simplified Exoplanet Data for Mapping/Charting.", "params": []},
        ]
    },
    "AI Track Advisor": {
        "tag": "AI Track Advisor",
        "endpoints": [
            {"path": "/api/advisor/latest_news", "summary": "Get Latest Space News Articles.", "params": []},
            {"path": "/api/control/solar_storm_alert", "summary": "Simplified solar storm status (72h).", "params": []},
            {"path": "/api/advisor/conjunction_risks", "summary": "Current Satellite Conjunction Risk Data (Debris).", "params": []},
            {"path": "/api/advisor/starlink_status", "summary": "Current Starlink Satellite Catalog Status.", "params": []},
            {"path": "/api/advisor/mission_telemetry", "summary": "Mock Time-Series Telemetry Data (Anomaly Prediction).", "params": []},
            {"path": "/api/advisor/gps_health_status", "summary": "Mock GPS Constellation Health Metrics.", "params": []},
        ]
    }
}

# --- Utility Functions ---

def generate_code_example(endpoint: Dict[str, Any]):
    """Generates a Python requests code block for an endpoint."""
    path = endpoint['path']
    params = endpoint['params']
    
    code = f"API_URL = \"{API_BASE_URL}\"\n"
    code += f"ENDPOINT = \"{path}\"\n\n"
    
    if params:
        param_list = [f'"{p[0]}": {p[1].split("(")[0]}(input("{p[0]} ({p[1]}): "))' for p in params]
        code += "params = {\n" + ",\n".join([f"    {p}" for p in param_list]) + "\n}\n\n"
        code += "try:\n"
        code += "    response = requests.get(API_URL + ENDPOINT, params=params)\n"
    else:
        code += "try:\n"
        code += "    response = requests.get(API_URL + ENDPOINT)\n"
    
    code += "    response.raise_for_status()\n"
    code += "    print(response.json())\n"
    code += "except requests.exceptions.RequestException as e:\n"
    code += "    print(f\"API Call Failed: {e}\")\n"
    
    return code


def render_full_documentation():
    """Renders the comprehensive documentation page."""
    st.header("Full API Documentation & Explorer")
    st.markdown(f"The Galactic Archives API provides specialized, pre-processed data to accelerate development in the hackathon tracks. **Base URL:** `{API_BASE_URL}`")
    st.markdown("---")

    import requests # Ensure requests is imported inside the function for code block generation

    for track, data in API_ENDPOINTS.items():
        st.subheader(f"✨ {track}")
        
        for endpoint in data["endpoints"]:
            with st.expander(f"`{endpoint['path']}`: {endpoint['summary']}"):
                
                # Description
                st.markdown(f"**Description:** {endpoint['summary']}")
                
                # Parameters table
                if endpoint['params']:
                    st.markdown("**Parameters:**")
                    param_table = pd.DataFrame(
                        endpoint['params'], 
                        columns=['Name', 'Type']
                    )
                    param_table['Type'] = param_table['Type'].apply(lambda x: x.replace('(', ' (').replace(')', ')').title())
                    st.table(param_table)
                else:
                    st.info("No parameters required for this endpoint.")
                
                # Code Example
                st.markdown("**Python Example (requests):**")
                st.code(generate_code_example(endpoint), language='python')
                
                st.markdown("---")


def render_home_page():
    """Renders the track overview page."""
    st.header("Welcome to the Galactic Archives Hackathon API Portal")
    st.markdown(f"This specialized API provides streamlined data access tailored to the three core hackathon tracks. All endpoints are hosted at: `{API_BASE_URL}`")
    st.markdown("---")

    for track, data in API_ENDPOINTS.items():
        if track == "Core Metrics": continue # Skip core metrics on home
        
        st.subheader(f"🚀 {track}")
        st.markdown(f"**Goal:** Leverage pre-processed data for high-impact projects in this track. Below are your dedicated endpoints:")
        
        cols = st.columns(3)
        for i, endpoint in enumerate(data["endpoints"]):
            col = cols[i % 3]
            with col:
                st.markdown(f"""
                <div style="background-color: #f0f2f6; padding: 10px; margin-bottom: 10px; border-radius: 8px; border-left: 5px solid #1f77b4;">
                    <p style="font-weight: bold; margin-bottom: 5px;">{endpoint['summary']}</p>
                    <code style="background-color: #e0e2e7; padding: 3px 6px; border-radius: 4px;">GET {endpoint['path']}</code>
                </div>
                """, unsafe_allow_html=True)
        st.markdown("---")

# --- Streamlit Page Control ---

page_names_to_funcs = {
    "Hackathon Tracks Overview": render_home_page,
    "Full API Documentation": render_full_documentation,
}

st.sidebar.title("Navigation")
selected_page = st.sidebar.radio("Go to", page_names_to_funcs.keys())

# Run the selected page function
page_names_to_funcs[selected_page]()

# Optional: Add a brief core status check for context on all pages
st.sidebar.markdown("---")
st.sidebar.subheader("Core Status Check")
core_endpoint = API_ENDPOINTS["Core Metrics"]["endpoints"][0]
st.sidebar.markdown(f"Core: `{core_endpoint['path']}`")
st.sidebar.markdown("Status: **CACHED & READY**")
