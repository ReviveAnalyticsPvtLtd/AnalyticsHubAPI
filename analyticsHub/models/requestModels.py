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

class SpeechToTextModel(BaseModel):
    b64String: str

class DeleteTable(BaseModel):
    projectId: str
    tableName: str

class EditMetadata(BaseModel):
    projectId: str
    tableName: str
    tableDescription: str | None = None
    columnName: str | None = None
    columnDescription: str | None = None

class PanelChartDetails(BaseModel):
    projectId: str
    chartType: str
    xAxis: str
    yAxis: str
    dataSource: str
    aggregationMetric: str

class CreateDataBlend(BaseModel):
    projectId: str
    blendOn: list[str]
    blendName: str
    tables: list[str]
    joinTypes: list[str]

class GetFieldsFromSources(BaseModel):
    projectId: str
    tableName: str

class LoadMySQLorPostgreSQL(BaseModel):
    projectId: str
    user: str
    password: str
    host: str
    port: int
    db: str
    table: str

class LoadMongoDB(BaseModel):
    projectId: str
    connectionString: str
    db: str
    collection: str

class CreatePage(BaseModel):
    projectId: str
    pageName: str

class ExportToDashboard(BaseModel):
    projectId: str
    page: str
    chartType: str
    title: str
    xLabels: list[str] | None = None
    yLabels: list[str] | None = None
    data: dict[str, list] | str
    layout: dict[str, int]

class EditWidgetPosition(BaseModel):
    projectId: str
    pageId: str
    widgets: list[dict]