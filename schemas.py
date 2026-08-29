from pydantic import BaseModel, Field


class SherlockRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)


class TheHarvesterRequest(BaseModel):
    domain: str = Field(min_length=1, max_length=253)
