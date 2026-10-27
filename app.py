import os
import time
from collections import defaultdict
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
RESEND_FROM_EMAIL = os.environ.get("RESEND_FROM_EMAIL", "onboarding@resend.dev")
CONTACT_TO_EMAIL = os.environ.get("CONTACT_TO_EMAIL", "")

app = FastAPI(title="EscalaPro Landing")

# Nada de esto se persiste a disco: es solo para frenar spam dentro del
# tiempo que el proceso sigue vivo. Un redeploy lo vacía y no pasa nada,
# a diferencia de una base de datos no habria nada que perder.
RATE_LIMIT = 5
RATE_WINDOW_SECONDS = 600
_hits: dict[str, list[float]] = defaultdict(list)


def _is_rate_limited(ip: str) -> bool:
    now = time.time()
    recent = [t for t in _hits[ip] if now - t < RATE_WINDOW_SECONDS]
    recent.append(now)
    _hits[ip] = recent
    return len(recent) > RATE_LIMIT


class ContactoIn(BaseModel):
    nombre: str = Field(min_length=2, max_length=80)
    contacto: str = Field(min_length=5, max_length=120)
    rubro: str = Field(default="", max_length=80)
    mensaje: str = Field(min_length=5, max_length=1000)
    empresa: str = Field(default="", max_length=200)  # honeypot: un bot lo completa, una persona no lo ve


def _escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


@app.post("/api/contacto")
async def contacto(data: ContactoIn, request: Request):
    if data.empresa.strip():
        # Campo honeypot relleno: es un bot. Respondemos como si todo
        # hubiera salido bien para no darle pistas, pero no mandamos nada.
        return {"ok": True}

    ip = request.client.host if request.client else "unknown"
    if _is_rate_limited(ip):
        raise HTTPException(
            status_code=429,
            detail="Demasiadas solicitudes. Probá de nuevo en un rato, o escribinos directo por WhatsApp.",
        )

    if not RESEND_API_KEY or not CONTACT_TO_EMAIL:
        raise HTTPException(
            status_code=503,
            detail="El formulario no está disponible en este momento. Escribinos por WhatsApp.",
        )

    html_body = f"""
    <h2>Nuevo pedido de presupuesto — EscalaPro</h2>
    <p><strong>Nombre:</strong> {_escape(data.nombre)}</p>
    <p><strong>Contacto:</strong> {_escape(data.contacto)}</p>
    <p><strong>Rubro:</strong> {_escape(data.rubro) or '-'}</p>
    <p><strong>Mensaje:</strong><br>{_escape(data.mensaje).replace(chr(10), '<br>')}</p>
    """

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
                json={
                    "from": f"EscalaPro <{RESEND_FROM_EMAIL}>",
                    "to": [CONTACT_TO_EMAIL],
                    "subject": f"Nuevo pedido de presupuesto de {data.nombre}",
                    "html": html_body,
                },
            )
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="No pudimos enviar tu mensaje. Probá por WhatsApp.")

    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail="No pudimos enviar tu mensaje. Probá por WhatsApp.")

    return {"ok": True}


@app.get("/health")
async def health():
    return {"status": "ok"}


STATIC_DIR = Path(__file__).resolve().parent
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
