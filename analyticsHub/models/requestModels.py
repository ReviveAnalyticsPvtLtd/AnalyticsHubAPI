from pydantic import BaseModel

class SignUp(BaseModel):
    email: str
    password: str

class Login(BaseModel):
    email: str
    password: str

class LoginWithProvider(BaseModel):
    email: str
    sub: str | None = None
    id: str | None = None
    nodeId: str | None = None
    provider: str

class OnboardingDetails(BaseModel):
    usage: str
    fullName: str
    email: str
    role: str
    companyName: str
    industryType: str
    companySize: str
    country: str
    goals: str
    source: str

class NewCredentials(BaseModel):
    newPassword: str

class UpdateProjectState(BaseModel):
    projectId: str
    action: str

class CreateProject(BaseModel):
    projectName: str
    projectDescription: str | None = None

class GenerateChartInput(BaseModel):
    inputQuery: str
    projectId: str