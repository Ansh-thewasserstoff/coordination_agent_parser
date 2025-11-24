import uvicorn
import shutil
import os
import logging
import asyncio
import time
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import List, Optional
from enum import Enum
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# --- Import Ray ---
try:
    import ray
except ImportError:
    raise ImportError("Please install ray: pip install 'ray[default]'")

# --- Configuration ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ray_api")

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".xls", ".png", ".jpg", ".jpeg", ".tiff", ".bmp"}
NUM_WORKERS = 2
GPU_PER_WORKER = 0.5  # Allows 2 workers to share 1 GPU. Set to 1.0 for exclusive access.


# --- Models ---
class ProcessingStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"


class MarkdownResponse(BaseModel):
    filename: str
    content_type: str
    markdown: Optional[str] = None
    status: ProcessingStatus
    error_message: Optional[str] = None
    processing_time_seconds: float = 0.0


class HealthCheck(BaseModel):
    status: str
    ray_status: str
    available_resources: dict


# --- Ray Actor (The Worker) ---
# This replaces the complex 'process_file_in_worker' and global variable logic.
# Ray keeps this class alive in memory (stateful), so the model loads ONLY ONCE.

@ray.remote(num_gpus=GPU_PER_WORKER)
class DoclingWorker:
    def __init__(self):
        logger.info(f"Worker initializing on PID {os.getpid()}...")

        # Import inside class to isolate dependencies
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions

        pipeline_options = PdfPipelineOptions()
        pipeline_options.do_ocr = True
        pipeline_options.do_table_structure = True
        pipeline_options.table_structure_options.do_cell_matching = True

        self.converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
                InputFormat.IMAGE: PdfFormatOption(pipeline_options=pipeline_options)
            }
        )
        logger.info("Worker model loaded and ready.")

    def process(self, file_path: str) -> str:
        """
        Process the file and return markdown.
        """
        try:
            path_obj = Path(file_path)
            result = self.converter.convert(path_obj)
            return result.document.export_to_markdown()
        except Exception as e:
            # Ray propagates exceptions back to the main process automatically
            raise e


# --- FastAPI Lifecycle ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Start Ray
    # ignore_reinit_error handles cases where API hot-reloads
    ray.init(ignore_reinit_error=True)
    logger.info("Ray initialized.")

    # 2. Create the Worker Pool
    # We create a list of persistent actors.
    app.state.workers = [DoclingWorker.remote() for _ in range(NUM_WORKERS)]

    # Simple Round-Robin iterator to distribute load
    import itertools
    app.state.worker_cycle = itertools.cycle(app.state.workers)

    yield

    # 3. Shutdown
    ray.shutdown()
    logger.info("Ray shutdown.")


app = FastAPI(
    title="Ray-Powered Docling API",
    version="3.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Logic ---

def validate_file_extension(filename: str):
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        return False, ext
    return True, ext


async def process_with_ray(file: UploadFile, worker_actor) -> MarkdownResponse:
    start_time = time.time()
    temp_path = None
    filename = file.filename or "unknown"

    # 1. Validation
    valid, ext = validate_file_extension(filename)
    if not valid:
        return MarkdownResponse(
            filename=filename, content_type=file.content_type or "unknown",
            status=ProcessingStatus.FAILED, error_message=f"Type '{ext}' not supported"
        )

    try:
        # 2. Save to Disk (Ray needs a file path)
        suffix = Path(filename).suffix.lower()
        with NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            shutil.copyfileobj(file.file, temp_file)
            temp_path = temp_file.name
        file.file.close()

        # 3. Send to Ray Worker
        # .remote() sends the task. await returns the actual result.
        # This is non-blocking for the API server.
        markdown_content = await worker_actor.process.remote(temp_path)

        duration = time.time() - start_time
        return MarkdownResponse(
            filename=filename,
            content_type=file.content_type or "application/octet-stream",
            markdown=markdown_content,
            status=ProcessingStatus.SUCCESS,
            processing_time_seconds=round(duration, 2)
        )

    except Exception as e:
        logger.error(f"Error processing {filename}: {e}")
        return MarkdownResponse(
            filename=filename,
            content_type=file.content_type or "unknown",
            status=ProcessingStatus.FAILED,
            error_message=str(e),
            processing_time_seconds=round(time.time() - start_time, 2)
        )
    finally:
        # 4. Cleanup
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except Exception:
                pass


# --- Endpoints ---

@app.get("/", response_model=HealthCheck)
async def health_check():
    return HealthCheck(
        status="online",
        ray_status="connected" if ray.is_initialized() else "disconnected",
        available_resources=dict(ray.available_resources()) if ray.is_initialized() else {}
    )


@app.post("/convert", response_model=List[MarkdownResponse])
async def convert_documents(files: List[UploadFile] = File(...)):
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")

    # Get the worker iterator
    worker_cycle = app.state.worker_cycle

    tasks = []
    for file in files:
        # Assign next worker in line (Round Robin)
        worker = next(worker_cycle)
        tasks.append(process_with_ray(file, worker))

    # Process concurrently
    results = await asyncio.gather(*tasks)
    return results


if __name__ == "__main__":
    uvicorn.run("main_ray:app", host="0.0.0.0", port=8000, reload=False)