# Document to Markdown API

A high-performance API server that converts various document formats (PDF, DOCX, images, etc.) into Markdown. This project uses the powerful `docling` library for document analysis and the `Ray` framework to manage a pool of parallel worker processes for efficient, scalable conversions.

## Features

-   **Fast & Asynchronous:** Built with FastAPI for high-throughput, non-blocking I/O.
-   **Parallel Processing:** Utilizes the Ray framework to distribute conversion tasks across multiple CPU cores, allowing for the simultaneous processing of multiple documents.
-   **Multi-Format Support:** Handles a wide range of file types, including `.pdf`, `.docx`, `.xlsx`, `.png`, `.jpg`, and more, powered by `docling`.
-   **Scalable Architecture:** The use of Ray actors as workers makes the system scalable and resilient.
-   **Simple REST Interface:** Easy to integrate with any client application via a standard REST API.

---

## Architecture Overview

This service is composed of three main components:

1.  **FastAPI Web Server:** Provides the main application entrypoint and the REST API interface. It handles incoming HTTP requests.
2.  **Ray Framework:** Manages a pool of background worker processes (Ray Actors). When a conversion request is received, FastAPI dispatches the task to Ray, which then distributes the work to an available worker. This prevents long-running conversion tasks from blocking the main server.
3.  **Docling Worker (`DoclingWorker`):** Each worker is a dedicated process that contains an instance of the `docling.DocumentConverter`. It receives a file path from the Ray scheduler, performs the intensive document analysis and conversion, and returns the resulting Markdown content.

---

## Setup and Installation

### Prerequisites

-   Python 3.12+
-   An NVIDIA GPU with CUDA installed (for GPU-accelerated model inference).
-   We recommend using `uv` for fast environment and package management.

### Installation Steps

1.  **Clone the repository:**
    ```bash
    git clone <your-repository-url>
    cd <your-project-directory>
    ```

2.  **Create and activate a virtual environment:**
    ```bash
    # Using uv (recommended)
    uv venv
    source .venv/bin/activate

    # Or using standard venv
    python -m venv .venv
    source .venv/bin/activate
    ```

3.  **Install dependencies:**
    The project dependencies are listed in `pyproject.toml`. You can install them with `uv` or `pip`.
    ```bash
    # Using uv
    uv pip install -e .

    # Or using pip
    pip install -e .
    ```

---

## Running the Application

To run the development server, use the `fastapi dev` command. It's crucial to bind the server to `0.0.0.0` to make it accessible from outside the immediate container or WSL environment.

```bash
fastapi dev parser_api.py --host 0.0.0.0
```

The API will be available at `http://localhost:8000`.
Interactive API documentation (via Swagger UI) will be at `http://localhost:8000/docs`.

---

## API Documentation

### `POST /convert`

Uploads one or more documents to be converted to Markdown.

-   **Request `multipart/form-data`:**
    -   `files`: The file(s) to be converted. You can specify this field multiple times for multiple files.

-   **Response `application/json`:**
    A JSON array where each object represents the result for a single file.
    ```json
    [
      {
        "filename": "my_document.pdf",
        "content_type": "application/pdf",
        "markdown": "# My Document...",
        "status": "success",
        "error_message": null,
        "processing_time_seconds": 25.5
      }
    ]
    ```

#### Example using `curl`

```bash
curl -X POST "http://localhost:8000/convert" \
  -F "files=@/path/to/your/document.pdf" \
  -F "files=@/path/to/your/image.png"
```

---

## Configuration

-   **Number of Workers:** The number of parallel worker processes is determined by your Ray implementation. This is typically configured via the number of Ray Actors you initialize (e.g., `num_cpus` in the `@ray.remote` decorator or the number of replicas in an actor pool).
    -   **Important:** Each worker consumes a significant amount of RAM (~2.5-3GB). Ensure your machine or WSL environment has enough memory for the number of workers you configure.

---

## Troubleshooting

-   **Ray OOM (Out of Memory) Error in WSL:**
    -   **Error:** You may see `Task was killed due to the node running low on memory`.
    -   **Reason:** This error refers to **system RAM** inside your WSL environment, not your total Windows RAM. Each `DoclingWorker` is memory-intensive.
    -   **Solution:** Increase the memory allocated to WSL. Create a `.wslconfig` file in your Windows user profile (`C:\Users\<YourUser>\.wslconfig`) and set a higher limit. Then, restart WSL with `wsl --shutdown`.
        ```ini
        [wsl2]
        memory=12GB  # Or more, depending on your system
        ```

-   **Long Delay on First Request:**
    -   **Symptom:** The first request after starting the server takes a very long time (1-2 minutes) before processing begins.
    -   **Reason:** This is the one-time, unavoidable cost of starting each Ray worker process and initializing the `docling` model and CUDA context on the GPU. Subsequent requests will be much faster as the workers are already running.

-   **`Failed to fetch` in `/docs` UI:**
    -   **Symptom:** The "Execute" button in the docs UI returns a "Failed to fetch" error.
    -   **Reason:** This usually happens if the server is not accessible from your browser.
    -   **Solution:** Ensure you are running the server with `--host 0.0.0.0` as specified in the run command.
