from pydantic import BaseModel, Field


class ScanResult(BaseModel):
    image_name: str
    high_count: int
    critical_count: int


class SherlockRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
