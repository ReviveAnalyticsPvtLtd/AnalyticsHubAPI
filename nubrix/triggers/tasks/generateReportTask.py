from utils.logger import logger

class GenerateReportTask:
    def execute(self, projectId: str) -> dict:
        logger.info(f"GenerateReportTask started for project: {projectId}")
        try:
            from api.services.managementService import managementService
            reports = managementService.generateReport(projectId=projectId)
            logger.info(f"GenerateReportTask completed for project: {projectId}")
            return {"status": "SUCCESS", "reportHtmlContent": reports}
        except Exception as e:
            logger.error(f"GenerateReportTask failed for project {projectId}: {e}")
            return {"status": "FAILURE", "error": str(e)}
