from fastapi import UploadFile, File, Form
from pydantic import BaseModel
from typing import Annotated

class SignUp(BaseModel):
    email: str
    password: str

class Login(BaseModel):
    email: str
    password: str

class UploadData(BaseModel):
    projectId: Annotated[str, Form()]
    projectName: Annotated[str, Form()]
    dataFile: Annotated[UploadFile, File()]