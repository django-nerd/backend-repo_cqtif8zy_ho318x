"""
Database Schemas for CSE Resource Sharing Platform

Define MongoDB collection schemas here using Pydantic models.
Each Pydantic model maps to a collection whose name is the lowercase of the class name.

Example: class User -> "user" collection
"""

from pydantic import BaseModel, Field, EmailStr
from typing import Optional, List, Literal
from datetime import datetime

Role = Literal["student", "teacher", "admin"]

class User(BaseModel):
    """Users collection schema"""
    name: str = Field(..., description="Full name")
    email: EmailStr = Field(..., description="Email address")
    role: Role = Field("student", description="User role")
    semester: Optional[int] = Field(None, ge=1, le=8, description="Applicable for students (1-8)")
    department: Optional[str] = Field("CSE", description="Department name")
    is_active: bool = Field(True, description="Whether user is active")

class Resource(BaseModel):
    """Academic resources uploaded by users"""
    title: str = Field(..., description="Resource title")
    description: Optional[str] = Field(None, description="Short description")
    semester: int = Field(..., ge=1, le=8, description="Semester number (1-8)")
    subject: str = Field(..., description="Subject name")
    tags: List[str] = Field(default_factory=list, description="List of tags")
    file_url: Optional[str] = Field(None, description="URL to the resource file (drive link or hosted URL)")
    content_url: Optional[str] = Field(None, description="Alternate URL (e.g., slides, code repo)")
    uploaded_by: EmailStr = Field(..., description="Email of uploader")
    uploader_name: Optional[str] = Field(None, description="Name of uploader")
    status: Literal["pending", "approved", "rejected"] = Field("pending", description="Moderation status")
    approved_by: Optional[EmailStr] = Field(None, description="Moderator email")
    approved_at: Optional[datetime] = Field(None, description="Approval timestamp")

class Notification(BaseModel):
    """Realtime notifications (also persisted for feed if desired)"""
    type: Literal["resource_created", "resource_approved"] = Field(...)
    message: str
    resource_id: Optional[str] = None
    created_by: Optional[EmailStr] = None
    semester: Optional[int] = None
    subject: Optional[str] = None

# Additional reference schema for subjects if needed later
class Subject(BaseModel):
    name: str
    semester: int = Field(..., ge=1, le=8)
    code: Optional[str] = None
