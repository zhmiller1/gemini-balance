"""
请求队列管理器
用于控制API请求的并发数量和请求间隔，防止触发Google Gemini API的突发请求限制
"""

import asyncio
import time
from typing import Any, Callable, Optional, Dict, TypeVar, Coroutine
from dataclasses import dataclass
from enum import Enum
from app.log.logger import get_api_client_logger

logger = get_api_client_logger()

T = TypeVar('T')

class RequestPriority(Enum):
    LOW = 1
    NORMAL = 2
    HIGH = 3

@dataclass
class QueuedRequest:
    """队列中的请求"""
    id: str
    func: Callable[..., Coroutine[Any, Any, T]]
    args: tuple
    kwargs: dict
    priority: RequestPriority
    created_at: float
    future: asyncio.Future

    def __lt__(self, other):
        # 优先级高的先执行，同优先级按创建时间排序
        if self.priority != other.priority:
            return self.priority.value > other.priority.value
        return self.created_at < other.created_at

class RequestQueue:
    """请求队列管理器"""
    
    def __init__(
        self,
        max_concurrent: int = 1,  # 最大并发数
        min_interval: float = 2.0,  # 最小请求间隔（秒）
        max_queue_size: int = 1000,  # 最大队列长度
        timeout: float = 300.0  # 请求超时时间（秒）
    ):
        self.max_concurrent = max_concurrent
        self.min_interval = min_interval
        self.max_queue_size = max_queue_size
        self.timeout = timeout
        
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self._active_requests = 0
        self._last_request_time = 0.0
        self._workers: list[asyncio.Task] = []
        self._shutdown = False
        self._request_counter = 0
        
        logger.info(
            f"RequestQueue initialized - max_concurrent: {max_concurrent}, "
            f"min_interval: {min_interval}s, max_queue_size: {max_queue_size}"
        )

    async def start(self):
        """启动队列工作器"""
        if self._workers:
            return
            
        logger.info("Starting request queue workers...")
        for i in range(self.max_concurrent):
            worker = asyncio.create_task(self._worker(f"worker-{i}"))
            self._workers.append(worker)
        
        logger.info(f"Started {len(self._workers)} queue workers")

    async def stop(self):
        """停止队列工作器"""
        logger.info("Stopping request queue...")
        self._shutdown = True
        
        # 等待所有工作器完成
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
            self._workers.clear()
        
        logger.info("Request queue stopped")

    async def enqueue(
        self,
        func: Callable[..., Coroutine[Any, Any, T]],
        *args,
        priority: RequestPriority = RequestPriority.NORMAL,
        **kwargs
    ) -> T:
        """将请求加入队列"""
        if self._queue.qsize() >= self.max_queue_size:
            raise RuntimeError(f"Queue is full (max size: {self.max_queue_size})")
        
        self._request_counter += 1
        request_id = f"req-{self._request_counter}"
        
        future = asyncio.Future()
        request = QueuedRequest(
            id=request_id,
            func=func,
            args=args,
            kwargs=kwargs,
            priority=priority,
            created_at=time.time(),
            future=future
        )
        
        await self._queue.put(request)
        logger.debug(f"Enqueued request {request_id} with priority {priority.name}")
        
        try:
            # 等待请求完成
            return await asyncio.wait_for(future, timeout=self.timeout)
        except asyncio.TimeoutError:
            logger.error(f"Request {request_id} timed out after {self.timeout}s")
            raise
        except Exception as e:
            logger.error(f"Request {request_id} failed: {e}")
            raise

    async def _worker(self, worker_name: str):
        """队列工作器"""
        logger.info(f"Queue worker {worker_name} started")
        
        while not self._shutdown:
            try:
                # 等待队列中的请求
                request = await asyncio.wait_for(
                    self._queue.get(), 
                    timeout=1.0
                )
                
                await self._process_request(worker_name, request)
                
            except asyncio.TimeoutError:
                # 正常的超时，继续循环
                continue
            except Exception as e:
                logger.error(f"Worker {worker_name} error: {e}")
                await asyncio.sleep(0.1)
        
        logger.info(f"Queue worker {worker_name} stopped")

    async def _process_request(self, worker_name: str, request: QueuedRequest):
        """处理单个请求"""
        start_time = time.time()
        
        try:
            # 确保请求间隔
            await self._ensure_interval()
            
            logger.debug(f"Worker {worker_name} processing request {request.id}")
            
            # 执行请求
            self._active_requests += 1
            result = await request.func(*request.args, **request.kwargs)
            
            # 设置结果
            if not request.future.done():
                request.future.set_result(result)
            
            processing_time = time.time() - start_time
            logger.debug(
                f"Request {request.id} completed by {worker_name} "
                f"in {processing_time:.3f}s"
            )
            
        except Exception as e:
            # 设置异常
            if not request.future.done():
                request.future.set_exception(e)
            
            processing_time = time.time() - start_time
            logger.error(
                f"Request {request.id} failed in {worker_name} "
                f"after {processing_time:.3f}s: {e}"
            )
        finally:
            self._active_requests -= 1
            self._last_request_time = time.time()

    async def _ensure_interval(self):
        """确保请求间隔"""
        if self._last_request_time > 0:
            elapsed = time.time() - self._last_request_time
            if elapsed < self.min_interval:
                sleep_time = self.min_interval - elapsed
                logger.debug(f"Enforcing {sleep_time:.3f}s interval between requests")
                await asyncio.sleep(sleep_time)

    def get_stats(self) -> Dict[str, Any]:
        """获取队列统计信息"""
        return {
            "queue_size": self._queue.qsize(),
            "active_requests": self._active_requests,
            "max_concurrent": self.max_concurrent,
            "min_interval": self.min_interval,
            "workers_count": len(self._workers),
            "is_running": len(self._workers) > 0 and not self._shutdown
        }

# 全局队列实例
_global_queue: Optional[RequestQueue] = None

async def get_request_queue() -> RequestQueue:
    """获取全局请求队列实例"""
    global _global_queue
    if _global_queue is None:
        _global_queue = RequestQueue(
            max_concurrent=1,  # 同时只处理1个请求
            min_interval=2.0,  # 最小间隔2秒
            max_queue_size=100,
            timeout=300.0
        )
        await _global_queue.start()
    return _global_queue

async def shutdown_request_queue():
    """关闭全局请求队列"""
    global _global_queue
    if _global_queue:
        await _global_queue.stop()
        _global_queue = None
