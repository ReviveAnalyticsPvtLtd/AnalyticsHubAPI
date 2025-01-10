from fastapi import UploadFile, File
from pydantic import BaseModel
from typing import Annotated

class SignUp(BaseModel):
    email: str
    password: str

class Login(BaseModel):
    email: str
    password: str

class UploadData(BaseModel):
    projectId: str
    projectName: str
    dataFile: Annotated[UploadFile, File()]