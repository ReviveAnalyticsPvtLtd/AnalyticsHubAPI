from utils.logger import logger

class GenerateMetadataTask:
    def execute(self, projectId: str, userId: str) -> dict:
        logger.info(f"GenerateMetadataTask started for project {projectId}, user {userId}")
        try:
            from api.services.managementService import managementService
            jsonData = managementService.generateMetadata(projectId=projectId, userId=userId)
            logger.info(f"GenerateMetadataTask completed for project {projectId}")
            return {"status": "SUCCESS", "metadata": jsonData}
        except Exception as e:
            logger.error(f"GenerateMetadataTask failed for project {projectId}: {e}")
            return {"status": "FAILURE", "error": str(e)}
