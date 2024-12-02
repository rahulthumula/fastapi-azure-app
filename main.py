from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Dict, Any
import logging
import uvicorn
from datetime import datetime
import tempfile
import os
from shared.invoice_processor import process_invoice_with_gpt
from shared.cosmos_operations import get_cosmos_manager

# Enhanced logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Invoice Processing API",
    description="API for processing and storing invoices",
    version="1.0.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Startup Event Handler
@app.on_event("startup")
async def startup_event():
    """
    Handles application startup events and verifies service connections
    """
    logger.info("Starting application initialization...")
    try:
        # Test Cosmos DB connection
        logger.info("Testing Cosmos DB connection...")
        cosmos_manager = await get_cosmos_manager()
        logger.info("Successfully connected to Cosmos DB")

        # Verify environment variables
        required_env_vars = [
            "COSMOS_ENDPOINT",
            "COSMOS_KEY",
            "COSMOS_DATABASE",
            "COSMOS_CONTAINER",
            "AZURE_FORM_RECOGNIZER_ENDPOINT",
            "AZURE_FORM_RECOGNIZER_KEY",
            "OPENAI_API_KEY"
        ]
        
        for var in required_env_vars:
            if not os.getenv(var):
                logger.warning(f"Environment variable {var} is not set")
                
        logger.info("Environment variables verification completed")
        
    except Exception as e:
        logger.error(f"Startup error: {str(e)}")
        # Log error but don't prevent startup
        pass

    logger.info("Application startup completed")

# Health Check Endpoint
@app.get("/health")
async def health_check():
    """Health check endpoint"""
    try:
        # Basic health check
        health_status = {
            "status": "healthy",
            "timestamp": datetime.utcnow().isoformat(),
            "version": "1.0.0"
        }

        # Optional: Add Cosmos DB connection check
        try:
            cosmos_manager = await get_cosmos_manager()
            health_status["cosmos_db"] = "connected"
        except Exception as e:
            health_status["cosmos_db"] = f"error: {str(e)}"

        return health_status
    except Exception as e:
        logger.error(f"Health check failed: {str(e)}")
        raise HTTPException(status_code=500, detail="Health check failed")

# Invoice Processing Endpoint
@app.post("/api/process-invoice/{user_id}")
async def process_invoice(user_id: str, files: List[UploadFile] = File(...)):
    """
    Process and store invoices for a given user
    """
    try:
        # Input validation
        if not user_id:
            raise HTTPException(status_code=400, detail="User ID is required")

        if not files:
            raise HTTPException(status_code=400, detail="No files were uploaded")

        # Process files
        all_invoices = []
        processed_files = 0
        failed_files = 0

        for file in files:
            try:
                logger.info(f"Processing file: {file.filename}")
                content = await file.read()
                
                with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                    temp_file.write(content)
                    temp_file_path = temp_file.name
                
                try:
                    # Process the temporary file
                    results = await process_invoice_with_gpt(temp_file_path)
                    if results:
                        all_invoices.extend(results)
                        processed_files += 1
                        logger.info(f"Successfully processed file: {file.filename}")
                    else:
                        failed_files += 1
                        logger.warning(f"No invoices found in file: {file.filename}")
                finally:
                    # Clean up the temporary file
                    os.unlink(temp_file_path)
                    
            except Exception as e:
                failed_files += 1
                logger.error(f"Error processing file {file.filename}: {str(e)}")
                continue

        if not all_invoices:
            return {
                "status": "completed",
                "message": "No invoices were found in the processed files",
                "invoice_count": 0,
                "processed_files": processed_files,
                "failed_files": failed_files
            }

        # Store invoices
        logger.info(f"Storing {len(all_invoices)} invoices for user {user_id}")
        cosmos_manager = await get_cosmos_manager()
        store_result = await cosmos_manager.store_invoices(user_id, all_invoices)

        return {
            "status": "completed",
            "message": f"Successfully processed {len(all_invoices)} invoices",
            "invoice_count": len(all_invoices),
            "processed_files": processed_files,
            "failed_files": failed_files,
            "store_result": store_result
        }

    except Exception as e:
        logger.error(f"Error processing invoice: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# Debug Endpoint (optional, can be disabled in production)
@app.post("/api/debug-invoice/{user_id}")
async def debug_invoice(user_id: str, files: List[UploadFile] = File(...)):
    """Debug endpoint to see raw processing results"""
    try:
        content = await files[0].read()
        with tempfile.NamedTemporaryFile(delete=False) as temp_file:
            temp_file.write(content)
            temp_file_path = temp_file.name
        try:
            results = await process_invoice_with_gpt(temp_file_path)
            return {"raw_results": results}
        finally:
            os.unlink(temp_file_path)
    except Exception as e:
        logger.error(f"Debug endpoint error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)