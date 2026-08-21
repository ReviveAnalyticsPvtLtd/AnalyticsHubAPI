import importlib
import json
import sys
import types
import unittest


def _install_import_stubs():
    logger_module = types.ModuleType("utils.logger")
    logger_module.logger = types.SimpleNamespace(
        info=lambda *_args, **_kwargs: None,
        warning=lambda *_args, **_kwargs: None,
        error=lambda *_args, **_kwargs: None,
    )
    sys.modules["utils.logger"] = logger_module

    commons_module = types.ModuleType("api.commons")
    commons_module.client = object()
    sys.modules["api.commons"] = commons_module

    # Several sibling test modules replace `requests` with a bare stub at import
    # time, which pytest executes during collection. Drop that stub so the import
    # chain below gets the real package back.
    stubbed_requests = sys.modules.get("requests")
    if stubbed_requests is not None and not hasattr(stubbed_requests, "HTTPError"):
        for name in [key for key in sys.modules if key == "requests" or key.startswith("requests.")]:
            del sys.modules[name]

    celery_module = types.ModuleType("nubrix.triggers.celery")
    celery_module.celeryApp = types.SimpleNamespace(send_task=lambda *_args, **_kwargs: None)
    sys.modules["nubrix.triggers.celery"] = celery_module

    celery_result_module = types.ModuleType("celery.result")
    celery_result_module.AsyncResult = type("AsyncResult", (), {})
    sys.modules["celery.result"] = celery_result_module

    seaborn_module = types.ModuleType("seaborn")
    seaborn_module.load_dataset = lambda *_args, **_kwargs: []
    sys.modules["seaborn"] = seaborn_module

    # UtilityService instantiates a module-level singleton at import time, so the
    # stubs must expose whatever its __init__ reads. ImageToInsights publishes the
    # payload budget knobs the DashboardPayloadBuilder is constructed from.
    component_stubs = (
        ("nubrix.components.insightContextBuilder", "InsightContextBuilder", {}),
        (
            "nubrix.components.imageToInsights",
            "ImageToInsights",
            {"fullRowLimit": 40, "compactRowLimit": 10, "payloadTokenBudget": 12000},
        ),
        ("nubrix.components.signalEngine", "SignalEngine", {}),
        ("nubrix.components.speechToText", "SpeechToText", {}),
        (
            "nubrix.components.dashboardPayloadBuilder",
            "DashboardPayloadBuilder",
            {"__init__": lambda self, **_kwargs: None},
        ),
    )
    for module_name, class_name, attributes in component_stubs:
        module = types.ModuleType(module_name)
        setattr(module, class_name, type(class_name, (), dict(attributes)))
        sys.modules[module_name] = module


_STUB_KEYS = (
    "api.commons",
    "api.services.utilityService",
    "celery.result",
    "nubrix.components.dashboardPayloadBuilder",
    "nubrix.components.imageToInsights",
    "nubrix.components.insightContextBuilder",
    "nubrix.components.signalEngine",
    "nubrix.components.speechToText",
    "nubrix.triggers.celery",
    "seaborn",
    "utils.logger",
)


def _reload_utility_service():
    _install_import_stubs()
    sys.modules.pop("api.services.utilityService", None)
    return importlib.import_module("api.services.utilityService")


def _insights(**marker):
    """
    Schema-shaped insights payload. Only records carrying the documented keys are
    served from cache, so fixtures that stand in for real insights must have them;
    the marker keys just identify the fixture in assertions.
    """
    return {
        "diagnostic_insights": [],
        "prescriptive_actions": [],
        "missing_data": [],
        **marker,
    }


class _FakeContextBuilder:
    def __init__(self):
        self.calls = []

    def buildContext(self, projectId, pageId):
        self.calls.append({"projectId": projectId, "pageId": pageId})
        return {"chartData": [{"ref": "W1", "title": "Revenue"}], "dashboard": {"pageId": pageId}}


class _FakeSignalEngine:
    def __init__(self):
        self.calls = []

    def buildStatisticalSummary(self, chartData):
        self.calls.append(chartData)
        return {"widgetCount": len(chartData), "perWidget": {}}


class _FakePayloadBuilder:
    def __init__(self, payload="## dashboard\npage: Overview"):
        self.payload = payload
        self.calls = []

    def build(self, context):
        self.calls.append(context)
        return self.payload


class _FakeImageToInsights:
    payloadTokenBudget = 12000
    fullRowLimit = 40
    compactRowLimit = 10

    def __init__(self, generated):
        self.generated = generated
        self.calls = []

    def getInsights(self, payloadText, context=None, callbacks=None):
        self.calls.append({"payloadText": payloadText, "context": context})
        return self.generated


class _FakeStorageBucket:
    def __init__(self):
        self.uploads = []

    def from_(self, bucket):
        self.bucket = bucket
        return self

    def upload(self, path, file, file_options=None):
        self.uploads.append({"path": path, "file": file, "file_options": file_options})


class _FakeClient:
    def __init__(self):
        self.storage = _FakeStorageBucket()


class DashboardInsightsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._savedModules = {name: sys.modules.get(name) for name in _STUB_KEYS}
        cls.module = _reload_utility_service()
        cls.ImageToInsightsModel = importlib.import_module("api.models").ImageToInsightsModel

    @classmethod
    def tearDownClass(cls):
        for name, module in cls._savedModules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def _service(self, existing_records, generated=None, payload="## dashboard\npage: Overview"):
        service = object.__new__(self.module.UtilityService)
        service.insightContextBuilder = _FakeContextBuilder()
        service.signalEngine = _FakeSignalEngine()
        service.payloadBuilder = _FakePayloadBuilder(payload)
        service.imageToInsightsModule = _FakeImageToInsights(generated or {"fresh": True})
        service.client = _FakeClient()
        service._loadDashboardInsightsFile = lambda _project_id: list(existing_records)
        return service

    def _payload(self, refresh=False, pageId="page_1"):
        return self.ImageToInsightsModel(
            projectId="project_1",
            pageId=pageId,
            refresh=refresh,
        )

    def _uploaded_records(self, service):
        upload = service.client.storage.uploads[-1]
        self.assertEqual("project_1/dashboardInsights.json", upload["path"])
        self.assertEqual({"upsert": "true"}, upload["file_options"])
        return json.loads(upload["file"].decode("utf-8"))

    def _cache_key(self, service, pageId="page_1"):
        return service._computeCacheKey(pageId, service.payloadBuilder.payload)

    def test_payload_text_is_forwarded_to_the_model(self):
        service = self._service(existing_records=[])

        service.getInsightsFromImage(self._payload())

        self.assertEqual("## dashboard\npage: Overview", service.imageToInsightsModule.calls[0]["payloadText"])

    def test_first_call_generates_and_persists_a_page_scoped_record(self):
        service = self._service(existing_records=[], generated={"fresh": "insight"})

        result = service.getInsightsFromImage(self._payload())

        self.assertEqual({"fresh": "insight"}, result["insights"])
        self.assertEqual("generated", result["source"])
        self.assertFalse(result["cacheHit"])

        records = self._uploaded_records(service)
        self.assertEqual(1, len(records))
        self.assertEqual("page_1", records[0]["pageId"])
        self.assertEqual(2, records[0]["cacheVersion"])
        self.assertEqual(self._cache_key(service), records[0]["cacheKey"])

    def test_matching_cache_key_returns_cached_record_without_model_call(self):
        service = self._service(existing_records=[])
        cached = {
            "id": "insight_1",
            "pageId": "page_1",
            "cacheKey": self._cache_key(service),
            "generatedAt": "2026-06-09T00:00:00+00:00",
            "insights": _insights(cached=True),
        }
        service._loadDashboardInsightsFile = lambda _project_id: [cached]

        result = service.getInsightsFromImage(self._payload())

        self.assertEqual(_insights(cached=True), result["insights"])
        self.assertTrue(result["cacheHit"])
        self.assertEqual("insight_1", result["insightId"])
        self.assertEqual([], service.imageToInsightsModule.calls)
        self.assertEqual([], service.client.storage.uploads)

    def test_changed_dashboard_data_invalidates_the_cache(self):
        service = self._service(existing_records=[])
        stale = {
            "id": "insight_1",
            "pageId": "page_1",
            "cacheKey": service._computeCacheKey("page_1", "## dashboard\npage: Something Else"),
            "insights": _insights(stale=True),
        }
        service._loadDashboardInsightsFile = lambda _project_id: [stale]

        result = service.getInsightsFromImage(self._payload())

        self.assertEqual("generated", result["source"])
        self.assertEqual(1, len(service.imageToInsightsModule.calls))

    def test_other_page_cached_record_is_not_served(self):
        service = self._service(existing_records=[])
        otherPage = {
            "id": "insight_other",
            "pageId": "page_2",
            "cacheKey": service._computeCacheKey("page_2", service.payloadBuilder.payload),
            "insights": _insights(other=True),
        }
        service._loadDashboardInsightsFile = lambda _project_id: [otherPage]

        result = service.getInsightsFromImage(self._payload(pageId="page_1"))

        self.assertEqual("generated", result["source"])

    def test_persisting_one_page_preserves_other_pages_records(self):
        service = self._service(existing_records=[])
        otherPage = {"id": "insight_other", "pageId": "page_2", "cacheKey": "x", "insights": {"other": True}}
        service._loadDashboardInsightsFile = lambda _project_id: [otherPage]

        service.getInsightsFromImage(self._payload(pageId="page_1"))

        records = self._uploaded_records(service)
        self.assertEqual({"page_1", "page_2"}, {record["pageId"] for record in records})

    def test_refresh_bypasses_a_matching_cache_entry(self):
        service = self._service(existing_records=[])
        cached = {
            "id": "insight_1",
            "pageId": "page_1",
            "cacheKey": self._cache_key(service),
            "insights": _insights(cached=True),
        }
        service._loadDashboardInsightsFile = lambda _project_id: [cached]

        result = service.getInsightsFromImage(self._payload(refresh=True))

        self.assertEqual({"fresh": True}, result["insights"])
        self.assertEqual("generated", result["source"])
        records = self._uploaded_records(service)
        self.assertEqual(1, len(records))
        self.assertNotEqual("insight_1", records[0]["id"])

    def test_legacy_records_without_cache_key_are_ignored(self):
        service = self._service(existing_records=[])
        legacy = {
            "id": "old",
            "scope": "project",
            "pageId": None,
            "insights": _insights(old=True),
            "cacheVersion": 1,
        }
        service._loadDashboardInsightsFile = lambda _project_id: [legacy]

        result = service.getInsightsFromImage(self._payload())

        self.assertEqual("generated", result["source"])

    def test_records_with_an_unexpected_insight_shape_are_regenerated(self):
        # Records persisted while the model's content blocks leaked through the
        # parser hold {"type": "text", "text": ...} instead of the real schema.
        # Serving them would keep the broken payload alive until the data changed.
        service = self._service(existing_records=[])
        malformed = {
            "id": "insight_bad",
            "pageId": "page_1",
            "cacheKey": self._cache_key(service),
            "insights": {"type": "text", "text": "{\"diagnostic_insights\": []}", "extras": {}},
        }
        service._loadDashboardInsightsFile = lambda _project_id: [malformed]

        result = service.getInsightsFromImage(self._payload())

        self.assertEqual("generated", result["source"])
        self.assertFalse(result["cacheHit"])
        self.assertEqual(1, len(service.imageToInsightsModule.calls))


if __name__ == "__main__":
    unittest.main()
