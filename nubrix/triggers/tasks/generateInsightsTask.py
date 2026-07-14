from utils.logger import logger

class GenerateInsightsTask:
    def execute(self, projectId: str, preserveCharted: bool, userId: str) -> dict:
        logger.info(f"GenerateInsightsTask started for project {projectId}, user {userId}")
        try:
            from api.services.managementService import managementService
            jsonData = managementService.generateInsightsForProject(projectId=projectId, preserveCharted=preserveCharted, userId=userId)
            logger.info(f"GenerateInsightsTask completed for project {projectId}")
            response = {"status": "SUCCESS"}
            response.update(jsonData)
            return response
        except Exception as e:
            logger.error(f"GenerateInsightsTask failed for project {projectId}: {e}")
            return {"status": "FAILURE", "error": str(e)}
