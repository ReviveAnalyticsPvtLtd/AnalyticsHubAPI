from pydantic import BaseModel

class SignUp(BaseModel):
    email: str
    password: str

class Login(BaseModel):
    email: str
    password: str

class LoginWithProvider(BaseModel):
    email: str
    provider: str
    sub: str | None = None
    id: str | None = None
    nodeId: str | None = None

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
    email: str
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
    xAxis: str | None = None
    yAxis: str | None = None
    dataSource: str
    aggregationMetric: str | None = None
    index: list[str] | None = None
    columns: list[str] | None = None
    values: list[str] | None = None

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
    label: str | None = None
    xLabels: list[str] | None = None
    yLabels: list[str] | None = None
    data: dict[str, list] | str
    layout: dict[str, int]
    generatedCode: str

class EditWidgetPosition(BaseModel):
    projectId: str
    pageId: str
    pageName: str
    widgets: list[dict] | None = None

class GetData(BaseModel):
    projectId: str
    page: str
    filters: list[dict] | None = None

class DeleteDashboardElement(BaseModel):
    projectId: str
    deletionObject: str
    id: str