# Use official Python lightweight image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Set the working directory
WORKDIR /app

# Install dependencies needed by numpy/trimesh occasionally, though usually slim is enough
# RUN apt-get update && apt-get install -y --no-install-recommends ...

# Copy the requirements file first to leverage Docker cache
COPY requirements-server.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements-server.txt

# Copy source code
COPY converter.py .
COPY main.py .

# Expose port (Cloud Run expects 8080 by default)
EXPOSE 8080

# Command to run the application using uvicorn
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
