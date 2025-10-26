# galacticarchives/Dockerfile

# Base image for Python 3.12 (or the version you use)
FROM python:3.12-slim

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your application code
# IMPORTANT: Do NOT copy .env or api_aggregator.py to the GitHub Pages repo.
# ONLY copy them to the repo used for THIS deployment.
COPY api_aggregator.py .
COPY .env .

# Expose the port Uvicorn will listen on (default for Cloud Run is 8080)
EXPOSE 8080

# Command to run the application using Uvicorn
# We ignore UVICORN_PORT=8001 from .env and listen on 0.0.0.0:8080 
# as required by Cloud Run.
CMD ["uvicorn", "api_aggregator:app", "--host", "0.0.0.0", "--port", "8080"]