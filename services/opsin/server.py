"""Standalone OPSIN JVM service. The main API/Celery processes never start a JVM."""

from __future__ import annotations

import asyncio
import hashlib
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse


def load_parser():
    import jpype

    jar = Path(os.environ["OPSIN_JAR"])
    expected = os.environ.get("OPSIN_JAR_SHA256")
    if expected and hashlib.sha256(jar.read_bytes()).hexdigest() != expected:
        raise RuntimeError("OPSIN JAR SHA256 mismatch")
    if not jpype.isJVMStarted():
        jpype.startJVM("-Xmx512m", classpath=[str(jar)], convertStrings=True)
    return jpype.JClass("uk.ac.cam.ch.wwmm.opsin.NameToStructure").getInstance()


def parse_name(parser, name):
    result = parser.parseChemicalName(name)
    return {
        "status": str(result.getStatus()),
        "smiles": result.getSmiles(),
        "message": result.getMessage(),
        "warnings": [str(w.getType()) + ": " + str(w.getMessage()) for w in result.getWarnings()],
    }


@asynccontextmanager
async def lifespan(app):
    app.state.parser = await asyncio.to_thread(load_parser)
    app.state.capacity = asyncio.Semaphore(2)
    yield


app = FastAPI(title="drugpaper OPSIN", lifespan=lifespan)


@app.get("/healthz")
async def health():
    return {"status": "ok", "opsin_version": os.environ.get("OPSIN_VERSION", "2.8.0")}


@app.get("/{chemical_name:path}.json")
async def parse(chemical_name: str):
    if not chemical_name.strip() or len(chemical_name) > 4000 or "\n" in chemical_name:
        raise HTTPException(422, "invalid chemical name")
    async with app.state.capacity:
        result = await asyncio.to_thread(parse_name, app.state.parser, chemical_name)
    return JSONResponse(result, status_code=404 if result["status"] == "FAILURE" else 200)
