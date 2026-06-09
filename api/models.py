from pydantic import BaseModel
from enum import Enum

class SignUp(BaseModel):
    email: str
    password: str

class Login(BaseModel):
    email: str
    password: str

class ProviderEnum(str, Enum):
    GOOGLE = "google"
    AZURE_AD = "azure-ad"

class LoginWithProvider(BaseModel):
    email: str
    provider: ProviderEnum
    sub: str
    profileImage: str | None = None

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
    workspaceId: str
    domainExpert: str

class GenerateChartInput(BaseModel):
    inputQuery: str
    projectId: str

class GenerateChartsInParallel(BaseModel):
    projectId: str
    inputQueries: list[str]

class SpeechToTextModel(BaseModel):
    b64String: str

class ImageToInsightsModel(BaseModel):
    b64String: str
    projectId: str
    pageId: str | None = None
    refresh: bool = False

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
    zipCodeColumn: str | None = None
    dataSource: str
    aggregationMetric: str | None = None
    index: list[str] | None = None
    columns: list[str] | None = None
    values: list[str] | None = None
    selectedColumns: list[str] | None = None
    mapType: str | None = None
    isFilterApplied: bool | None = None
    filters: list[dict] | None = None

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
    data: dict[str, list | dict] | list[dict] | str | None = None
    map: dict | None = None
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
    refresh: bool = False

class DeleteDashboardElement(BaseModel):
    projectId: str
    deletionObject: str
    id: str

class VerifySubscriptionRequest(BaseModel):
    razorpayOrderId: str
    razorpayPaymentId: str
    razorpaySignature: str

    class Config:
        extra = "forbid"

class CreateSubscriptionRequest(BaseModel):
    domains: list[str]
    contact: str
    billingMode: str | None = "monthly_recurring"

class AddDomainsRequest(BaseModel):
    domains: list[str]

class VerifyDomainUpgradeRequest(BaseModel):
    domains: list[str] | None = None
    razorpayOrderId: str
    razorpayPaymentId: str
    razorpaySignature: str

class RemoveDomainRequest(BaseModel):
    domains: list[str]

class CancelPendingAdditionRequest(BaseModel):
    domain: str

class CancelSubscriptionRequest(BaseModel):
    reason: str

class SubscriptionStatus(str, Enum):
    NONE = "NONE"
    TRIAL = "TRIAL"
    ACTIVE = "ACTIVE"
    CANCELLED = "CANCELLED"
    PENDING_CANCELLATION = "PENDING_CANCELLATION"
    PAUSED = "PAUSED"
    EXPIRED = "EXPIRED"

class CreateAnnualRenewalSessionRequest(BaseModel):
    invoiceId: str

class VerifyAnnualRenewalPaymentRequest(BaseModel):
    invoiceId: str
    razorpayOrderId: str
    razorpayPaymentId: str
    razorpaySignature: str

class ReplayWebhookEventRequest(BaseModel):
    eventId: str

class MarkReconciliationInvestigatedRequest(BaseModel):
    entityType: str
    entityId: str
    note: str

class RefundRequest(BaseModel):
    paymentId: str
    amount: int | None = None

class SaveQuery(BaseModel):
    projectId: str
    query: str

class DeleteQuery(BaseModel):
    projectId: str
    queryId: str

class RenameProject(BaseModel):
    projectId: str
    newProjectName: str

class UpdateDashboardInsightStatus(BaseModel):
    projectId: str
    insightId: str
    status: str
