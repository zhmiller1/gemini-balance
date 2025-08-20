"""
请求队列状态监控路由
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette import status
from app.service.queue.request_queue import get_request_queue
from app.core.security import verify_auth_token
from app.log.logger import get_api_client_logger

router = APIRouter(prefix="/api/queue")
logger = get_api_client_logger()

async def verify_token(request: Request):
    auth_token = request.cookies.get("auth_token")
    if not auth_token or not verify_auth_token(auth_token):
        logger.warning("Unauthorized access attempt to queue API")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

@router.get("/status")
async def get_queue_status(
    _=Depends(verify_token)
):
    """获取请求队列状态"""
    try:
        queue = await get_request_queue()
        stats = queue.get_stats()
        
        return {
            "status": "success",
            "data": {
                "queue_size": stats["queue_size"],
                "active_requests": stats["active_requests"],
                "max_concurrent": stats["max_concurrent"],
                "min_interval": stats["min_interval"],
                "workers_count": stats["workers_count"],
                "is_running": stats["is_running"]
            }
        }
    except Exception as e:
        logger.error(f"Failed to get queue status: {e}")
        raise HTTPException(status_code=500, detail="Failed to get queue status")

@router.post("/config")
async def update_queue_config(
    max_concurrent: int = None,
    min_interval: float = None,
    _=Depends(verify_token)
):
    """更新队列配置（需要重启生效）"""
    try:
        config_updates = {}
        if max_concurrent is not None:
            if max_concurrent < 1 or max_concurrent > 10:
                raise HTTPException(status_code=400, detail="max_concurrent must be between 1 and 10")
            config_updates["max_concurrent"] = max_concurrent
        
        if min_interval is not None:
            if min_interval < 0.1 or min_interval > 5.0:
                raise HTTPException(status_code=400, detail="min_interval must be between 0.1 and 5.0 seconds")
            config_updates["min_interval"] = min_interval
        
        if not config_updates:
            raise HTTPException(status_code=400, detail="No valid configuration provided")
        
        # 这里只是返回配置更新信息，实际更新需要重启应用
        return {
            "status": "success",
            "message": "Queue configuration received. Restart application to apply changes.",
            "config_updates": config_updates
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update queue config: {e}")
        raise HTTPException(status_code=500, detail="Failed to update queue config")
