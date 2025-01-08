from pydantic import BaseModel

class SignUp(BaseModel):
    email: str
    password: str

class Login(BaseModel):
    email: str
    password: str