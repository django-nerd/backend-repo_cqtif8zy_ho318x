import os
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, HTTPException, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, EmailStr
import asyncio

from database import db, create_document, get_documents
from schemas import User as UserSchema, Resource as ResourceSchema, Notification as NotificationSchema
from bson import ObjectId

app = FastAPI(title="CSE Resource Sharing Platform API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------------
# Utilities
# ----------------------

def oid(id_str: str) -> ObjectId:
    try:
        return ObjectId(id_str)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid id")


def clean(doc: Dict[str, Any]):
    if not doc:
        return doc
    doc["id"] = str(doc.pop("_id"))
    # Convert datetimes to isoformat
    for k, v in list(doc.items()):
        if isinstance(v, datetime):
            doc[k] = v.isoformat()
    return doc


# ----------------------
# Minimal auth (email + role) for demo purposes
# ----------------------
class LoginRequest(BaseModel):
    name: str
    email: EmailStr
    role: str  # "student" | "teacher" | "admin"
    semester: Optional[int] = None


@app.post("/auth/login")
def login(payload: LoginRequest):
    # Upsert user record
    if db is None:
        raise HTTPException(status_code=503, detail="Database not configured")
    users = db["user"]
    existing = users.find_one({"email": payload.email})
    doc = {
        "name": payload.name,
        "email": payload.email,
        "role": payload.role,
        "semester": payload.semester,
        "department": "CSE",
        "is_active": True,
        "updated_at": datetime.now(timezone.utc),
    }
    if existing:
        users.update_one({"_id": existing["_id"]}, {"$set": doc})
        existing.update(doc)
        return clean(existing)
    else:
        created_id = create_document("user", doc)
        new_doc = users.find_one({"_id": ObjectId(created_id)})
        return clean(new_doc)


# ----------------------
# Resources CRUD + Moderation
# ----------------------
class CreateResourceRequest(BaseModel):
    title: str
    description: Optional[str] = None
    semester: int
    subject: str
    tags: List[str] = []
    file_url: Optional[str] = None
    content_url: Optional[str] = None
    uploaded_by: EmailStr
    uploader_name: Optional[str] = None


@app.post("/resources")
async def create_resource(payload: CreateResourceRequest):
    data = ResourceSchema(
        title=payload.title,
        description=payload.description,
        semester=payload.semester,
        subject=payload.subject,
        tags=payload.tags or [],
        file_url=payload.file_url,
        content_url=payload.content_url,
        uploaded_by=payload.uploaded_by,
        uploader_name=payload.uploader_name,
        status="pending",
    ).model_dump()

    rid = create_document("resource", data)

    # Emit notification (resource_created)
    notif = NotificationSchema(
        type="resource_created",
        message=f"New resource pending: {data['title']}",
        resource_id=rid,
        created_by=data["uploaded_by"],
        semester=data["semester"],
        subject=data["subject"],
    ).model_dump()
    create_document("notification", notif)
    await broadcaster.broadcast({"event": "resource_created", "resource_id": rid, "title": data["title"]})

    doc = db["resource"].find_one({"_id": ObjectId(rid)})
    return clean(doc)


@app.get("/resources")
def list_resources(
    semester: Optional[int] = Query(None),
    subject: Optional[str] = Query(None),
    status: str = Query("approved"),
    uploaded_by: Optional[str] = Query(None),
    limit: Optional[int] = Query(100),
):
    if db is None:
        raise HTTPException(status_code=503, detail="Database not configured")
    q: Dict[str, Any] = {}
    if semester is not None:
        q["semester"] = semester
    if subject:
        q["subject"] = subject
    if status:
        q["status"] = status
    if uploaded_by:
        q["uploaded_by"] = uploaded_by
    docs = get_documents("resource", q, limit)
    return [clean(d) for d in docs]


@app.get("/resources/pending")
def list_pending(semester: Optional[int] = None, subject: Optional[str] = None):
    q: Dict[str, Any] = {"status": "pending"}
    if semester is not None:
        q["semester"] = semester
    if subject:
        q["subject"] = subject
    docs = get_documents("resource", q, 200)
    return [clean(d) for d in docs]


class ApproveRequest(BaseModel):
    approved_by: EmailStr


@app.post("/resources/{resource_id}/approve")
async def approve_resource(resource_id: str, payload: ApproveRequest):
    rcol = db["resource"]
    doc = rcol.find_one({"_id": oid(resource_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Resource not found")
    if doc.get("status") == "approved":
        return clean(doc)

    rcol.update_one(
        {"_id": doc["_id"]},
        {
            "$set": {
                "status": "approved",
                "approved_by": payload.approved_by,
                "approved_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }
        },
    )
    updated = rcol.find_one({"_id": doc["_id"]})

    # Notification
    notif = NotificationSchema(
        type="resource_approved",
        message=f"Resource approved: {updated['title']}",
        resource_id=str(updated["_id"]),
        created_by=payload.approved_by,
        semester=updated.get("semester"),
        subject=updated.get("subject"),
    ).model_dump()
    create_document("notification", notif)
    await broadcaster.broadcast({"event": "resource_approved", "resource_id": str(updated["_id"])})

    return clean(updated)


# ----------------------
# Server-Sent Events broadcaster for realtime updates
# ----------------------
class Broadcaster:
    def __init__(self):
        self.subscribers: List[asyncio.Queue] = []

    async def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self.subscribers.append(q)
        # On subscribe, send a hello event
        await q.put({"event": "connected"})
        return q

    def unsubscribe(self, q: asyncio.Queue):
        try:
            self.subscribers.remove(q)
        except ValueError:
            pass

    async def broadcast(self, message: Dict[str, Any]):
        for q in list(self.subscribers):
            try:
                await q.put(message)
            except Exception:
                pass


broadcaster = Broadcaster()


@app.get("/events")
async def events():
    async def event_generator():
        q = await broadcaster.subscribe()
        try:
            while True:
                msg = await q.get()
                yield f"data: {msg}\n\n"
        finally:
            broadcaster.unsubscribe(q)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ----------------------
# Meta & health
# ----------------------
@app.get("/")
def read_root():
    return {"message": "CSE Resource Sharing Platform API"}


@app.get("/api/hello")
def hello():
    return {"message": "Hello from the backend API!"}


@app.get("/schema")
def get_schema():
    """Expose available Pydantic models (names only) for tooling."""
    return {
        "models": [
            {"name": "user"},
            {"name": "resource"},
            {"name": "notification"},
        ]
    }


@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": [],
    }
    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
            response["database_name"] = "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set"
            response["connection_status"] = "Connected"
            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️  Connected but Error: {str(e)[:50]}"
        else:
            response["database"] = "⚠️  Available but not initialized"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"
    return response


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
