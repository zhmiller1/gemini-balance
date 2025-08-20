"""
队列装饰器
为API调用函数自动添加队列处理功能
"""

import functools
from typing import Callable, TypeVar, Any
from app.service.queue.request_queue import get_request_queue, RequestPriority
from app.log.logger import get_api_client_logger

logger = get_api_client_logger()

T = TypeVar('T')

def queued_request(priority: RequestPriority = RequestPriority.NORMAL):
    """
    队列装饰器，将函数调用自动加入请求队列
    
    Args:
        priority: 请求优先级
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # 获取队列实例
            queue = await get_request_queue()
            
            # 通过队列执行请求
            logger.debug(f"Queueing request for {func.__name__} with priority {priority.name}")
            return await queue.enqueue(func, *args, priority=priority, **kwargs)
        
        return wrapper
    return decorator

def high_priority_request(func: Callable[..., T]) -> Callable[..., T]:
    """高优先级请求装饰器"""
    return queued_request(RequestPriority.HIGH)(func)

def low_priority_request(func: Callable[..., T]) -> Callable[..., T]:
    """低优先级请求装饰器"""
    return queued_request(RequestPriority.LOW)(func)
